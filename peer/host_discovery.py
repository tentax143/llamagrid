from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


@dataclass
class HostEndpoint:
    ip: str
    port: int
    version: str = ""

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}:{self.port}"


def probe_host(ip: str, port: int, timeout: int = 4) -> HostEndpoint | None:
    """Return HostEndpoint if /api/version responds, else None."""
    try:
        url = f"http://{ip}:{port}/api/version"
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return HostEndpoint(ip=ip, port=port, version=data.get("host_version", ""))
    except Exception:
        pass
    return None


def discover_via_cache(host_ip: str, host_port: int) -> HostEndpoint | None:
    if not host_ip:
        return None
    log.debug("Probing cached host %s:%d", host_ip, host_port)
    return probe_host(host_ip, host_port)


def discover_via_mdns(timeout_sec: int = 15) -> HostEndpoint | None:
    """Browse mDNS for _llamagrid-host._tcp.local., return first found."""
    found: list[HostEndpoint] = []
    event = threading.Event()

    def on_found(ip: str, port: int, props: dict) -> None:
        ep = probe_host(ip, port, timeout=3)
        if ep:
            found.append(ep)
            event.set()

    try:
        from shared import mdns
        browser = mdns.browse_host(on_found)
        event.wait(timeout=timeout_sec)
        mdns.shutdown()
        if found:
            log.info("Found host via mDNS: %s:%d", found[0].ip, found[0].port)
            return found[0]
    except Exception as e:
        log.debug("mDNS host discovery error: %s", e)
    return None


def discover_host(cfg) -> HostEndpoint | None:
    """Full discovery sequence: cache → mDNS."""
    # 1. Cached / configured IP
    ep = discover_via_cache(cfg.host_ip, cfg.host_port)
    if ep:
        log.debug("Host reachable at cached address %s", ep.base_url)
        return ep

    # 2. mDNS
    log.info("Cached host unreachable, trying mDNS discovery...")
    ep = discover_via_mdns(timeout_sec=15)
    if ep:
        return ep

    log.warning("Could not discover host — will retry later")
    return None
