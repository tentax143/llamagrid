from __future__ import annotations

import logging
import os
import platform
import socket
import subprocess
import time

import psutil

from shared.protocol import GPUInfo, SystemReport
from shared.versioning import AGENT_VERSION

log = logging.getLogger(__name__)


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _cpu_model() -> str:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        )
        return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:
        return platform.processor() or "Unknown CPU"


def _cuda_version_from_nvcc() -> str:
    try:
        r = subprocess.run(
            ["nvcc", "--version"], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if "release" in line.lower():
                    # "Cuda compilation tools, release 12.1, V12.1.105"
                    after = line.split("release", 1)[1].strip()
                    return after.split(",")[0].strip()
    except Exception:
        pass
    return ""


def _cuda_version_from_registry() -> str:
    try:
        import winreg
        for ver in ["12.4", "12.3", "12.2", "12.1", "12.0", "11.8", "11.7"]:
            try:
                winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    f"SOFTWARE\\NVIDIA Corporation\\GPU Computing Toolkit\\CUDA\\v{ver}",
                )
                return ver
            except FileNotFoundError:
                continue
    except Exception:
        pass
    return ""


def _cuda_version() -> str:
    v = _cuda_version_from_nvcc()
    if v:
        return v
    return _cuda_version_from_registry()


def _get_gpus() -> list[GPUInfo]:
    gpus: list[GPUInfo] = []
    cuda_ver = _cuda_version()
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4:
                    continue
                try:
                    gpus.append(
                        GPUInfo(
                            name=parts[0],
                            vram_total_mb=int(float(parts[1])),
                            vram_free_mb=int(float(parts[2])),
                            driver_version=parts[3],
                            cuda_version=cuda_ver,
                        )
                    )
                except (ValueError, IndexError):
                    continue
    except FileNotFoundError:
        log.debug("nvidia-smi not found — no NVIDIA GPU or driver not installed")
    except Exception as e:
        log.debug("nvidia-smi error: %s", e)
    return gpus


def _rpc_server_version(rpc_path: str = "") -> str:
    candidates = []
    if rpc_path:
        candidates.append(rpc_path)
    candidates += [
        r"C:\LlamaGrid\rpc\rpc-server.exe",
        r"C:\llama\rpc-server.exe",
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            r = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=5
            )
            out = (r.stdout + r.stderr).strip()
            if out:
                return out.splitlines()[0][:80]
        except Exception:
            continue
    return "unknown"


def build_system_report(rpc_server_path: str = "") -> SystemReport:
    mem = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage("C:\\")
        disk_free_gb = disk.free // (1024 ** 3)
    except Exception:
        disk_free_gb = 0

    return SystemReport(
        hostname=platform.node(),
        ip=_local_ip(),
        cpu_model=_cpu_model(),
        cpu_cores_physical=psutil.cpu_count(logical=False) or 1,
        cpu_cores_logical=psutil.cpu_count(logical=True) or 1,
        ram_total_mb=mem.total // (1024 * 1024),
        ram_free_mb=mem.available // (1024 * 1024),
        gpus=_get_gpus(),
        os_version=f"{platform.system()} {platform.release()} {platform.version()}",
        disk_free_gb=disk_free_gb,
        rpc_server_version=_rpc_server_version(rpc_server_path),
        agent_version=AGENT_VERSION,
        boot_time_epoch=int(psutil.boot_time()),
    )


if __name__ == "__main__":
    import json
    report = build_system_report()
    print(json.dumps(report.model_dump(), indent=2))
