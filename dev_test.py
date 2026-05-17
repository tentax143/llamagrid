"""
dev_test.py — Test the LlamaGrid host without real peers.

Spins up the host Flask app in a background thread, then simulates:
  1. Two fake peers registering
  2. Both peers sending heartbeats every 5 s
  3. One peer going offline (stops heartbeating after 30 s)
  4. The dashboard API endpoints returning expected data

Run: python dev_test.py [--no-browser]
"""
from __future__ import annotations

import argparse
import sys
import os
import threading
import time
import uuid
import webbrowser
import json
import requests

# Repo root on path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import shared.logging_setup as ls
ls.setup("host")

import logging
log = logging.getLogger("dev_test")

from host.config import load_config, save_config
from host.coordinator import Coordinator
from host.llama_manager import LlamaManager
from host.api import build_flask_app
from shared.protocol import GPUInfo, HeartbeatRequest, RegisterRequest, SystemReport
from shared.versioning import AGENT_VERSION, LLAMA_CPP_PINNED_TAG


# ── Fake peer factory ─────────────────────────────────────────────────

def fake_system(hostname: str, gpu_name: str = "NVIDIA GeForce RTX 4090") -> SystemReport:
    return SystemReport(
        hostname=hostname,
        ip="127.0.0.1",
        cpu_model="Intel Core i9-14900K",
        cpu_cores_physical=8,
        cpu_cores_logical=24,
        ram_total_mb=65536,
        ram_free_mb=32000,
        gpus=[
            GPUInfo(
                name=gpu_name,
                vram_total_mb=24576,
                vram_free_mb=18000,
                driver_version="551.86",
                cuda_version="12.4",
            )
        ],
        os_version="Windows 11 Pro 22631",
        disk_free_gb=250,
        rpc_server_version=f"build {LLAMA_CPP_PINNED_TAG}",
        agent_version=AGENT_VERSION,
        boot_time_epoch=int(time.time()) - 3600,
    )


class FakePeer:
    def __init__(self, base_url: str, auth_token: str, name: str, offline_after: float = 9999):
        self.base_url = base_url
        self.auth_token = auth_token
        self.name = name
        self.peer_id = str(uuid.uuid4())
        self.offline_after = offline_after
        self.start_time = time.time()
        self._running = True

    def register(self) -> bool:
        req = RegisterRequest(
            peer_id=self.peer_id,
            auth_token=self.auth_token,
            rpc_port=50052,
            system=fake_system(self.name),
        )
        try:
            r = requests.post(f"{self.base_url}/api/register", json=req.model_dump(), timeout=5)
            data = r.json()
            if data.get("accepted"):
                log.info("[%s] Registered (slot=%s)", self.name, data.get("assigned_slot"))
                return True
            log.warning("[%s] Registration rejected: %s", self.name, data.get("reject_reason"))
            return False
        except Exception as e:
            log.error("[%s] Registration error: %s", self.name, e)
            return False

    def heartbeat_loop(self) -> None:
        while self._running:
            elapsed = time.time() - self.start_time
            if elapsed > self.offline_after:
                log.info("[%s] Going offline (simulated dropout after %ds)", self.name, int(elapsed))
                break

            req = HeartbeatRequest(
                peer_id=self.peer_id,
                auth_token=self.auth_token,
                system=fake_system(self.name),
                rpc_server_running=True,
            )
            try:
                r = requests.post(f"{self.base_url}/api/heartbeat", json=req.model_dump(), timeout=5)
                if r.status_code == 200:
                    resp = r.json()
                    log.debug("[%s] Heartbeat OK, cmd=%s", self.name, resp.get("command"))
                else:
                    log.warning("[%s] Heartbeat %d", self.name, r.status_code)
            except Exception as e:
                log.debug("[%s] Heartbeat error: %s", self.name, e)

            time.sleep(5)

    def start(self) -> None:
        t = threading.Thread(target=self.heartbeat_loop, daemon=True, name=f"peer-{self.name}")
        t.start()

    def stop(self) -> None:
        self._running = False


# ── Test assertions ────────────────────────────────────────────────────

def run_assertions(base_url: str) -> None:
    log.info("=== Running API assertions ===")
    time.sleep(3)  # give peers time to register

    def get(path):
        return requests.get(f"{base_url}{path}", timeout=5).json()

    # /api/version
    v = get("/api/version")
    assert "host_version" in v, f"/api/version missing host_version: {v}"
    log.info("✓ /api/version OK — %s", v["host_version"])

    # /api/peers
    peers = get("/api/peers")
    assert len(peers) == 2, f"Expected 2 peers, got {len(peers)}"
    log.info("✓ /api/peers — %d peers registered", len(peers))

    # /api/cluster
    cluster = get("/api/cluster")
    assert cluster["peers_online"] == 2, f"Expected 2 online, got {cluster['peers_online']}"
    assert cluster["total_vram_mb"] > 0, "total_vram_mb should be > 0"
    log.info(
        "✓ /api/cluster — %d/%d online, VRAM %d MB",
        cluster["peers_online"], cluster["peers_total"], cluster["total_vram_mb"],
    )

    # /api/alerts
    alerts = get("/api/alerts")
    log.info("✓ /api/alerts — %d active alerts", len(alerts))

    # /api/models
    models = get("/api/models")
    log.info("✓ /api/models — %d model(s) found", len(models))

    # /api/peers/<id>/restart — queue a command
    peer_id = peers[0]["peer_id"]
    r = requests.post(f"{base_url}/api/peers/{peer_id}/restart", timeout=5)
    assert r.json().get("queued"), "Restart should be queued"
    log.info("✓ /api/peers/<id>/restart queued")

    log.info("=== All assertions passed ===")


# ── Main ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    parser.add_argument("--no-llama", action="store_true", default=True, help="Skip launching llama-server (default: True)")
    args = parser.parse_args()

    cfg = load_config()
    log.info("Host config loaded — auth_token=%s", cfg.auth_token)
    log.info("Model: %s", cfg.model_path)

    coord = Coordinator(cfg)
    coord.start()

    llama = LlamaManager(cfg, coord)
    # In dev_test we DON'T start llama-server unless the model + exe exist
    if not args.no_llama and os.path.isfile(cfg.llama_server_exe) and os.path.isfile(cfg.model_path):
        llama.start()
        log.info("llama-server started")
    else:
        log.info("Skipping llama-server start (model or exe not found, or --no-llama)")

    app = build_flask_app(coord, llama, cfg)

    base_url = f"http://localhost:{cfg.port}"

    # Start Flask in background
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=cfg.port, threaded=True, use_reloader=False),
        daemon=True,
        name="flask",
    )
    flask_thread.start()
    time.sleep(1)

    # Spawn fake peers
    peers = [
        FakePeer(base_url, cfg.auth_token, "worker-01", offline_after=9999),
        FakePeer(base_url, cfg.auth_token, "worker-02", offline_after=35),  # goes offline after 35 s
    ]

    for p in peers:
        p.register()
        p.start()

    # Open browser
    if not args.no_browser:
        time.sleep(0.5)
        webbrowser.open(base_url)
        log.info("Dashboard opened: %s", base_url)

    # Run assertions in background
    def assert_thread():
        try:
            run_assertions(base_url)
        except AssertionError as e:
            log.error("ASSERTION FAILED: %s", e)
        except Exception as e:
            log.error("Assertion error: %s", e)

    threading.Thread(target=assert_thread, daemon=True).start()

    log.info("Dev server running at %s — Ctrl+C to stop", base_url)
    log.info("worker-02 will go offline in ~35s to test peer-dropout handling")

    try:
        while True:
            time.sleep(5)
            cluster = requests.get(f"{base_url}/api/cluster", timeout=3).json()
            log.info(
                "Cluster: %d/%d online | VRAM free: %d MB | tok/s: %.1f",
                cluster["peers_online"],
                cluster["peers_total"],
                cluster["free_vram_mb"],
                cluster["tokens_per_sec"],
            )
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        for p in peers:
            p.stop()
        llama.stop()
        coord.stop()


if __name__ == "__main__":
    main()
