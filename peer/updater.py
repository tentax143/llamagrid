from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import time
import zipfile

import requests

log = logging.getLogger(__name__)

RPC_DIR = r"C:\LlamaGrid\rpc"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: str, expected_sha256: str = "") -> bool:
    """Stream-download url to dest. Return True on success."""
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        if expected_sha256:
            actual = _sha256_file(dest)
            if actual.lower() != expected_sha256.lower():
                log.error("SHA256 mismatch: expected %s got %s", expected_sha256, actual)
                os.remove(dest)
                return False
        return True
    except Exception as e:
        log.error("Download failed: %s", e)
        return False


def update(payload: dict, service_name: str = "LlamaGridRPC") -> bool:
    """
    Download new rpc-server build from payload dict and hot-swap it.
    payload keys: download_url, sha256, version
    """
    url = payload.get("download_url", "")
    sha256 = payload.get("sha256", "")
    version = payload.get("version", "unknown")

    if not url:
        log.error("update payload missing download_url")
        return False

    log.info("Downloading rpc-server %s from %s", version, url)
    os.makedirs(RPC_DIR, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "rpc_update.zip")
        if not download_file(url, zip_path, sha256):
            return False

        # Extract
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as e:
            log.error("Bad zip file: %s", e)
            return False

        # Find rpc-server.exe in extracted tree
        new_exe = None
        for root, _dirs, files in os.walk(extract_dir):
            for fname in files:
                if fname.lower() == "rpc-server.exe":
                    new_exe = os.path.join(root, fname)
                    break
            if new_exe:
                break

        if not new_exe:
            log.error("rpc-server.exe not found in downloaded zip")
            return False

        # Stop service, swap binary, restart
        from peer.service import stop_service, start_service
        stop_service(service_name)
        time.sleep(2)

        dest_exe = os.path.join(RPC_DIR, "rpc-server.exe")
        backup = dest_exe + ".bak"

        try:
            if os.path.isfile(dest_exe):
                shutil.copy2(dest_exe, backup)
            shutil.copy2(new_exe, dest_exe)

            # Also copy DLLs
            for root, _dirs, files in os.walk(extract_dir):
                for fname in files:
                    if fname.lower().endswith(".dll"):
                        shutil.copy2(os.path.join(root, fname), os.path.join(RPC_DIR, fname))

            log.info("rpc-server updated to version %s", version)
            return True
        except Exception as e:
            log.error("Failed to swap rpc-server binary: %s", e)
            # Restore backup
            if os.path.isfile(backup):
                shutil.copy2(backup, dest_exe)
            return False
        finally:
            start_service(service_name)
