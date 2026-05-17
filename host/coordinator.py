from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

from shared import mdns
from shared.protocol import (
    AlertEvent,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
    SystemReport,
)
from shared.versioning import HOST_VERSION, LLAMA_CPP_PINNED_TAG
from host import alerts as alert_engine

if TYPE_CHECKING:
    from host.config import HostConfig

log = logging.getLogger(__name__)

PEERS_CACHE = r"C:\LlamaGrid\peers.json"


@dataclass
class PeerRecord:
    peer_id: str
    ip: str
    rpc_port: int
    hostname: str
    last_seen: float
    status: Literal["pending", "online", "offline", "warning", "error"] = "pending"
    system: SystemReport | None = None
    last_error: str | None = None
    layers_assigned: int | None = None
    rpc_server_running: bool = False
    discovered_via: Literal["mdns", "http"] = "http"
    alerts: list[AlertEvent] = field(default_factory=list)
    assigned_slot: int = 0

    def to_dict(self) -> dict:
        d = {
            "peer_id": self.peer_id,
            "ip": self.ip,
            "rpc_port": self.rpc_port,
            "hostname": self.hostname,
            "last_seen": self.last_seen,
            "status": self.status,
            "last_error": self.last_error,
            "layers_assigned": self.layers_assigned,
            "rpc_server_running": self.rpc_server_running,
            "discovered_via": self.discovered_via,
            "assigned_slot": self.assigned_slot,
            "system": self.system.model_dump() if self.system else None,
            "alerts": [a.model_dump() for a in self.alerts],
        }
        return d


class Coordinator:
    def __init__(self, cfg: "HostConfig"):
        self.cfg = cfg
        self._peers: dict[str, PeerRecord] = {}
        self._lock = threading.RLock()
        self._next_slot = 1
        self._pending_commands: dict[str, list[tuple[str, dict | None]]] = {}
        self._on_topology_change: Callable | None = None
        self._sweep_thread: threading.Thread | None = None
        self._persist_thread: threading.Thread | None = None
        self._host_info: mdns.ServiceInfo | None = None
        self._running = False

    # ------------------------------------------------------------------ startup

    def start(self) -> None:
        self._running = True
        self._load_peers_cache()
        self._sweep_thread = threading.Thread(target=self._sweep_loop, daemon=True, name="sweep")
        self._sweep_thread.start()
        self._persist_thread = threading.Thread(target=self._persist_loop, daemon=True, name="persist")
        self._persist_thread.start()
        self.start_mdns_advertiser()
        self.start_mdns_browser()

    def stop(self) -> None:
        self._running = False
        if self._host_info:
            mdns.unregister(self._host_info)
        mdns.shutdown()

    def set_topology_change_callback(self, cb: Callable) -> None:
        self._on_topology_change = cb

    # ------------------------------------------------------------------ mDNS

    def start_mdns_advertiser(self) -> None:
        from shared.versioning import PROTOCOL_VERSION
        try:
            self._host_info = mdns.advertise_host(
                self.cfg.port,
                {
                    "ver": HOST_VERSION,
                    "proto": str(PROTOCOL_VERSION),
                    "token_required": "true",
                },
            )
        except Exception as e:
            log.warning("mDNS host advertisement failed (LAN still works via IP): %s", e)

    def start_mdns_browser(self) -> None:
        try:
            mdns.browse_peers(
                on_added=self._on_mdns_peer_added,
                on_removed=self._on_mdns_peer_removed,
            )
        except Exception as e:
            log.warning("mDNS peer browser failed: %s", e)

    def _on_mdns_peer_added(self, ip: str, port: int, props: dict) -> None:
        peer_id = props.get("id", "")
        log.debug("mDNS peer seen: ip=%s port=%d id=%s", ip, port, peer_id)
        # Just a hint — full auth happens via HTTP register

    def _on_mdns_peer_removed(self, name_prefix: str) -> None:
        log.debug("mDNS peer departed: %s", name_prefix)

    # ------------------------------------------------------------------ register

    def register(self, req: RegisterRequest, source_ip: str) -> RegisterResponse:
        with self._lock:
            # Auth check
            if req.auth_token != self.cfg.auth_token:
                log.warning("Rejected registration from %s — bad auth token", source_ip)
                return RegisterResponse(
                    accepted=False,
                    host_version=HOST_VERSION,
                    heartbeat_interval_sec=self.cfg.heartbeat_interval_sec,
                    rpc_server_latest_version=LLAMA_CPP_PINNED_TAG,
                    reject_reason="invalid_auth_token",
                )

            # Capacity check
            active = sum(1 for p in self._peers.values() if p.status != "offline")
            if req.peer_id not in self._peers and active >= self.cfg.max_peers:
                return RegisterResponse(
                    accepted=False,
                    host_version=HOST_VERSION,
                    heartbeat_interval_sec=self.cfg.heartbeat_interval_sec,
                    rpc_server_latest_version=LLAMA_CPP_PINNED_TAG,
                    reject_reason="cluster_full",
                )

            is_new = req.peer_id not in self._peers
            slot = self._peers[req.peer_id].assigned_slot if not is_new else self._next_slot
            if is_new:
                self._next_slot += 1

            peer = PeerRecord(
                peer_id=req.peer_id,
                ip=source_ip,
                rpc_port=req.rpc_port,
                hostname=req.system.hostname,
                last_seen=time.time(),
                status="online",
                system=req.system,
                rpc_server_running=True,
                assigned_slot=slot,
            )
            self._peers[req.peer_id] = peer
            peer.alerts = alert_engine.evaluate(peer)

            log.info(
                "Peer registered: %s (%s) slot=%d new=%s",
                req.system.hostname, source_ip, slot, is_new,
            )

            if is_new and self._on_topology_change:
                threading.Thread(target=self._on_topology_change, daemon=True).start()

            return RegisterResponse(
                accepted=True,
                assigned_slot=slot,
                host_version=HOST_VERSION,
                heartbeat_interval_sec=self.cfg.heartbeat_interval_sec,
                rpc_server_latest_version=LLAMA_CPP_PINNED_TAG,
            )

    # ------------------------------------------------------------------ heartbeat

    def heartbeat(self, req: HeartbeatRequest, source_ip: str) -> HeartbeatResponse:
        with self._lock:
            if req.auth_token != self.cfg.auth_token:
                return HeartbeatResponse(acknowledged=False)

            peer = self._peers.get(req.peer_id)
            if peer is None:
                # Unknown peer — ask it to re-register by returning a rejection
                return HeartbeatResponse(acknowledged=False)

            was_offline = peer.status == "offline"
            peer.ip = source_ip
            peer.last_seen = time.time()
            peer.status = "online"
            peer.system = req.system
            peer.rpc_server_running = req.rpc_server_running
            peer.last_error = req.last_error
            if req.layers_assigned is not None:
                peer.layers_assigned = req.layers_assigned
            peer.alerts = alert_engine.evaluate(peer)

            if peer.alerts:
                worst = max(peer.alerts, key=lambda a: ["info", "warning", "error"].index(a.severity))
                peer.status = "warning" if worst.severity == "warning" else "error" if worst.severity == "error" else "online"

            log.debug("Heartbeat from %s (%s) rpc_running=%s", peer.hostname, source_ip, req.rpc_server_running)

            if was_offline and self._on_topology_change:
                threading.Thread(target=self._on_topology_change, daemon=True).start()

            # Pop a queued command if any
            cmds = self._pending_commands.get(req.peer_id, [])
            if cmds:
                cmd_name, payload = cmds.pop(0)
                return HeartbeatResponse(acknowledged=True, command=cmd_name, payload=payload)

            return HeartbeatResponse(acknowledged=True)

    # ------------------------------------------------------------------ peer error

    def report_error(self, peer_id: str, code: str, message: str) -> None:
        with self._lock:
            peer = self._peers.get(peer_id)
            if peer:
                peer.last_error = f"[{code}] {message}"
                peer.alerts = alert_engine.evaluate(peer)
                log.warning("Peer %s error [%s]: %s", peer.hostname if peer else peer_id, code, message)

    # ------------------------------------------------------------------ commands

    def queue_command(self, peer_id: str, command: str, payload: dict | None = None) -> bool:
        with self._lock:
            if peer_id not in self._peers:
                return False
            if peer_id not in self._pending_commands:
                self._pending_commands[peer_id] = []
            self._pending_commands[peer_id].append((command, payload))
            log.info("Queued command '%s' for peer %s", command, peer_id)
            return True

    # ------------------------------------------------------------------ queries

    def all_peers(self) -> list[PeerRecord]:
        with self._lock:
            return list(self._peers.values())

    def get_peer(self, peer_id: str) -> PeerRecord | None:
        with self._lock:
            return self._peers.get(peer_id)

    def online_peers_for_rpc(self) -> list[PeerRecord]:
        with self._lock:
            return [p for p in self._peers.values() if p.status in ("online", "warning")]

    def forget_peer(self, peer_id: str) -> bool:
        with self._lock:
            if peer_id in self._peers:
                del self._peers[peer_id]
                self._pending_commands.pop(peer_id, None)
                if self._on_topology_change:
                    threading.Thread(target=self._on_topology_change, daemon=True).start()
                return True
            return False

    def all_alerts(self) -> list[AlertEvent]:
        with self._lock:
            out = []
            for p in self._peers.values():
                out.extend(p.alerts)
            out.sort(key=lambda a: a.ts_epoch, reverse=True)
            return out

    # ------------------------------------------------------------------ cluster summary

    def cluster_summary(self, tokens_per_sec: float, llama_running: bool, model_path: str) -> dict:
        with self._lock:
            peers = list(self._peers.values())
            online = [p for p in peers if p.status in ("online", "warning")]
            total_ram = sum(p.system.ram_total_mb for p in online if p.system)
            free_ram = sum(p.system.ram_free_mb for p in online if p.system)
            total_vram = sum(
                g.vram_total_mb for p in online if p.system for g in p.system.gpus
            )
            free_vram = sum(
                g.vram_free_mb for p in online if p.system for g in p.system.gpus
            )
            return {
                "peers_online": len(online),
                "peers_total": len(peers),
                "total_ram_mb": total_ram,
                "free_ram_mb": free_ram,
                "total_vram_mb": total_vram,
                "free_vram_mb": free_vram,
                "tokens_per_sec": round(tokens_per_sec, 2),
                "llama_server_running": llama_running,
                "model_path": model_path,
                "model_name": os.path.basename(model_path) if model_path else "",
            }

    # ------------------------------------------------------------------ sweep

    def _sweep_loop(self) -> None:
        while self._running:
            time.sleep(10)
            self._sweep()

    def _sweep(self) -> None:
        now = time.time()
        threshold = self.cfg.peer_offline_after_sec
        changed = False
        with self._lock:
            for peer in self._peers.values():
                if peer.status != "offline" and (now - peer.last_seen) > threshold:
                    peer.status = "offline"
                    peer.rpc_server_running = False
                    peer.alerts = alert_engine.evaluate(peer)
                    log.warning("Peer %s marked offline (no heartbeat for %ds)", peer.hostname, int(now - peer.last_seen))
                    changed = True

        if changed and self._on_topology_change:
            threading.Thread(target=self._on_topology_change, daemon=True).start()

    # ------------------------------------------------------------------ persistence

    def _persist_loop(self) -> None:
        while self._running:
            time.sleep(60)
            self._save_peers_cache()

    def _save_peers_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(PEERS_CACHE), exist_ok=True)
            with self._lock:
                data = [p.to_dict() for p in self._peers.values()]
            with open(PEERS_CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.debug("Peers cache save failed: %s", e)

    def _load_peers_cache(self) -> None:
        if not os.path.isfile(PEERS_CACHE):
            return
        try:
            with open(PEERS_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                for d in data:
                    sys_data = d.pop("system", None)
                    d.pop("alerts", None)
                    peer = PeerRecord(**{k: v for k, v in d.items() if k in PeerRecord.__dataclass_fields__})
                    peer.status = "offline"  # assume offline until heartbeat
                    if sys_data:
                        peer.system = SystemReport(**sys_data)
                    self._peers[peer.peer_id] = peer
                    if peer.assigned_slot >= self._next_slot:
                        self._next_slot = peer.assigned_slot + 1
            log.info("Loaded %d peers from cache", len(data))
        except Exception as e:
            log.warning("Failed to load peers cache: %s", e)
