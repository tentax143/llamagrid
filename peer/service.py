from __future__ import annotations

import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)

RPC_SERVICE = "LlamaGridRPC"
AGENT_SERVICE = "LlamaGridAgent"
RPC_DIR = r"C:\LlamaGrid\rpc"
LOG_DIR = r"C:\LlamaGrid\logs"


def _nssm(cfg, *args) -> tuple[int, str]:
    nssm = cfg.nssm_path
    if not os.path.isfile(nssm):
        return 1, f"nssm.exe not found at {nssm}"
    try:
        r = subprocess.run(
            [nssm, *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (r.stdout + r.stderr).strip()
        return r.returncode, out
    except Exception as e:
        return 1, str(e)


def _sc(*args) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["sc", *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def service_exists(name: str) -> bool:
    rc, _ = _sc("query", name)
    return rc == 0


def service_running(name: str) -> bool:
    rc, out = _sc("query", name)
    return rc == 0 and "RUNNING" in out


def install_rpc_service(cfg, rpc_exe: str) -> bool:
    os.makedirs(RPC_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    if service_exists(RPC_SERVICE):
        log.info("Service %s already exists — skipping install", RPC_SERVICE)
        return True

    log.info("Installing %s via NSSM", RPC_SERVICE)
    steps = [
        ("install", RPC_SERVICE, rpc_exe, "-H", "0.0.0.0", "-p", str(cfg.rpc_port)),
        ("set", RPC_SERVICE, "AppDirectory", RPC_DIR),
        ("set", RPC_SERVICE, "AppStdout", os.path.join(LOG_DIR, "rpc.out.log")),
        ("set", RPC_SERVICE, "AppStderr", os.path.join(LOG_DIR, "rpc.err.log")),
        ("set", RPC_SERVICE, "AppRotateFiles", "1"),
        ("set", RPC_SERVICE, "AppRotateBytes", "10485760"),
        ("set", RPC_SERVICE, "Start", "SERVICE_AUTO_START"),
        ("set", RPC_SERVICE, "ObjectName", "LocalSystem"),
        ("set", RPC_SERVICE, "AppRestartDelay", "5000"),
        ("set", RPC_SERVICE, "AppExit", "Default", "Restart"),
    ]

    for step in steps:
        rc, out = _nssm(cfg, *step)
        if rc != 0:
            log.error("NSSM step %s failed (rc=%d): %s", step[0], rc, out)
            return False
        log.debug("NSSM %s: %s", " ".join(str(s) for s in step[:3]), out[:100])

    rc, out = start_service(RPC_SERVICE)
    return rc == 0


def install_agent_service(cfg, agent_exe: str) -> bool:
    if service_exists(AGENT_SERVICE):
        log.info("Service %s already exists", AGENT_SERVICE)
        return True

    log.info("Installing %s via NSSM", AGENT_SERVICE)
    steps = [
        ("install", AGENT_SERVICE, agent_exe, "--service"),
        ("set", AGENT_SERVICE, "AppDirectory", os.path.dirname(agent_exe)),
        ("set", AGENT_SERVICE, "AppStdout", os.path.join(LOG_DIR, "agent.out.log")),
        ("set", AGENT_SERVICE, "AppStderr", os.path.join(LOG_DIR, "agent.err.log")),
        ("set", AGENT_SERVICE, "AppRotateFiles", "1"),
        ("set", AGENT_SERVICE, "AppRotateBytes", "10485760"),
        ("set", AGENT_SERVICE, "Start", "SERVICE_AUTO_START"),
        ("set", AGENT_SERVICE, "AppExit", "Default", "Restart"),
    ]

    for step in steps:
        rc, out = _nssm(cfg, *step)
        if rc != 0:
            log.error("NSSM agent step %s failed: %s", step[0], out)
            return False

    return True


def start_service(name: str) -> tuple[int, str]:
    log.info("Starting service: %s", name)
    rc, out = _sc("start", name)
    if rc not in (0, 1056):  # 1056 = already running
        log.error("Failed to start %s: %s", name, out)
    return rc, out


def stop_service(name: str) -> tuple[int, str]:
    log.info("Stopping service: %s", name)
    return _sc("stop", name)


def restart_service(name: str) -> bool:
    stop_service(name)
    time.sleep(3)
    rc, _ = start_service(name)
    return rc in (0, 1056)


def remove_rpc_service(cfg) -> None:
    stop_service(RPC_SERVICE)
    time.sleep(2)
    _nssm(cfg, "remove", RPC_SERVICE, "confirm")
    log.info("Removed service %s", RPC_SERVICE)


def remove_agent_service(cfg) -> None:
    stop_service(AGENT_SERVICE)
    time.sleep(2)
    _nssm(cfg, "remove", AGENT_SERVICE, "confirm")
    log.info("Removed service %s", AGENT_SERVICE)
