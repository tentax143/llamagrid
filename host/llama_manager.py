from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from host.config import HostConfig
    from host.coordinator import Coordinator

log = logging.getLogger(__name__)

_TPS_RE = re.compile(r"eval time\s*=.*?(\d+\.\d+)\s*tokens per second", re.IGNORECASE)
_TPS_RE2 = re.compile(r"(\d+\.?\d*)\s*t/s", re.IGNORECASE)


class LlamaManager:
    def __init__(self, cfg: "HostConfig", coord: "Coordinator"):
        self.cfg = cfg
        self.coord = coord
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._tokens_per_sec: float = 0.0
        self._restart_timer: threading.Timer | None = None
        self._running = False

        coord.set_topology_change_callback(self._on_topology_change)

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        self._running = True
        self._launch()

    def stop(self) -> None:
        self._running = False
        self._cancel_restart_timer()
        self._kill()

    def restart(self) -> None:
        self._kill()
        time.sleep(1)
        if self._running:
            self._launch()

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def tokens_per_sec(self) -> float:
        return self._tokens_per_sec

    def update_model(self, model_path: str) -> None:
        self.cfg.model_path = model_path
        from host.config import save_config
        save_config(self.cfg)
        log.info("Model changed to %s — restarting llama-server", os.path.basename(model_path))
        self.restart()

    # ------------------------------------------------------------------ internal

    def _on_topology_change(self) -> None:
        """Called by coordinator when peers join/leave. Debounced 5 s."""
        self._cancel_restart_timer()
        self._restart_timer = threading.Timer(5.0, self._debounced_restart)
        self._restart_timer.daemon = True
        self._restart_timer.start()
        log.debug("Topology changed — scheduled llama-server restart in 5 s")

    def _debounced_restart(self) -> None:
        if self._running:
            log.info("Restarting llama-server due to peer topology change")
            self.restart()

    def _cancel_restart_timer(self) -> None:
        if self._restart_timer is not None:
            self._restart_timer.cancel()
            self._restart_timer = None

    def _build_command(self) -> list[str]:
        peers = self.coord.online_peers_for_rpc()
        exe = self.cfg.llama_server_exe

        cmd = [
            exe,
            "--model", self.cfg.model_path,
            "--ctx-size", str(self.cfg.llama_server_args.ctx_size),
            "--n-gpu-layers", str(self.cfg.llama_server_args.n_gpu_layers),
            "--threads", str(self.cfg.llama_server_args.threads),
            "--host", "127.0.0.1",
            "--port", str(self.cfg.llama_server_port),
        ]

        if peers:
            rpc_arg = ",".join(f"{p.ip}:{p.rpc_port}" for p in peers)
            cmd += ["--rpc", rpc_arg]
            log.info("Starting llama-server with %d RPC peer(s): %s", len(peers), rpc_arg)
        else:
            log.info("Starting llama-server with no RPC peers (local only)")

        return cmd

    def _launch(self) -> None:
        if not os.path.isfile(self.cfg.llama_server_exe):
            log.error("llama-server.exe not found at %s", self.cfg.llama_server_exe)
            return

        if not os.path.isfile(self.cfg.model_path):
            log.error("Model file not found at %s", self.cfg.model_path)
            return

        cmd = self._build_command()
        log.info("Launching: %s", " ".join(cmd))
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            t = threading.Thread(target=self._read_stdout, daemon=True, name="llama-stdout")
            t.start()
        except Exception as e:
            log.error("Failed to launch llama-server: %s", e)

    def _kill(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            log.info("Terminating llama-server (pid=%d)", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    log.debug("[llama-server] %s", line)
                m = _TPS_RE.search(line) or _TPS_RE2.search(line)
                if m:
                    try:
                        self._tokens_per_sec = float(m.group(1))
                    except ValueError:
                        pass
        except Exception:
            pass
        finally:
            if proc.poll() is not None and self._running:
                log.warning("llama-server exited (code=%s) — will restart in 5 s", proc.returncode)
                time.sleep(5)
                if self._running:
                    self._launch()
