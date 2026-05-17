from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

log = logging.getLogger(__name__)

SERVICE_TYPE_PEER = "_llamagrid-peer._tcp.local."
SERVICE_TYPE_HOST = "_llamagrid-host._tcp.local."

_zeroconf: Zeroconf | None = None
_lock = threading.Lock()


def _get_zeroconf() -> Zeroconf:
    global _zeroconf
    with _lock:
        if _zeroconf is None:
            _zeroconf = Zeroconf()
    return _zeroconf


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def advertise_peer(
    peer_id: str, port: int, properties: dict[str, str]
) -> ServiceInfo:
    zc = _get_zeroconf()
    local_ip = _local_ip()
    name = f"{peer_id[:8]}.{SERVICE_TYPE_PEER}"

    txt = {k: v.encode() for k, v in properties.items()}
    info = ServiceInfo(
        SERVICE_TYPE_PEER,
        name,
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties=txt,
        server=f"{socket.gethostname()}.local.",
    )
    zc.register_service(info)
    log.info("mDNS peer advertisement started: %s port %d", name, port)
    return info


def advertise_host(port: int, properties: dict[str, str]) -> ServiceInfo:
    zc = _get_zeroconf()
    local_ip = _local_ip()
    name = f"{socket.gethostname()}.{SERVICE_TYPE_HOST}"

    txt = {k: v.encode() for k, v in properties.items()}
    info = ServiceInfo(
        SERVICE_TYPE_HOST,
        name,
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties=txt,
        server=f"{socket.gethostname()}.local.",
    )
    zc.register_service(info)
    log.info("mDNS host advertisement started: port %d", port)
    return info


def browse_peers(
    on_added: Callable[[str, int, dict], None],
    on_removed: Callable[[str], None],
) -> ServiceBrowser:
    zc = _get_zeroconf()

    class _Handler:
        def add_service(self, zc, type_, name):
            try:
                info = zc.get_service_info(type_, name, timeout=3000)
                if info is None:
                    return
                addr = socket.inet_ntoa(info.addresses[0]) if info.addresses else ""
                props = {
                    k.decode(): v.decode() if isinstance(v, bytes) else v
                    for k, v in info.properties.items()
                }
                on_added(addr, info.port, props)
            except Exception as e:
                log.debug("browse_peers add_service error: %s", e)

        def remove_service(self, zc, type_, name):
            try:
                # Extract peer_id prefix from name
                peer_prefix = name.split(".")[0]
                on_removed(peer_prefix)
            except Exception as e:
                log.debug("browse_peers remove_service error: %s", e)

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

    browser = ServiceBrowser(zc, SERVICE_TYPE_PEER, _Handler())
    log.info("mDNS peer browser started")
    return browser


def browse_host(on_found: Callable[[str, int, dict], None]) -> ServiceBrowser:
    zc = _get_zeroconf()

    class _Handler:
        def add_service(self, zc, type_, name):
            try:
                info = zc.get_service_info(type_, name, timeout=3000)
                if info is None:
                    return
                addr = socket.inet_ntoa(info.addresses[0]) if info.addresses else ""
                props = {
                    k.decode(): v.decode() if isinstance(v, bytes) else v
                    for k, v in info.properties.items()
                }
                on_found(addr, info.port, props)
            except Exception as e:
                log.debug("browse_host add_service error: %s", e)

        def remove_service(self, zc, type_, name):
            pass

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

    browser = ServiceBrowser(zc, SERVICE_TYPE_HOST, _Handler())
    log.info("mDNS host browser started")
    return browser


def unregister(info: ServiceInfo) -> None:
    try:
        _get_zeroconf().unregister_service(info)
    except Exception:
        pass


def shutdown() -> None:
    global _zeroconf
    with _lock:
        if _zeroconf is not None:
            _zeroconf.close()
            _zeroconf = None
