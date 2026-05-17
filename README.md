# LlamaGrid

Distribute a single large language model across up to 30 Windows machines on a private LAN. One machine runs the coordinator and serves inference; the others contribute RAM and VRAM through the [llama.cpp RPC backend](https://github.com/ggerganov/llama.cpp). No Docker, no Linux, no hardcoded IPs.

```
HOST MACHINE                          PEER MACHINES (up to 30)
┌─────────────────────────┐           ┌──────────────┐
│  Web dashboard  :8080   │◄──mDNS───►│ rpc-server   │
│  Flask REST API         │◄──HTTP───►│ agent        │
│  llama-server.exe       │◄──RPC ────│ :50052       │
│  model file (.gguf)     │           └──────────────┘
└─────────────────────────┘
```

The model file lives only on the host. Peers contribute compute — they never touch the weights.

---

## Requirements

| | Host | Peer |
|---|---|---|
| OS | Windows 10/11 x64 | Windows 10/11 x64 |
| GPU | Optional (recommended) | NVIDIA GPU + CUDA 12.1+ |
| RAM | 16 GB+ | 8 GB+ |
| Python | 3.10 (build only) | bundled inside .exe |
| llama.cpp | `C:\llama\llama-server.exe` | `C:\llama\rpc-server.exe` |

No Node.js, no npm, no WSL, no Docker. Python is bundled inside the installer — peers install nothing manually.

---

## Quick start (development)

### 1. Clone and install dependencies

```powershell
git clone <repo>
cd llamagrid
pip install -r requirements.txt
```

### 2. Run the host

```powershell
$env:PYTHONPATH = $PWD
python host\main.py
```

Dashboard opens at `http://localhost:8080`. The auth token is printed to the console and shown in the dashboard Settings tab.

### 3. Run a peer (on any other machine on the LAN)

```powershell
$env:PYTHONPATH = $PWD
python peer\main.py
```

The first-run wizard asks for the host IP and auth token, then installs `rpc-server.exe` as a Windows service. After that, the agent runs in the background and sends heartbeats every 30 seconds.

### 4. Test locally without real peers

```powershell
python dev_test.py
```

Spins up the host and two simulated peers, opens the dashboard, and runs API assertions. `worker-02` goes offline after 35 seconds to test dropout handling.

---

## Architecture

```
llamagrid/
├── shared/          Protocol types, mDNS helpers, system report, logging
├── host/            Flask API, peer coordinator, llama-server manager, alerts
│   └── webroot/     Single-file dashboard (HTML + CSS + JS, no npm)
├── peer/            Tkinter wizard, heartbeat agent, installer, NSSM service wrapper
├── build/           PyInstaller specs, NSIS scripts, fetch_binaries.py
├── scripts/         PowerShell helpers for dev and build
└── tests/           Unit tests for protocol and alert rules
```

### Control flow

1. **Peer starts** → mDNS announces `_llamagrid-peer._tcp.local.` → HTTP POST `/api/register`
2. **Host accepts** → adds peer to registry, triggers debounced `llama-server` restart with updated `--rpc` list
3. **Peer heartbeats** every 30 s → host updates stats, evaluates alert rules
4. **No heartbeat for 90 s** → host marks peer offline, restarts `llama-server` without it
5. **Dashboard polls** `/api/cluster`, `/api/peers`, `/api/alerts` every 5 s

### mDNS service types

| Type | Advertised by | Purpose |
|------|--------------|---------|
| `_llamagrid-host._tcp.local.` | Host | Peers find the host IP without configuration |
| `_llamagrid-peer._tcp.local.` | Each peer | Host gets hints about peer addresses |

mDNS is advisory only. The HTTP register call is the authoritative source of truth.

---

## Dashboard

Single HTML file served by the host — no build step, no CDN, works air-gapped.

- **Cluster bar** — live peer count, total/free VRAM, total RAM, current model, tokens/sec
- **Peer cards** — color coded green/yellow/red, VRAM and RAM bars, per-GPU detail, one-click restart
- **Inference panel** — prompt input, streaming output, model selector
- **Alerts tab** — active alerts with severity badges (CUDA missing, low VRAM, outdated driver, RPC crashed, peer offline)
- **Settings tab** — auth token display, llama-server start/stop/restart

---

## Alert rules

| Code | Severity | Trigger |
|------|----------|---------|
| `PEER_OFFLINE` | error | No heartbeat for > 90 s |
| `RPC_NOT_RUNNING` | error | `rpc_server_running = false` in heartbeat |
| `CUDA_MISSING` | error | GPU present but no CUDA runtime detected |
| `LAST_ERROR` | error | Peer forwarded a fatal log line |
| `LOW_VRAM` | warning | < 1 GB VRAM free on any GPU |
| `LOW_RAM` | warning | < 2 GB RAM free |
| `DISK_LOW` | warning | < 5 GB disk free |
| `DRIVER_OUTDATED` | warning | NVIDIA driver < 535 |
| `RPC_VERSION_DRIFT` | warning | rpc-server version ≠ pinned tag |

---

## Building installers

```powershell
# 1. Populate peer\bundled\ (copies from C:\llama, downloads NSSM)
powershell -ExecutionPolicy Bypass -File scripts\prepare_bundled.ps1

# 2. Build both executables
pyinstaller --clean --noconfirm --distpath build\dist\host build\host.spec
pyinstaller --clean --noconfirm --distpath build\dist\peer build\peer.spec

# 3. (Optional) Build NSIS installers — requires makensis in PATH
.\scripts\build_all.ps1 -Version 0.1.0 -SkipFetch

# Outputs
#   build\dist\host\llamagrid-host.exe
#   build\dist\peer\llamagrid-peer.exe
#   build\output\llamagrid-host-0.1.0-setup.exe
#   build\output\llamagrid-peer-0.1.0-setup.exe
```

The peer installer is a single `.exe` that:
- Detects existing `C:\llama\rpc-server.exe` and skips re-download
- Installs VC++ redistributable and checks CUDA
- Adds a Windows Firewall rule for port 50052
- Installs `LlamaGridRPC` and `LlamaGridAgent` as auto-start Windows services via NSSM

---

## Peer installer wizard

The wizard runs on first launch and has three screens:

1. **Welcome** — lists what will be installed
2. **Host configuration** — enter host IP and auth token (or auto-discover via mDNS)
3. **Installation progress** — live log of each step with pass/fail indicators

After installation the agent runs as a Windows service (`LlamaGridAgent`) and starts automatically on boot. The `LlamaGridRPC` service starts `rpc-server.exe` on port 50052.

---

## Configuration

**Host** — `C:\LlamaGrid\config.json`

```json
{
  "model_path": "C:/models/Meta-Llama-3-70B-Instruct-Q4_K_M.gguf",
  "model_dir":  "C:/models",
  "llama_server_exe": "C:/llama/llama-server.exe",
  "port": 8080,
  "rpc_port": 50052,
  "max_peers": 30,
  "auth_token": "<generated on first run>",
  "heartbeat_interval_sec": 30,
  "peer_offline_after_sec": 90
}
```

**Peer** — `C:\LlamaGrid\peer_config.json`

```json
{
  "host_ip": "172.16.71.183",
  "host_port": 8080,
  "rpc_port": 50052,
  "llama_dir": "C:\\llama",
  "auth_token": "<paste from host dashboard Settings tab>"
}
```

---

## REST API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/register` | Peer joins cluster |
| POST | `/api/heartbeat` | 30 s peer tick |
| GET | `/api/peers` | All peer records |
| GET | `/api/peers/<id>` | Single peer detail |
| POST | `/api/peers/<id>/restart` | Queue rpc-server restart |
| POST | `/api/peers/<id>/update` | Queue rpc-server update |
| POST | `/api/peers/<id>/forget` | Remove peer from registry |
| GET | `/api/cluster` | Aggregate stats |
| GET | `/api/models` | Scan host for .gguf files |
| POST | `/api/model/select` | Switch model, restart llama-server |
| POST | `/api/inference` | Streaming inference proxy (SSE) |
| GET | `/api/alerts` | Active alerts |
| GET | `/api/version` | Host version + auth token |
| POST | `/api/llama/start` | Start llama-server |
| POST | `/api/llama/stop` | Stop llama-server |
| POST | `/api/llama/restart` | Restart llama-server |

---

## Air-gapped / offline LANs

The app works without internet access after initial setup. To pre-seed the peer installer cache on machines with no outbound access:

```
C:\LlamaGrid\cache\
    vc_redist.x64.exe
    nssm.zip
    llama-b4231-bin-win-cuda-cu12.2.0-x64.zip
```

The installer checks this directory before attempting any download.

---

## Tech stack

| Component | Library |
|-----------|---------|
| Web server + API | Flask 3.0 |
| Wire types | Pydantic v2 |
| Peer/host discovery | zeroconf (mDNS) |
| System stats | psutil |
| Peer wizard GUI | tkinter |
| Windows service | NSSM 2.24 |
| Packaging | PyInstaller 6.7 |
| Installers | NSIS |
| Inference backend | llama.cpp b4231 |

---

## Version

`v0.1.0` — llama.cpp pinned to `b4231`
