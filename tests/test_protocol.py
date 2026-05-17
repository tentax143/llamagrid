import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.protocol import (
    GPUInfo, SystemReport, RegisterRequest, RegisterResponse,
    HeartbeatRequest, HeartbeatResponse, AlertEvent, ModelInfo,
)
import time


def _gpu():
    return GPUInfo(name="RTX 4090", vram_total_mb=24576, vram_free_mb=18000,
                   driver_version="551.86", cuda_version="12.4")


def _sys():
    return SystemReport(
        hostname="test-pc", ip="10.0.0.1", cpu_model="i9", cpu_cores_physical=8,
        cpu_cores_logical=24, ram_total_mb=65536, ram_free_mb=32768,
        gpus=[_gpu()], os_version="Windows 11", disk_free_gb=100,
        rpc_server_version="b4231", agent_version="0.1.0",
        boot_time_epoch=int(time.time()) - 3600,
    )


def test_register_roundtrip():
    req = RegisterRequest(peer_id="abc", auth_token="tok", rpc_port=50052, system=_sys())
    data = req.model_dump()
    req2 = RegisterRequest.model_validate(data)
    assert req2.peer_id == "abc"
    assert req2.system.gpus[0].cuda_version == "12.4"


def test_heartbeat_roundtrip():
    req = HeartbeatRequest(peer_id="x", auth_token="t", system=_sys(),
                           rpc_server_running=True, last_error=None)
    data = req.model_dump()
    req2 = HeartbeatRequest.model_validate(data)
    assert req2.rpc_server_running is True
    assert req2.last_error is None


def test_heartbeat_response_defaults():
    resp = HeartbeatResponse(acknowledged=True)
    assert resp.command == "none"
    assert resp.payload is None


def test_alert_event():
    a = AlertEvent(peer_id="p1", severity="error", code="CUDA_MISSING",
                   message="No CUDA", ts_epoch=12345)
    assert a.severity == "error"


def test_model_info():
    m = ModelInfo(name="llama.gguf", path="/models/llama.gguf", size_gb=38.5, quant="Q4_K_M")
    assert m.quant == "Q4_K_M"


if __name__ == "__main__":
    test_register_roundtrip()
    test_heartbeat_roundtrip()
    test_heartbeat_response_defaults()
    test_alert_event()
    test_model_info()
    print("All protocol tests passed.")
