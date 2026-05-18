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

_DONE_RE      = re.compile(r"listening at|server is listening", re.IGNORECASE)

# T/s: new format "tg = 3.15 t/s" (print_timing lines) and old "eval time" format
_TPS_RE       = re.compile(r"tg\s*=\s*([\d.]+)\s*t/s", re.IGNORECASE)
_TPS_RE_OLD   = re.compile(r"eval time\s*=.*?([\d.]+)\s*tokens per second", re.IGNORECASE)

# Device info: new format (llama.cpp b4000+)
# "I   - RPC0    : 172.16.71.117:50052 (8187 MiB, 7095 MiB free)"
_RPC_DEVICE_RE = re.compile(
    r"-\s+RPC(\d+)\s*:\s*([\d.]+):\d+\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)",
    re.IGNORECASE,
)
_CPU_DEVICE_RE = re.compile(
    r"-\s+(CPU[^\s:]*)\s*:.*?\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)",
    re.IGNORECASE,
)

# Legacy format (llama.cpp pre-b4000) kept as fallback
_BUF_RE_OLD   = re.compile(r"llm_load_tensors:\s+(\S+)\s+buffer size\s*=\s*([\d.]+)\s*MiB", re.IGNORECASE)
_OFFLOAD_RE   = re.compile(r"llm_load_tensors:\s+offloading\s+(\d+)\s+repeating\s+layers", re.IGNORECASE)
_LAYERS_RE    = re.compile(r"llm_load_tensors:\s+total:\s+(\d+)\s+layers", re.IGNORECASE)


class LlamaManager:
    def __init__(self, cfg: "HostConfig", coord: "Coordinator"):
        self.cfg = cfg
        self.coord = coord
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._tokens_per_sec: float = 0.0
        self._restart_timer: threading.Timer | None = None
        self._running = False

        # Loading progress state
        self._loading: bool = False
        self._load_start_time: float = 0.0
        self._last_load_duration_sec: float = 0.0
        self._allocations: list[dict] = []   # [{name, size_mb, peer_ip?}]
        self._rpc_peer_map: list[str] = []   # index → peer IP
        self._total_layers: int = 0
        self._offloaded_layers: int = 0
        self._startup_log: list[str] = []    # raw stdout lines from last launch

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

    def loading_info(self) -> dict:
        elapsed = time.time() - self._load_start_time if self._load_start_time else 0.0
        est = self._last_load_duration_sec
        if not est:
            # Rough estimate: model size in GB × 8 seconds
            try:
                size_bytes = os.path.getsize(self.cfg.model_path)
                est = (size_bytes / (1024 ** 3)) * 8
            except Exception:
                est = 0.0
        progress_pct = min(99.0, (elapsed / est * 100)) if est > 0 else None
        model_name = os.path.basename(self.cfg.model_path)
        try:
            model_size_gb = round(os.path.getsize(self.cfg.model_path) / (1024 ** 3), 1)
        except Exception:
            model_size_gb = 0.0
        return {
            "loading": self._loading,
            "elapsed_sec": round(elapsed, 1),
            "estimated_total_sec": round(est, 1) if est else None,
            "progress_pct": round(progress_pct, 1) if progress_pct is not None else None,
            "model_name": model_name,
            "model_size_gb": model_size_gb,
            "allocations": list(self._allocations),
            "total_layers": self._total_layers,
            "offloaded_layers": self._offloaded_layers,
        }

    def record_tps(self, tps: float) -> None:
        self._tokens_per_sec = tps

    def rpc_status(self) -> dict:
        return {
            "rpc_peers": list(self._rpc_peer_map),
            "allocations": list(self._allocations),
            "alloc_type": "buffer" if self._total_layers > 0 else "device_free_vram",
        }

    def startup_log(self) -> list[str]:
        return list(self._startup_log)

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
            "--threads", str(self.cfg.llama_server_args.threads),
            "--host", "127.0.0.1",
            "--port", str(self.cfg.llama_server_port),
        ]

        if peers:
            # With RPC peers, omit --n-gpu-layers so llama-server's built-in -fit
            # mode can auto-distribute layers across all GPU backends. Passing an
            # explicit value causes -fit to abort its optimisation pass.
            rpc_arg = ",".join(f"{p.ip}:{p.rpc_port}" for p in peers)
            cmd += ["--rpc", rpc_arg]
            self._rpc_peer_map = [p.ip for p in peers]
            log.info("Starting llama-server with %d RPC peer(s): %s", len(peers), rpc_arg)
        else:
            # No peers — pass n-gpu-layers explicitly for local GPU inference
            cmd += ["--n-gpu-layers", str(self.cfg.llama_server_args.n_gpu_layers)]
            self._rpc_peer_map = []
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
            self._loading = True
            self._load_start_time = time.time()
            self._allocations = []
            self._total_layers = 0
            self._offloaded_layers = 0
            self._startup_log = []
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
                if not line:
                    continue
                log.debug("[llama-server] %s", line)

                # Capture raw startup log (first 600 lines, until model is ready)
                if len(self._startup_log) < 600:
                    self._startup_log.append(line)

                # T/s: new format "tg = 3.15 t/s" or legacy "eval time" format
                m = _TPS_RE.search(line) or _TPS_RE_OLD.search(line)
                if m:
                    try:
                        self._tokens_per_sec = float(m.group(1))
                    except ValueError:
                        pass

                # New device_info format: "- RPC0 : 1.2.3.4:50052 (8187 MiB, 7095 MiB free)"
                m = _RPC_DEVICE_RE.search(line)
                if m:
                    idx = int(m.group(1))
                    peer_ip = m.group(2)
                    free_mb = float(m.group(4))
                    name = f"RPC{idx}"
                    if not any(a.get("name") == name for a in self._allocations):
                        self._allocations.append({"name": name, "size_mb": free_mb, "peer_ip": peer_ip})

                # Legacy buffer allocation format (pre-b4000)
                m = _BUF_RE_OLD.search(line)
                if m:
                    name = m.group(1)
                    size_mb = float(m.group(2))
                    entry: dict = {"name": name, "size_mb": size_mb}
                    if name.upper().startswith("RPC"):
                        try:
                            idx = int(name[3:])
                            if idx < len(self._rpc_peer_map):
                                entry["peer_ip"] = self._rpc_peer_map[idx]
                        except ValueError:
                            pass
                    if not any(a.get("name") == name for a in self._allocations):
                        self._allocations.append(entry)

                # Layer counts (legacy format)
                m = _LAYERS_RE.search(line)
                if m:
                    self._total_layers = int(m.group(1))

                m = _OFFLOAD_RE.search(line)
                if m:
                    self._offloaded_layers = int(m.group(1))

                # loading complete
                if _DONE_RE.search(line):
                    duration = time.time() - self._load_start_time
                    self._last_load_duration_sec = duration
                    self._loading = False
                    log.info("llama-server ready — model loaded in %.1f s", duration)

        except Exception:
            pass
        finally:
            self._loading = False
            if proc.poll() is not None and self._running:
                log.warning("llama-server exited (code=%s) — will restart in 5 s", proc.returncode)
                time.sleep(5)
                if self._running:
                    self._launch()
