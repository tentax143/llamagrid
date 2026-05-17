"""
Download llama.cpp and NSSM binaries into the bundled/ directories.
Run once before building. Idempotent — skips files that pass sha256 check.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import zipfile
from pathlib import Path

# Add repo root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

from shared.versioning import (
    LLAMA_CPP_PINNED_TAG,
    LLAMA_CPP_RELEASE_URL,
    LLAMA_CPP_SHA256,
    NSSM_URL,
    NSSM_SHA256,
)

HOST_LLAMA_DIR = ROOT / "host" / "bundled" / "llama-server"
PEER_RPC_DIR   = ROOT / "peer" / "bundled" / "rpc-server"
PEER_NSSM      = ROOT / "peer" / "bundled" / "nssm.exe"

HOST_LLAMA_KEEP = {"llama-server.exe"}
PEER_RPC_KEEP   = {"rpc-server.exe"}
DLL_SUFFIX      = ".dll"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expected_sha: str = "") -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and expected_sha:
        if sha256(dest).lower() == expected_sha.lower():
            print(f"  ✓ {dest.name} already present (sha256 OK)")
            return True

    print(f"  ↓ {dest.name} from {url[:80]}...")
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = int(done / total * 100)
                        print(f"\r  {pct}%", end="", flush=True)
        print()
    except Exception as e:
        print(f"  ✗ Download failed: {e}")
        return False

    if expected_sha:
        actual = sha256(dest)
        if actual.lower() != expected_sha.lower():
            print(f"  ✗ SHA256 mismatch: expected {expected_sha}, got {actual}")
            dest.unlink(missing_ok=True)
            return False

    print(f"  ✓ {dest.name} OK")
    return True


def extract_and_copy(zip_path: Path, dest_dir: Path, keep_exes: set[str]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = Path(member).name.lower()
            base = Path(member).name
            if base.lower() in keep_exes or base.lower().endswith(DLL_SUFFIX):
                target = dest_dir / base
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                print(f"    extracted: {base}")


def extract_nssm(zip_path: Path, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.lower().endswith("win64/nssm.exe"):
                with zf.open(member) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                print(f"    extracted nssm.exe → {dest}")
                return True
    print("  ✗ win64/nssm.exe not found in archive")
    return False


def main() -> None:
    import tempfile

    print("=== LlamaGrid binary fetch ===")
    print(f"llama.cpp tag: {LLAMA_CPP_PINNED_TAG}")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Download llama.cpp zip once
        llama_zip = tmp / "llama.zip"
        print("[llama.cpp]")
        ok = download(LLAMA_CPP_RELEASE_URL, llama_zip, LLAMA_CPP_SHA256)
        if ok:
            print("  Extracting for host...")
            extract_and_copy(llama_zip, HOST_LLAMA_DIR, {"llama-server.exe"})
            print("  Extracting for peer...")
            extract_and_copy(llama_zip, PEER_RPC_DIR, {"rpc-server.exe"})
        else:
            print("  WARNING: llama.cpp download failed — build will use system binaries if present")

        # 2. Download NSSM
        nssm_zip = tmp / "nssm.zip"
        print("\n[NSSM]")
        ok = download(NSSM_URL, nssm_zip, NSSM_SHA256)
        if ok:
            if PEER_NSSM.exists():
                print(f"  ✓ nssm.exe already at {PEER_NSSM}")
            else:
                extract_nssm(nssm_zip, PEER_NSSM)
        else:
            print("  WARNING: NSSM download failed — peer service install will fail without it")

    print("\n=== Done ===")
    print(f"Host llama-server dir: {HOST_LLAMA_DIR}")
    print(f"Peer rpc-server dir:   {PEER_RPC_DIR}")
    print(f"Peer nssm.exe:         {PEER_NSSM}")


if __name__ == "__main__":
    main()
