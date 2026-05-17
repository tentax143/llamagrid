from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict

log = logging.getLogger(__name__)

DATA_DIR = r"C:\LlamaGrid"
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")


@dataclass
class LlamaServerArgs:
    ctx_size: int = 8192
    n_gpu_layers: int = 999
    threads: int = 8


@dataclass
class HostConfig:
    model_path: str = r"C:\models\Meta-Llama-3-70B-Instruct-Q4_K_M.gguf"
    model_dir: str = r"C:\models"
    llama_server_exe: str = r"C:\llama\llama-server.exe"
    port: int = 8080
    llama_server_port: int = 8081
    rpc_port: int = 50052
    max_peers: int = 30
    auth_token: str = field(default_factory=lambda: str(uuid.uuid4()))
    heartbeat_interval_sec: int = 30
    peer_offline_after_sec: int = 90
    auto_start_inference: bool = False
    llama_server_args: LlamaServerArgs = field(default_factory=LlamaServerArgs)


def load_config() -> HostConfig:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "logs"), exist_ok=True)

    if not os.path.isfile(CONFIG_PATH):
        cfg = HostConfig()
        _save(cfg)
        log.info("Created default config at %s", CONFIG_PATH)
        return cfg

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    args_data = data.pop("llama_server_args", {})
    args = LlamaServerArgs(**{k: v for k, v in args_data.items() if k in LlamaServerArgs.__dataclass_fields__})
    cfg = HostConfig(**{k: v for k, v in data.items() if k in HostConfig.__dataclass_fields__ and k != "llama_server_args"})
    cfg.llama_server_args = args
    return cfg


def save_config(cfg: HostConfig) -> None:
    _save(cfg)


def _save(cfg: HostConfig) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    d = asdict(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
