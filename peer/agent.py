from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import traceback
from typing import TYPE_CHECKING

import requests

from peer.config import PeerConfig, save_config
from peer.host_discovery import HostEndpoint, discover_host
from shared.protocol import HeartbeatRequest, HeartbeatResponse, RegisterRequest
from shared.sysreport import build_system_report
from shared.versioning import AGENT_VERSION

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

RPC_LOG = r"C:\LlamaGrid\logs\rpc.err.log"
STATUS_FILE = r"C:\LlamaGrid\peer_status.json"
_SYSREPORT_REBUILD_SEC = 30

_CUDA_ERRORS = [
    "out of memory",
    "CUDA error",
    "driver mismatch",
    "cuInit failed",
    "failed to allocate",
    "ggml_cuda_init: failed",
]


class Agent:
    def __init__(self, cfg: PeerConfig):
        self.cfg = cfg
        self._host: HostEndpoint | None = None
        self._mdns_info = None
        self._running = True
        self._registered = False
        self._cached_report = None
        self._last_report_time: float = 0.0
        self._heartbeat_count: int = 0

    def run(self) -> None:
        log.info("LlamaGrid peer agent %s starting (peer_id=%s)", AGENT_VERSION, self.cfg.peer_id)
        self._start_mdns()
        self._register_with_retry()
        self._start_log_watcher()

        while self._running:
            try:
                self._heartbeat_tick()
            except Exception:
                log.warning("Heartbeat tick raised unexpectedly:\n%s", traceback.format_exc())
            time.sleep(self.cfg.heartbeat_interval_sec)

    def stop(self) -> None:
        self._running = False
        if self._mdns_info:
            try:
                from shared import mdns
                mdns.unregister(self._mdns_info)
                mdns.shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------ mDNS

    def _start_mdns(self) -> None:
        try:
            from shared import mdns
            from shared.versioning import LLAMA_CPP_PINNED_TAG
            sys_report = build_system_report(self.cfg.resolved_rpc_path)
            gpu_name = sys_report.gpus[0].name.replace(" ", "_")[:20] if sys_report.gpus else "none"
            vram = str(sys_report.gpus[0].vram_total_mb) if sys_report.gpus else "0"
            self._mdns_info = mdns.advertise_peer(
                self.cfg.peer_id,
                self.cfg.rpc_port,
                {
                    "id": self.cfg.peer_id,
                    "ver": AGENT_VERSION,
                    "rpc": LLAMA_CPP_PINNED_TAG,
                    "gpu": gpu_name,
                    "vram": vram,
                },
            )
        except Exception as e:
            log.warning("mDNS advertisement failed: %s", e)

    # ------------------------------------------------------------------ host discovery

    def _ensure_host(self) -> bool:
        if self._host and self._probe_host(self._host):
            return True
        self._host = discover_host(self.cfg)
        if self._host:
            self.cfg.host_ip = self._host.ip
            save_config(self.cfg)
            self._registered = False  # need to re-register with new host endpoint
            return True
        return False

    def _probe_host(self, ep: HostEndpoint) -> bool:
        try:
            r = requests.get(f"{ep.base_url}/api/version", timeout=4)
            return r.status_code == 200
        except Exception:
            return False

    def _maybe_rediscover_host(self) -> None:
        log.info("Re-discovering host...")
        self._host = None
        self._registered = False
        self._ensure_host()
        if self._host:
            self._register_once()

    # ------------------------------------------------------------------ registration

    def _register_with_retry(self) -> None:
        backoff = 5
        while self._running and not self._registered:
            if self._ensure_host():
                if self._register_once():
                    return
            log.info("Will retry host registration in %ds", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _register_once(self) -> bool:
        if not self._host:
            return False
        try:
            sys_report = build_system_report(self.cfg.resolved_rpc_path)
            req = RegisterRequest(
                peer_id=self.cfg.peer_id,
                auth_token=self.cfg.auth_token,
                rpc_port=self.cfg.rpc_port,
                system=sys_report,
            )
            r = requests.post(
                f"{self._host.base_url}/api/register",
                json=req.model_dump(),
                timeout=10,
            )
            data = r.json()
            if data.get("accepted"):
                self.cfg.heartbeat_interval_sec = data.get("heartbeat_interval_sec", 30)
                self.cfg.host_version = data.get("host_version", "")
                save_config(self.cfg)
                self._registered = True
                log.info(
                    "Registered with host %s (slot=%s)",
                    self._host.base_url,
                    data.get("assigned_slot"),
                )
                return True
            else:
                log.warning("Registration rejected: %s", data.get("reject_reason"))
                return False
        except Exception as e:
            log.error("Registration attempt failed: %s", e)
            return False

    # ------------------------------------------------------------------ heartbeat

    def _get_sys_report(self):
        now = time.monotonic()
        if self._cached_report is None or (now - self._last_report_time) >= _SYSREPORT_REBUILD_SEC:
            self._cached_report = build_system_report(self.cfg.resolved_rpc_path)
            self._last_report_time = now
        return self._cached_report

    def _write_status(self, rpc_running: bool, sys_report=None) -> None:
        import json, datetime
        self._heartbeat_count += 1
        status = {
            "last_heartbeat": datetime.datetime.now().isoformat(),
            "host_ip": self.cfg.host_ip,
            "host_port": self.cfg.host_port,
            "rpc_running": rpc_running,
            "peer_id": self.cfg.peer_id[:16],
            "heartbeat_count": self._heartbeat_count,
        }
        if sys_report:
            status["hostname"] = sys_report.hostname
            status["cpu_model"] = sys_report.cpu_model
            status["ram_free_mb"] = sys_report.ram_free_mb
            status["ram_total_mb"] = sys_report.ram_total_mb
            status["disk_free_gb"] = sys_report.disk_free_gb
            if sys_report.gpus:
                g = sys_report.gpus[0]
                status["gpu_name"] = g.name
                status["vram_free_mb"] = g.vram_free_mb
                status["vram_total_mb"] = g.vram_total_mb
                status["driver_version"] = g.driver_version
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(status, f)
        except Exception:
            pass

    def _heartbeat_tick(self) -> None:
        # Non-blocking re-registration: one attempt per tick, no backoff loop.
        # _register_with_retry() (blocking) is only used at startup in run().
        if not self._registered:
            if not self._host:
                # Only probe the cached IP — never do slow mDNS here.
                from peer.host_discovery import discover_via_cache
                self._host = discover_via_cache(self.cfg.host_ip, self.cfg.host_port)
            if self._host:
                self._register_once()
            return

        from peer.service import service_running
        rpc_running = service_running("LlamaGridRPC")
        sys_report = self._get_sys_report()
        last_error = self._pop_pending_error()

        req = HeartbeatRequest(
            peer_id=self.cfg.peer_id,
            auth_token=self.cfg.auth_token,
            system=sys_report,
            rpc_server_running=rpc_running,
            last_error=last_error,
        )

        try:
            r = requests.post(
                f"{self._host.base_url}/api/heartbeat",
                json=req.model_dump(),
                timeout=4,  # short — must be well under peer_offline_after_sec
            )
        except (requests.ConnectionError, requests.Timeout):
            log.warning("Heartbeat failed — will retry next tick")
            self._registered = False
            return  # never block; retry on next tick
        except Exception:
            log.warning("Heartbeat error: %s", traceback.format_exc())
            return

        if r.status_code == 404:
            log.info("Host says peer unknown — re-registering next tick")
            self._registered = False
            return

        if r.status_code != 200:
            log.warning("Heartbeat returned %d", r.status_code)
            return

        resp = HeartbeatResponse.model_validate(r.json())
        log.debug("Heartbeat OK, command=%s", resp.command)

        if not resp.acknowledged:
            self._registered = False
            return

        self._write_status(rpc_running, sys_report)
        self._apply_command(resp)

    # ------------------------------------------------------------------ commands

    def _apply_command(self, resp: HeartbeatResponse) -> None:
        match resp.command:
            case "restart_rpc":
                log.info("Command: restart_rpc")
                from peer.service import restart_service
                threading.Thread(target=restart_service, args=("LlamaGridRPC",), daemon=True).start()

            case "update_rpc":
                log.info("Command: update_rpc")
                from peer import updater
                payload = resp.payload or {}
                threading.Thread(target=updater.update, args=(payload,), daemon=True).start()

            case "shutdown":
                log.info("Command: shutdown — stopping rpc-server and exiting agent")
                from peer.service import stop_service
                stop_service("LlamaGridRPC")
                self._running = False

            case "none" | _:
                pass

    # ------------------------------------------------------------------ error forwarding

    _pending_error: str | None = None

    def _pop_pending_error(self) -> str | None:
        err = self._pending_error
        self._pending_error = None
        return err

    def _set_error(self, msg: str) -> None:
        self._pending_error = msg
        if self._host:
            try:
                requests.post(
                    f"{self._host.base_url}/api/peer/error",
                    json={
                        "peer_id": self.cfg.peer_id,
                        "auth_token": self.cfg.auth_token,
                        "code": "RPC_ERROR",
                        "message": msg,
                    },
                    timeout=5,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------ log watcher

    def _start_log_watcher(self) -> None:
        t = threading.Thread(target=self._watch_rpc_log, daemon=True, name="log-watcher")
        t.start()

    def _watch_rpc_log(self) -> None:
        if not os.path.isfile(RPC_LOG):
            # Wait for the file to appear
            for _ in range(60):
                if not self._running:
                    return
                time.sleep(5)
                if os.path.isfile(RPC_LOG):
                    break
            else:
                return

        try:
            with open(RPC_LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)  # seek to end
                while self._running:
                    line = f.readline()
                    if not line:
                        time.sleep(1)
                        continue
                    lower = line.lower()
                    for pattern in _CUDA_ERRORS:
                        if pattern.lower() in lower:
                            self._set_error(line.strip()[:300])
                            break
        except Exception as e:
            log.debug("Log watcher error: %s", e)
