from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from shared.protocol import AlertEvent
from shared.versioning import DRIVER_VERSION_MIN, LLAMA_CPP_PINNED_TAG

if TYPE_CHECKING:
    from host.coordinator import PeerRecord

log = logging.getLogger(__name__)


def evaluate(peer: "PeerRecord", now: float | None = None) -> list[AlertEvent]:
    now = now or time.time()
    alerts: list[AlertEvent] = []
    ts = int(now)

    def alert(severity, code, message):
        alerts.append(AlertEvent(peer_id=peer.peer_id, severity=severity, code=code, message=message, ts_epoch=ts))

    # Offline
    if peer.status == "offline":
        alert("error", "PEER_OFFLINE", f"{peer.hostname} has not sent a heartbeat in >{90}s")

    # RPC not running
    if peer.status != "offline" and not peer.rpc_server_running:
        alert("error", "RPC_NOT_RUNNING", f"rpc-server is not running on {peer.hostname}")

    sys = peer.system
    if sys is None:
        return alerts

    # CUDA missing
    has_cuda = any(g.cuda_version for g in sys.gpus)
    if sys.gpus and not has_cuda:
        alert("error", "CUDA_MISSING", f"No CUDA runtime detected on {peer.hostname}")

    # Low VRAM
    for gpu in sys.gpus:
        if gpu.vram_free_mb < 1024:
            alert(
                "warning",
                "LOW_VRAM",
                f"{peer.hostname} GPU {gpu.name}: only {gpu.vram_free_mb} MB VRAM free",
            )

    # Low RAM
    if sys.ram_free_mb < 2048:
        alert("warning", "LOW_RAM", f"{peer.hostname} only has {sys.ram_free_mb} MB RAM free")

    # Low disk
    if sys.disk_free_gb < 5:
        alert("warning", "DISK_LOW", f"{peer.hostname} only has {sys.disk_free_gb} GB disk free")

    # Outdated driver
    for gpu in sys.gpus:
        try:
            major = int(gpu.driver_version.split(".")[0])
            if major < DRIVER_VERSION_MIN:
                alert(
                    "warning",
                    "DRIVER_OUTDATED",
                    f"{peer.hostname} GPU driver {gpu.driver_version} is below recommended {DRIVER_VERSION_MIN}",
                )
        except (ValueError, IndexError):
            pass

    # RPC version drift
    pinned = LLAMA_CPP_PINNED_TAG
    if sys.rpc_server_version and pinned not in sys.rpc_server_version:
        alert(
            "warning",
            "RPC_VERSION_DRIFT",
            f"{peer.hostname} rpc-server version '{sys.rpc_server_version}' differs from pinned '{pinned}'",
        )

    # Last error forwarded
    if peer.last_error:
        alert("error", "LAST_ERROR", f"{peer.hostname}: {peer.last_error}")

    return alerts
