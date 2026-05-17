import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from host.coordinator import PeerRecord
from host import alerts as engine
from shared.protocol import GPUInfo, SystemReport


def _sys(cuda="12.4", driver="551.86", vram_free=18000, ram_free=32768):
    return SystemReport(
        hostname="w1", ip="10.0.0.2", cpu_model="i9", cpu_cores_physical=8,
        cpu_cores_logical=16, ram_total_mb=65536, ram_free_mb=ram_free,
        gpus=[GPUInfo(name="RTX 4090", vram_total_mb=24576, vram_free_mb=vram_free,
                      driver_version=driver, cuda_version=cuda)],
        os_version="Win11", disk_free_gb=100, rpc_server_version="b4231",
        agent_version="0.1.0", boot_time_epoch=int(time.time()) - 3600,
    )


def _peer(**kw):
    defaults = dict(peer_id="p1", ip="10.0.0.2", rpc_port=50052, hostname="w1",
                    last_seen=time.time(), status="online", system=_sys(),
                    rpc_server_running=True, last_error=None)
    defaults.update(kw)
    return PeerRecord(**defaults)


def test_healthy_peer_no_alerts():
    p = _peer()
    result = engine.evaluate(p)
    assert result == []


def test_offline_triggers_alert():
    p = _peer(status="offline")
    result = engine.evaluate(p)
    codes = [a.code for a in result]
    assert "PEER_OFFLINE" in codes


def test_cuda_missing_triggers_alert():
    p = _peer(system=_sys(cuda=""))
    result = engine.evaluate(p)
    codes = [a.code for a in result]
    assert "CUDA_MISSING" in codes


def test_low_vram_triggers_warning():
    p = _peer(system=_sys(vram_free=512))
    result = engine.evaluate(p)
    codes = [a.code for a in result]
    assert "LOW_VRAM" in codes


def test_low_ram_triggers_warning():
    p = _peer(system=_sys(ram_free=1024))
    result = engine.evaluate(p)
    codes = [a.code for a in result]
    assert "LOW_RAM" in codes


def test_outdated_driver_triggers_warning():
    p = _peer(system=_sys(driver="531.10"))
    result = engine.evaluate(p)
    codes = [a.code for a in result]
    assert "DRIVER_OUTDATED" in codes


def test_rpc_not_running():
    p = _peer(rpc_server_running=False)
    result = engine.evaluate(p)
    codes = [a.code for a in result]
    assert "RPC_NOT_RUNNING" in codes


def test_last_error_surfaced():
    p = _peer(last_error="CUDA out of memory")
    result = engine.evaluate(p)
    codes = [a.code for a in result]
    assert "LAST_ERROR" in codes


if __name__ == "__main__":
    test_healthy_peer_no_alerts()
    test_offline_triggers_alert()
    test_cuda_missing_triggers_alert()
    test_low_vram_triggers_warning()
    test_low_ram_triggers_warning()
    test_outdated_driver_triggers_warning()
    test_rpc_not_running()
    test_last_error_surfaced()
    print("All alert tests passed.")
