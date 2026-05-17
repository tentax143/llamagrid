from __future__ import annotations

import json
import logging
import os
import uuid

log = logging.getLogger(__name__)

DATA_DIR = r"C:\LlamaGrid"
CONFIG_PATH = os.path.join(DATA_DIR, "peer_config.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")

_DEFAULTS = {
    "host_ip": "",
    "host_port": 8080,
    "rpc_port": 50052,
    "llama_dir": r"C:\llama",
    "log_dir": LOG_DIR,
    "auth_token": "",
    "auto_start": True,
    "check_updates": True,
}

_STATE_KEYS = {
    "peer_id": "",
    "installed": False,
    "rpc_server_path": "",
    "nssm_path": r"C:\LlamaGrid\nssm.exe",
    "heartbeat_interval_sec": 30,
    "host_version": "",
}


class PeerConfig:
    def __init__(self, data: dict):
        # User-facing config
        self.host_ip: str = data.get("host_ip", "")
        self.host_port: int = int(data.get("host_port", 8080))
        self.rpc_port: int = int(data.get("rpc_port", 50052))
        self.llama_dir: str = data.get("llama_dir", r"C:\llama")
        self.log_dir: str = data.get("log_dir", LOG_DIR)
        self.auth_token: str = data.get("auth_token", "")
        self.auto_start: bool = bool(data.get("auto_start", True))
        self.check_updates: bool = bool(data.get("check_updates", True))

        # Runtime state
        self.peer_id: str = data.get("peer_id", "") or str(uuid.uuid4())
        self.installed: bool = bool(data.get("installed", False))
        self.rpc_server_path: str = data.get("rpc_server_path", "")
        self.nssm_path: str = data.get("nssm_path", r"C:\LlamaGrid\nssm.exe")
        self.heartbeat_interval_sec: int = int(data.get("heartbeat_interval_sec", 30))
        self.host_version: str = data.get("host_version", "")

    @property
    def host_base_url(self) -> str:
        return f"http://{self.host_ip}:{self.host_port}"

    @property
    def resolved_rpc_path(self) -> str:
        if self.rpc_server_path and os.path.isfile(self.rpc_server_path):
            return self.rpc_server_path
        candidate = os.path.join(self.llama_dir, "rpc-server.exe")
        if os.path.isfile(candidate):
            return candidate
        return os.path.join(DATA_DIR, "rpc", "rpc-server.exe")

    def to_dict(self) -> dict:
        return {
            "host_ip": self.host_ip,
            "host_port": self.host_port,
            "rpc_port": self.rpc_port,
            "llama_dir": self.llama_dir,
            "log_dir": self.log_dir,
            "auth_token": self.auth_token,
            "auto_start": self.auto_start,
            "check_updates": self.check_updates,
            "peer_id": self.peer_id,
            "installed": self.installed,
            "rpc_server_path": self.rpc_server_path,
            "nssm_path": self.nssm_path,
            "heartbeat_interval_sec": self.heartbeat_interval_sec,
            "host_version": self.host_version,
        }


def load_config() -> PeerConfig:
    _ensure_dirs()
    if not os.path.isfile(CONFIG_PATH):
        cfg = PeerConfig({})
        save_config(cfg)
        log.info("Created default peer config at %s", CONFIG_PATH)
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return PeerConfig(data)
    except Exception as e:
        log.error("Failed to load peer config: %s — using defaults", e)
        return PeerConfig({})


def save_config(cfg: PeerConfig) -> None:
    _ensure_dirs()
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, indent=2)
    except Exception as e:
        log.error("Failed to save peer config: %s", e)


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
