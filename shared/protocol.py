from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class GPUInfo(BaseModel):
    name: str
    vram_total_mb: int
    vram_free_mb: int
    driver_version: str
    cuda_version: str


class SystemReport(BaseModel):
    hostname: str
    ip: str
    cpu_model: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    ram_total_mb: int
    ram_free_mb: int
    gpus: list[GPUInfo]
    os_version: str
    disk_free_gb: int
    rpc_server_version: str
    agent_version: str
    boot_time_epoch: int


class RegisterRequest(BaseModel):
    peer_id: str
    auth_token: str
    rpc_port: int
    system: SystemReport


class RegisterResponse(BaseModel):
    accepted: bool
    assigned_slot: int | None = None
    host_version: str
    heartbeat_interval_sec: int
    rpc_server_latest_version: str
    reject_reason: str | None = None


class HeartbeatRequest(BaseModel):
    peer_id: str
    auth_token: str
    system: SystemReport
    rpc_server_running: bool
    last_error: str | None = None
    layers_assigned: int | None = None


class HeartbeatResponse(BaseModel):
    acknowledged: bool
    command: Literal["none", "restart_rpc", "update_rpc", "shutdown"] = "none"
    payload: dict | None = None


class AlertEvent(BaseModel):
    peer_id: str
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    ts_epoch: int


class ModelInfo(BaseModel):
    name: str
    path: str
    size_gb: float
    quant: str
