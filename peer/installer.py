from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import winreg
import zipfile
from typing import Callable

import requests

from peer.config import PeerConfig, DATA_DIR
from shared.versioning import (
    LLAMA_CPP_PINNED_TAG,
    LLAMA_CPP_RELEASE_URL,
    LLAMA_CPP_SHA256,
    NSSM_URL,
    NSSM_SHA256,
    VC_REDIST_URL,
)

log = logging.getLogger(__name__)

RPC_DIR = os.path.join(DATA_DIR, "rpc")
CACHE_DIR = os.path.join(DATA_DIR, "cache")

Step = tuple[bool, str]


def _progress(cb: Callable[[str], None] | None, msg: str) -> None:
    log.info(msg)
    if cb:
        cb(msg)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: str, expected_sha256: str = "", progress_cb=None) -> bool:
    fname = os.path.basename(dest)

    # Check cache first
    cached = os.path.join(CACHE_DIR, fname)
    if os.path.isfile(cached):
        if not expected_sha256 or _sha256_file(cached).lower() == expected_sha256.lower():
            _progress(progress_cb, f"Using cached {fname}")
            shutil.copy2(cached, dest)
            return True

    _progress(progress_cb, f"Downloading {fname}...")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and progress_cb:
                        pct = int(downloaded / total * 100)
                        progress_cb(f"Downloading {fname}... {pct}%")
    except Exception as e:
        log.error("Download failed: %s", e)
        return False

    if expected_sha256:
        actual = _sha256_file(dest)
        if actual.lower() != expected_sha256.lower():
            log.error("SHA256 mismatch for %s", fname)
            os.remove(dest)
            return False

    # Cache it
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        shutil.copy2(dest, cached)
    except Exception:
        pass

    return True


# ── Individual checks ───────────────────────────────────────────────────


def check_vc_redist(progress_cb=None) -> Step:
    _progress(progress_cb, "Checking Visual C++ Redistributable...")
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        )
        installed = winreg.QueryValueEx(key, "Installed")[0]
        if installed:
            return True, "VC++ Redistributable already installed"
    except FileNotFoundError:
        pass

    # Also check via newer registry path
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        )
        installed = winreg.QueryValueEx(key, "Installed")[0]
        if installed:
            return True, "VC++ Redistributable already installed"
    except FileNotFoundError:
        pass

    _progress(progress_cb, "Installing Visual C++ Redistributable...")
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = os.path.join(tmpdir, "vc_redist.x64.exe")
        if not _download(VC_REDIST_URL, dest, progress_cb=progress_cb):
            return False, "Failed to download VC++ Redistributable"
        r = subprocess.run(
            [dest, "/install", "/quiet", "/norestart"],
            capture_output=True, timeout=300
        )
        if r.returncode in (0, 3010):  # 3010 = success, reboot required
            return True, "VC++ Redistributable installed"
        return False, f"VC++ install failed (rc={r.returncode})"


def check_cuda(progress_cb=None) -> Step:
    _progress(progress_cb, "Checking CUDA installation...")
    from shared.sysreport import _cuda_version
    ver = _cuda_version()
    if ver:
        return True, f"CUDA {ver} detected"

    _progress(progress_cb, "CUDA not found — please install CUDA 12.1+ manually from https://developer.nvidia.com/cuda-downloads")
    _progress(progress_cb, "WARNING: rpc-server may fail without CUDA. Continuing anyway...")
    return True, "CUDA not detected — install manually if needed"


def check_rpc_server(cfg: PeerConfig, progress_cb=None) -> Step:
    _progress(progress_cb, "Checking rpc-server.exe...")

    # 1. Check if C:\llama\rpc-server.exe exists (user's existing install)
    llama_rpc = os.path.join(cfg.llama_dir, "rpc-server.exe")
    if os.path.isfile(llama_rpc):
        cfg.rpc_server_path = llama_rpc
        from peer.config import save_config
        save_config(cfg)
        return True, f"Found rpc-server.exe at {llama_rpc}"

    # 2. Check our own managed dir
    managed_rpc = os.path.join(RPC_DIR, "rpc-server.exe")
    if os.path.isfile(managed_rpc):
        cfg.rpc_server_path = managed_rpc
        from peer.config import save_config
        save_config(cfg)
        return True, f"Found rpc-server.exe at {managed_rpc}"

    # 3. Download from GitHub
    _progress(progress_cb, f"Downloading llama.cpp {LLAMA_CPP_PINNED_TAG}...")
    os.makedirs(RPC_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "llama.zip")
        if not _download(LLAMA_CPP_RELEASE_URL, zip_path, LLAMA_CPP_SHA256, progress_cb):
            return False, "Failed to download llama.cpp"

        _progress(progress_cb, "Extracting llama.cpp...")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmpdir)
        except Exception as e:
            return False, f"Extraction failed: {e}"

        # Find and copy rpc-server.exe + DLLs
        found = False
        for root, _dirs, files in os.walk(tmpdir):
            for fname in files:
                src = os.path.join(root, fname)
                if fname.lower() == "rpc-server.exe":
                    shutil.copy2(src, os.path.join(RPC_DIR, fname))
                    found = True
                elif fname.lower().endswith(".dll"):
                    shutil.copy2(src, os.path.join(RPC_DIR, fname))

        if not found:
            return False, "rpc-server.exe not found in downloaded archive"

    rpc_path = os.path.join(RPC_DIR, "rpc-server.exe")
    cfg.rpc_server_path = rpc_path
    from peer.config import save_config
    save_config(cfg)
    return True, f"rpc-server.exe installed to {rpc_path}"


def check_firewall(cfg: PeerConfig, progress_cb=None) -> Step:
    _progress(progress_cb, f"Checking firewall rule for port {cfg.rpc_port}...")
    r = subprocess.run(
        ["netsh", "advfirewall", "firewall", "show", "rule", "name=LlamaGrid-RPC"],
        capture_output=True, text=True, timeout=10
    )
    if r.returncode == 0 and "LlamaGrid-RPC" in r.stdout:
        return True, "Firewall rule already exists"

    rpc_exe = cfg.resolved_rpc_path
    _progress(progress_cb, f"Adding firewall rule for port {cfg.rpc_port}...")
    cmd = [
        "netsh", "advfirewall", "firewall", "add", "rule",
        "name=LlamaGrid-RPC",
        "dir=in",
        "action=allow",
        "protocol=TCP",
        f"localport={cfg.rpc_port}",
    ]
    if os.path.isfile(rpc_exe):
        cmd += [f"program={rpc_exe}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode == 0:
        return True, "Firewall rule added"
    return False, f"Failed to add firewall rule: {r.stderr}"


def check_nssm(cfg: PeerConfig, progress_cb=None) -> Step:
    _progress(progress_cb, "Checking NSSM...")
    if os.path.isfile(cfg.nssm_path):
        return True, f"NSSM found at {cfg.nssm_path}"

    _progress(progress_cb, "Downloading NSSM...")
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "nssm.zip")
        if not _download(NSSM_URL, zip_path, NSSM_SHA256, progress_cb):
            return False, "Failed to download NSSM"
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmpdir)
        except Exception as e:
            return False, f"NSSM extraction failed: {e}"

        # Find win64/nssm.exe
        for root, _dirs, files in os.walk(tmpdir):
            for fname in files:
                if fname.lower() == "nssm.exe" and "win64" in root.lower():
                    dest = cfg.nssm_path
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(os.path.join(root, fname), dest)
                    return True, f"NSSM installed to {dest}"

    return False, "nssm.exe not found in downloaded archive"


def check_service(cfg: PeerConfig, progress_cb=None) -> Step:
    from peer.service import install_rpc_service, service_exists, start_service, service_running
    _progress(progress_cb, "Checking rpc-server Windows service...")

    rpc_exe = cfg.resolved_rpc_path
    if not os.path.isfile(rpc_exe):
        return False, f"rpc-server.exe not found at {rpc_exe}"

    if service_exists("LlamaGridRPC"):
        if not service_running("LlamaGridRPC"):
            start_service("LlamaGridRPC")
        return True, "Service LlamaGridRPC already installed"

    ok = install_rpc_service(cfg, rpc_exe)
    if ok:
        return True, "Service LlamaGridRPC installed and started"
    return False, "Failed to install LlamaGridRPC service"


def run_all_checks(
    cfg: PeerConfig,
    progress_cb: Callable[[str], None] | None = None,
) -> list[tuple[str, bool, str]]:
    """
    Run all checks in order.
    Returns list of (step_name, success, message).
    """
    results = []

    steps = [
        ("VC++ Redistributable", lambda: check_vc_redist(progress_cb)),
        ("CUDA Check",           lambda: check_cuda(progress_cb)),
        ("rpc-server.exe",       lambda: check_rpc_server(cfg, progress_cb)),
        ("Firewall Rule",        lambda: check_firewall(cfg, progress_cb)),
        ("NSSM",                 lambda: check_nssm(cfg, progress_cb)),
        ("Windows Service",      lambda: check_service(cfg, progress_cb)),
    ]

    for name, fn in steps:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"Exception: {e}"
            log.exception("Step '%s' raised", name)
        results.append((name, ok, msg))
        level = logging.INFO if ok else logging.ERROR
        log.log(level, "%-25s %s  %s", name, "✓" if ok else "✗", msg)

    return results
