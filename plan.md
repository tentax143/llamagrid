# LlamaGrid — Distributed LLM Inference Cluster for Windows

A Windows-only distributed inference application that splits a single large
language model across up to 30 machines on a private LAN, using the
`llama.cpp` RPC backend. One machine runs the coordinator, web dashboard,
and `llama-server.exe`; the others run `rpc-server.exe` as a Windows
service and contribute RAM/VRAM to the pool. The model file lives only on
the host.

This document is the implementation contract: every component, every API
endpoint, every protocol, every build command. It is meant to be read
end-to-end before a single line of code is written.

---

## 0. Glossary

| Term | Meaning |
|------|---------|
| **Host** | The single coordinator machine. Runs the Flask backend, the dashboard, and `llama-server.exe`. Holds the `.gguf` model file. |
| **Peer** | A worker machine (up to 30). Runs `rpc-server.exe` as a Windows service. Holds no model file. |
| **Coordinator** | The Python process on the host that discovers peers, manages state, and launches `llama-server.exe`. |
| **mDNS** | Multicast DNS / Zeroconf. Used for zero-config peer↔host discovery on LAN. |
| **NSSM** | The Non-Sucking Service Manager. Wraps `rpc-server.exe` as a Windows service on peers. |
| **rpc-server** | `llama.cpp`'s RPC backend binary. Exposes a peer's compute to a remote `llama-server`. |
| **gguf** | The model file format `llama.cpp` consumes. |

---

## 1. Top-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                              HOST MACHINE                            │
│                                                                      │
│  ┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐  │
│  │  Dashboard  │◄──►│  Flask REST API  │◄──►│   Coordinator core  │  │
│  │ (HTML/JS)   │    │  :8080           │    │  - mDNS browser     │  │
│  └─────────────┘    └──────────────────┘    │  - peer registry    │  │
│                              ▲              │  - llama-server mgr │  │
│                              │              │  - alert engine     │  │
│                              │              └─────────┬───────────┘  │
│                              │                        │              │
│                              │                        ▼              │
│                              │              ┌─────────────────────┐  │
│                              │              │ llama-server.exe    │  │
│                              │              │  --rpc p1,p2,p3,..  │  │
│                              │              │  --model X.gguf     │  │
│                              │              └──────────┬──────────┘  │
│                              │                         │             │
│                              │       mDNS announce     │ RPC :50052  │
│                              │       (_llamagrid._tcp) │             │
└──────────────────────────────┼─────────────────────────┼─────────────┘
                               │                         │
            ┌──────────────────┴──────┬──────────────────┴────────┐
            ▼                         ▼                           ▼
   ┌────────────────┐        ┌────────────────┐         ┌────────────────┐
   │   PEER #1      │        │   PEER #2      │   ...   │   PEER #30     │
   │                │        │                │         │                │
   │ rpc-server.exe │        │ rpc-server.exe │         │ rpc-server.exe │
   │  (NSSM svc)    │        │  (NSSM svc)    │         │  (NSSM svc)    │
   │                │        │                │         │                │
   │ llamagrid-     │        │ llamagrid-     │         │ llamagrid-     │
   │ agent.exe      │        │ agent.exe      │         │ agent.exe      │
   │  - mDNS adv    │        │  - mDNS adv    │         │  - mDNS adv    │
   │  - heartbeat   │        │  - heartbeat   │         │  - heartbeat   │
   │  - autoupdate  │        │  - autoupdate  │         │  - autoupdate  │
   └────────────────┘        └────────────────┘         └────────────────┘
```

Two data planes:

1. **Control plane** (HTTP + mDNS): registration, heartbeat, alerts, remote
   restart, model selection. JSON over Flask.
2. **Data plane** (RPC, port 50052): the actual tensor traffic between
   `llama-server.exe` on the host and `rpc-server.exe` on each peer. This
   is `llama.cpp`'s own protocol — we do not touch it.

---

## 2. Repository layout

```
llamagrid/
├── plan.md                     ← this file
├── README.md
├── LICENSE
├── .gitignore
│
├── shared/                     ← code used by both host and peer
│   ├── __init__.py
│   ├── protocol.py             ← TypedDicts / dataclasses for HTTP payloads
│   ├── mdns.py                 ← thin wrapper over zeroconf
│   ├── sysreport.py            ← system info collection (CPU/RAM/GPU/CUDA)
│   ├── versioning.py           ← version constants + compatibility check
│   └── logging_setup.py
│
├── host/
│   ├── __init__.py
│   ├── main.py                 ← entrypoint launched by host.exe
│   ├── coordinator.py          ← peer registry, lifecycle, alerts
│   ├── llama_manager.py        ← spawn/kill/restart llama-server.exe
│   ├── api.py                  ← Flask blueprints
│   ├── alerts.py               ← rule engine
│   ├── model_scanner.py        ← finds .gguf files on host
│   ├── config.py               ← reads/writes config.json
│   ├── webroot/
│   │   ├── index.html
│   │   ├── app.js
│   │   ├── style.css
│   │   └── assets/
│   │       └── logo.svg
│   └── bundled/
│       └── llama-server/       ← llama.cpp CUDA Windows build, bundled
│           ├── llama-server.exe
│           ├── ggml-cuda.dll
│           └── ...
│
├── peer/
│   ├── __init__.py
│   ├── main.py                 ← entrypoint launched by peer.exe
│   ├── gui.py                  ← tkinter first-run wizard
│   ├── agent.py                ← long-running daemon: heartbeat + mDNS
│   ├── installer.py            ← CUDA / VC++ / rpc-server / firewall checks
│   ├── service.py              ← NSSM install/uninstall/restart
│   ├── updater.py              ← rpc-server self-update
│   ├── host_discovery.py       ← mDNS browser for host + HTTP fallback
│   └── bundled/
│       ├── nssm.exe
│       └── rpc-server/         ← initial llama.cpp RPC build
│           ├── rpc-server.exe
│           └── ggml-cuda.dll
│
├── build/
│   ├── host.spec               ← PyInstaller spec for host
│   ├── peer.spec               ← PyInstaller spec for peer
│   ├── host_installer.nsi      ← NSIS script for host installer
│   ├── peer_installer.nsi      ← NSIS script for peer installer
│   ├── fetch_binaries.py       ← downloads llama.cpp + NSSM into bundled/
│   ├── icon_host.ico
│   ├── icon_peer.ico
│   └── version_info.txt
│
├── scripts/
│   ├── dev_run_host.ps1        ← run host from source for development
│   ├── dev_run_peer.ps1        ← run peer from source for development
│   ├── build_all.ps1           ← end-to-end installer build
│   └── clean.ps1
│
└── tests/
    ├── test_protocol.py
    ├── test_sysreport.py
    ├── test_alerts.py
    ├── test_mdns.py
    └── integration/
        └── test_two_peers.py
```

### .gitignore highlights

```
__pycache__/
build/dist/
build/output/
host/bundled/llama-server/
peer/bundled/rpc-server/
peer/bundled/nssm.exe
*.spec.bak
*.log
config.local.json
```

Bundled binaries are produced by `build/fetch_binaries.py` at build time —
they're not checked into git.

---

## 3. Shared module (`shared/`)

### 3.1 `protocol.py`

Single source of truth for every wire-format payload. Pydantic v2 models
because we want validation on the host side without writing schemas twice.

```python
class GPUInfo(BaseModel):
    name: str                  # "NVIDIA GeForce RTX 4090"
    vram_total_mb: int
    vram_free_mb: int
    driver_version: str        # "551.86"
    cuda_version: str          # "12.4"

class SystemReport(BaseModel):
    hostname: str
    ip: str
    cpu_model: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    ram_total_mb: int
    ram_free_mb: int
    gpus: list[GPUInfo]
    os_version: str            # "Windows 11 Pro 22631"
    disk_free_gb: int
    rpc_server_version: str    # e.g. "b4231"
    agent_version: str         # llamagrid agent semver
    boot_time_epoch: int       # for uptime

class RegisterRequest(BaseModel):
    peer_id: str               # stable UUID4 generated on first run
    auth_token: str
    rpc_port: int              # default 50052
    system: SystemReport

class RegisterResponse(BaseModel):
    accepted: bool
    assigned_slot: int | None
    host_version: str
    heartbeat_interval_sec: int
    rpc_server_latest_version: str
    reject_reason: str | None

class HeartbeatRequest(BaseModel):
    peer_id: str
    auth_token: str
    system: SystemReport
    rpc_server_running: bool
    last_error: str | None
    layers_assigned: int | None    # populated by host echo, peer keeps for display

class HeartbeatResponse(BaseModel):
    acknowledged: bool
    command: Literal["none", "restart_rpc", "update_rpc", "shutdown"]
    payload: dict | None

class AlertEvent(BaseModel):
    peer_id: str
    severity: Literal["info", "warning", "error"]
    code: str                  # e.g. "CUDA_MISSING"
    message: str
    ts_epoch: int
```

### 3.2 `mdns.py`

Wraps `zeroconf` so neither side touches the library directly.

```python
SERVICE_TYPE_PEER = "_llamagrid-peer._tcp.local."
SERVICE_TYPE_HOST = "_llamagrid-host._tcp.local."

def advertise_peer(peer_id, port, properties): ...
def advertise_host(port, properties): ...
def browse_peers(on_added, on_removed): ...
def browse_host(on_found): ...
```

Properties packed into the TXT record (UTF-8 key/value bytes, ≤ ~200 B):

| Key | Value |
|-----|-------|
| `id` | peer UUID |
| `ver` | agent version |
| `rpc` | rpc-server version |
| `gpu` | first GPU short name |
| `vram` | VRAM total MB |

### 3.3 `sysreport.py`

`build_system_report() -> SystemReport`. Uses:

- `psutil` for CPU, RAM, disk, boot time.
- `platform` + Windows registry (`winreg`) for OS version detail.
- `nvidia-smi --query-gpu=... --format=csv,noheader` parsed via subprocess
  for GPU name, VRAM total/free, driver version. CUDA toolkit version from
  `nvcc --version` if present, else from registry
  `HKLM\SOFTWARE\NVIDIA Corporation\GPU Computing Toolkit\CUDA`.
- WMI fallback (`wmic cpu get name`) only if `psutil` is missing fields.

Functions return `GPUInfo(...)` with `cuda_version=""` if CUDA not found —
that signal feeds the alert engine.

### 3.4 `versioning.py`

```python
AGENT_VERSION = "0.1.0"
HOST_VERSION = "0.1.0"
PROTOCOL_VERSION = 1
MIN_COMPATIBLE_AGENT = "0.1.0"
LLAMA_CPP_PINNED_TAG = "b4231"  # bumped intentionally
```

### 3.5 `logging_setup.py`

Rotating file handler at `%PROGRAMDATA%\LlamaGrid\logs\{host,peer}.log`,
10 MB × 5 files, plus a console handler at INFO.

---

## 4. Host application

### 4.1 `host/main.py`

```python
def main():
    cfg = load_config()
    setup_logging("host")
    coord = Coordinator(cfg)
    llama = LlamaManager(cfg, coord)
    app = build_flask_app(coord, llama, cfg)

    coord.start_mdns_browser()
    coord.start_mdns_advertiser()
    coord.start_alert_loop()
    app.run(host="0.0.0.0", port=cfg.port, threaded=True)
```

### 4.2 `host/config.py`

`config.json` lives at `%PROGRAMDATA%\LlamaGrid\config.json`. Schema:

```json
{
  "model_path": "C:/models/llama-3-70b-q4.gguf",
  "model_dir": "C:/models",
  "port": 8080,
  "max_peers": 30,
  "auth_token": "auto-generated-uuid-on-first-run",
  "rpc_port": 50052,
  "heartbeat_interval_sec": 30,
  "peer_offline_after_sec": 90,
  "llama_server_args": {
    "ctx_size": 8192,
    "n_gpu_layers": 999,
    "threads": 8
  },
  "auto_start_inference": false
}
```

`load_config()` creates the file with safe defaults if missing.

### 4.3 `host/coordinator.py`

Holds the **peer registry** — an in-memory dict keyed by `peer_id`:

```python
@dataclass
class PeerRecord:
    peer_id: str
    ip: str
    rpc_port: int
    hostname: str
    last_seen: float                 # epoch
    status: Literal["online","offline","warning","error"]
    system: SystemReport
    last_error: str | None
    layers_assigned: int | None
    alerts: list[AlertEvent]
    rpc_server_running: bool
    discovered_via: Literal["mdns","http"]
```

Responsibilities:

- `register(req: RegisterRequest, source_ip: str) -> RegisterResponse`
- `heartbeat(req: HeartbeatRequest) -> HeartbeatResponse`
- `sweep()` — runs every 10 s, marks peers offline after
  `peer_offline_after_sec`.
- `start_mdns_browser()` — uses `shared.mdns.browse_peers`; an mDNS
  appearance does *not* register a peer alone — the peer still has to HTTP
  POST to `/api/register`. mDNS is purely a "you can reach the host at X"
  signal for the peer, and a "this peer's address may have changed"
  signal for the host.
- `start_mdns_advertiser()` — publishes the host's service so peers can
  resolve the host's IP without configuration.
- `pending_commands: dict[peer_id, list[Command]]` — queues remote restarts
  and updates; flushed in the next `HeartbeatResponse`.
- Persists registry to `%PROGRAMDATA%\LlamaGrid\peers.json` every 60 s so a
  host restart preserves view continuity (peers still re-register on their
  next heartbeat).

### 4.4 `host/llama_manager.py`

Single class that owns the `llama-server.exe` process.

```python
class LlamaManager:
    def build_command(self) -> list[str]:
        peers = self.coord.online_peers_for_rpc()
        rpc_arg = ",".join(f"{p.ip}:{p.rpc_port}" for p in peers)
        return [
            str(self.exe_path),
            "--model", self.cfg.model_path,
            "--rpc", rpc_arg,
            "--ctx-size", str(self.cfg.llama_server_args["ctx_size"]),
            "--n-gpu-layers", str(self.cfg.llama_server_args["n_gpu_layers"]),
            "--threads", str(self.cfg.llama_server_args["threads"]),
            "--host", "127.0.0.1",
            "--port", "8081",
        ]

    def start(self): ...
    def stop(self): ...
    def restart_on_topology_change(self): ...   # debounced 5 s
    def is_running(self) -> bool: ...
    def tokens_per_second(self) -> float: ...   # tail llama-server stdout
```

Topology-change rule: whenever a peer transitions online↔offline, schedule
a debounced restart of `llama-server.exe` 5 s later. This avoids
restart-storms when a switch hiccups. Restart drains in-flight requests
with a 30 s grace period.

### 4.5 `host/alerts.py`

Pure-function rule engine evaluated on every heartbeat:

| Code | Severity | Condition |
|------|----------|-----------|
| `PEER_OFFLINE` | error | `now - last_seen > peer_offline_after_sec` |
| `LOW_VRAM` | warning | any GPU `vram_free_mb < 1024` |
| `LOW_RAM` | warning | `ram_free_mb < 2048` |
| `CUDA_MISSING` | error | no GPU has non-empty `cuda_version` |
| `DRIVER_OUTDATED` | warning | `driver_version` major < 535 |
| `RPC_NOT_RUNNING` | error | heartbeat `rpc_server_running == False` |
| `RPC_VERSION_DRIFT` | warning | peer rpc-server version != pinned |
| `DISK_LOW` | warning | `disk_free_gb < 5` |
| `LAST_ERROR` | error | `last_error` field non-null |

Alerts are deduped on `(peer_id, code)` and timestamped on first occurrence
and last-seen. Cleared automatically when the condition stops holding.

### 4.6 `host/model_scanner.py`

`scan(model_dir: str) -> list[ModelInfo]` — recursively walks the
configured model directory for `*.gguf`, returns name, path, size in GB,
and quantization tag parsed from filename (e.g. `Q4_K_M`).

### 4.7 `host/api.py` — REST endpoints

All endpoints prefixed `/api`. JSON in/out. Auth: requests from peers must
include `auth_token` matching `config.auth_token`. Browser endpoints are
unauthenticated but bound to host-local network — operators are expected
to firewall port 8080 if needed.

| Method | Path | Body / Query | Purpose |
|--------|------|--------------|---------|
| POST | `/api/register` | `RegisterRequest` | Peer joins the cluster. Returns `RegisterResponse`. Source IP overrides claimed IP if they disagree. |
| POST | `/api/heartbeat` | `HeartbeatRequest` | 30 s tick from peer. Returns `HeartbeatResponse` with any queued command. |
| POST | `/api/peer/error` | `{peer_id, code, message}` | Out-of-band error report (e.g. CUDA load failed). |
| GET | `/api/peers` | — | Full peer list for dashboard. |
| GET | `/api/peers/<peer_id>` | — | Full record + alert history. |
| POST | `/api/peers/<peer_id>/restart` | — | Queue a `restart_rpc` command. |
| POST | `/api/peers/<peer_id>/update` | — | Queue an `update_rpc` command. |
| GET | `/api/cluster` | — | Aggregate: peer counts, total/free RAM, total/free VRAM, current model, tokens/sec, llama-server status. |
| GET | `/api/models` | — | List of `.gguf` files found on host. |
| POST | `/api/model/select` | `{path}` | Update `config.model_path` and restart `llama-server.exe`. |
| POST | `/api/inference` | `{prompt, max_tokens, temperature}` | Proxies to `llama-server.exe` at `127.0.0.1:8081`. SSE streaming. |
| GET | `/api/alerts` | — | Active alerts across all peers. |
| GET | `/api/version` | — | Host version + protocol version + pinned rpc-server version. |
| GET | `/` | — | Serves `webroot/index.html`. |
| GET | `/<path>` | — | Serves static dashboard assets. |

### 4.8 Heartbeat protocol — detailed

**Cadence**: peer sends every `heartbeat_interval_sec` (default 30 s).
First heartbeat is sent ≤ 5 s after a successful `/api/register`.

**Peer → host:**

```http
POST /api/heartbeat HTTP/1.1
Content-Type: application/json
X-LlamaGrid-Protocol: 1

{
  "peer_id": "a4f3c2d1-...",
  "auth_token": "...",
  "system": { ... full SystemReport ... },
  "rpc_server_running": true,
  "last_error": null,
  "layers_assigned": 18
}
```

**Host → peer:**

```json
{
  "acknowledged": true,
  "command": "restart_rpc",
  "payload": null
}
```

`command` values:

- `"none"` — no action.
- `"restart_rpc"` — peer stops and restarts the rpc-server service.
- `"update_rpc"` — peer downloads pinned rpc-server build and restarts.
  `payload = {"download_url": "...", "sha256": "...", "version": "b4231"}`.
- `"shutdown"` — peer stops the rpc-server service and exits agent loop.

**Failure handling on peer:**

- Connection refused / DNS fail → fall back to mDNS host re-resolution.
- 401 (bad auth) → log, retry every 5 minutes.
- 5xx → exponential backoff 1 s → 60 s capped.
- Three consecutive failures → re-run host discovery from scratch.

**Failure handling on host:**

- No heartbeat for `peer_offline_after_sec` (90 s) → peer status `offline`,
  triggers debounced llama-server restart.

---

## 5. Peer application

### 5.1 `peer/main.py`

```python
def main():
    setup_logging("peer")
    state = load_or_create_peer_state()   # %PROGRAMDATA%\LlamaGrid\peer.json

    if not state.installed or not is_service_installed():
        gui.run_first_run_wizard(state)   # blocks until done

    Agent(state).run()                    # never returns
```

`peer.json` stores: `peer_id` (UUID4 created once), host IP/host (last
known good), auth token (set from wizard or auto-fetched), rpc-server
version on disk.

### 5.2 `peer/gui.py` — first-run wizard

A single tkinter window. The wizard has three screens shown sequentially:

1. **Welcome** — version, license link, "Next".
2. **Host configuration**:
   - Radio: "Auto-discover host on LAN (recommended)" / "Enter host IP".
   - Text fields for IP and auth token (token can also be left blank for a
     setup where the host is in open-join mode).
   - "Test connection" button — does an HTTP GET to `/api/version`.
3. **Installation progress**:
   - Live log view backed by `installer.run_all_checks()` (see § 5.4).
   - Steps shown with checkmarks: VC++ → CUDA → llama.cpp rpc-server →
     Firewall rule → NSSM service install → Initial registration.
   - "Finish" enabled when all green; "View logs" opens the log file.

The wizard never re-runs once installation is complete — the agent
service starts on boot from then on. Re-running `peer.exe` while
installed shows a 4th "Status" screen instead (service running? last
heartbeat? "Open dashboard" button that launches the browser to the host's
IP).

### 5.3 `peer/host_discovery.py`

```python
def discover_host(timeout_sec=15) -> HostEndpoint | None:
    # 1. Try cached host from peer.json with /api/version probe (2 s).
    # 2. Try mDNS browse for _llamagrid-host._tcp.local.
    # 3. Return None — caller surfaces error to GUI / retries later.
```

The agent never gives up; on registration failure it falls back to a slow
mDNS poll every 30 s.

### 5.4 `peer/installer.py`

Each check is idempotent and returns `(ok: bool, message: str)`.

| Step | Detection | Action if missing |
|------|-----------|-------------------|
| **VC++ Redist 2015–2022 x64** | Registry `HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64\Installed` | Download `VC_redist.x64.exe` from `https://aka.ms/vs/17/release/vc_redist.x64.exe`, silent install `/install /quiet /norestart`. |
| **CUDA 12.1+** | `nvcc --version` exit 0 OR registry `HKLM\SOFTWARE\NVIDIA Corporation\GPU Computing Toolkit\CUDA\v12.1` | Download `cuda_12.1.1_531.14_windows.exe` from NVIDIA (URL pinned in `versioning.py`), silent install with `-s nvcc_12.1 cudart_12.1`. ~3 GB. Warn the user about disk + reboot. |
| **NVIDIA driver ≥ 535** | `nvidia-smi --query-gpu=driver_version` | Skip auto-install — instruct user. (Driver upgrades are too risky to do silently.) |
| **rpc-server.exe** | File at `%PROGRAMDATA%\LlamaGrid\rpc\rpc-server.exe` AND `--version` returns pinned tag | Download `llama-bin-win-cuda12-x64.zip` from `https://github.com/ggerganov/llama.cpp/releases/tag/{LLAMA_CPP_PINNED_TAG}`, extract to `%PROGRAMDATA%\LlamaGrid\rpc\`. |
| **Firewall rule** | `netsh advfirewall firewall show rule name=LlamaGrid-RPC` | `netsh advfirewall firewall add rule name="LlamaGrid-RPC" dir=in action=allow protocol=TCP localport=50052 program="%PROGRAMDATA%\LlamaGrid\rpc\rpc-server.exe"` |
| **Windows service** | `sc query LlamaGridRPC` | Install via NSSM — see § 5.5. |
| **Agent service** | `sc query LlamaGridAgent` | Install peer agent itself as a separate Windows service via NSSM. |

All downloads are streamed to `%TEMP%\llamagrid-dl\` with sha256
verification against constants in `versioning.py`. If the host is on an
isolated LAN, an admin can pre-seed `%PROGRAMDATA%\LlamaGrid\cache\` —
`installer.py` checks that directory first.

### 5.5 `peer/service.py` — NSSM service setup

Two services are installed: one for `rpc-server.exe` and one for the
Python agent.

```powershell
# RPC backend service
nssm install LlamaGridRPC "%PROGRAMDATA%\LlamaGrid\rpc\rpc-server.exe" `
    -H 0.0.0.0 -p 50052
nssm set LlamaGridRPC AppDirectory "%PROGRAMDATA%\LlamaGrid\rpc"
nssm set LlamaGridRPC AppStdout    "%PROGRAMDATA%\LlamaGrid\logs\rpc.out.log"
nssm set LlamaGridRPC AppStderr    "%PROGRAMDATA%\LlamaGrid\logs\rpc.err.log"
nssm set LlamaGridRPC AppRotateFiles 1
nssm set LlamaGridRPC AppRotateBytes 10485760
nssm set LlamaGridRPC Start        SERVICE_AUTO_START
nssm set LlamaGridRPC ObjectName   LocalSystem
nssm set LlamaGridRPC AppRestartDelay 5000
nssm set LlamaGridRPC AppExit Default Restart
nssm start LlamaGridRPC

# Agent service
nssm install LlamaGridAgent "%PROGRAMFILES%\LlamaGrid\peer.exe" --service
nssm set LlamaGridAgent AppDirectory "%PROGRAMFILES%\LlamaGrid"
nssm set LlamaGridAgent Start SERVICE_AUTO_START
nssm start LlamaGridAgent
```

`peer.exe --service` skips the wizard and goes straight to `Agent.run()`.

### 5.6 `peer/agent.py`

```python
class Agent:
    def run(self):
        mdns.advertise_peer(self.peer_id, RPC_PORT, self._txt_props())
        self._register_with_retry()
        while True:
            try:
                resp = self._send_heartbeat()
                self._apply_command(resp)
            except Exception as e:
                log.warning("heartbeat failed: %s", e)
                self._maybe_rediscover_host()
            time.sleep(self.heartbeat_interval)

    def _apply_command(self, resp):
        match resp.command:
            case "restart_rpc": service.restart("LlamaGridRPC")
            case "update_rpc":  updater.update(resp.payload); service.restart("LlamaGridRPC")
            case "shutdown":    service.stop("LlamaGridRPC"); sys.exit(0)
            case "none":        pass
```

The agent also tails `rpc.err.log` for fatal CUDA messages
(`out of memory`, `CUDA error`, `driver mismatch`) and forwards them to
`/api/peer/error` in real time, independent of the heartbeat cycle.

### 5.7 `peer/updater.py`

`update(payload)` — downloads `payload["download_url"]`, verifies sha256,
stops `LlamaGridRPC`, swaps the binary in `%PROGRAMDATA%\LlamaGrid\rpc\`
(via rename-then-replace so a failure leaves the old binary intact),
starts the service again. Reports outcome in next heartbeat's
`last_error` (null on success).

---

## 6. mDNS announcement format

Both sides use `python-zeroconf`. Service registrations:

**Host:**

```
type:     _llamagrid-host._tcp.local.
name:     <hostname>._llamagrid-host._tcp.local.
port:     8080
txt:      ver=0.1.0 proto=1 token_required=true
```

**Peer:**

```
type:     _llamagrid-peer._tcp.local.
name:     <peer_id-short>._llamagrid-peer._tcp.local.
port:     50052
txt:      id=<full uuid> ver=0.1.0 rpc=b4231 gpu=RTX_4090 vram=24576
```

The host's mDNS browser uses peer announcements **only as hints** —
authoritative truth is the HTTP register call. This means an attacker on
the LAN can't poison the registry just by advertising a name.

---

## 7. Dashboard frontend (`host/webroot/`)

### 7.1 `index.html` structure

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LlamaGrid</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header id="cluster-bar">
    <span id="peer-count">0/0 peers</span>
    <span id="total-ram">RAM: 0 / 0 GB</span>
    <span id="total-vram">VRAM: 0 / 0 GB</span>
    <span id="current-model">Model: —</span>
    <span id="tokens-per-sec">— tok/s</span>
    <span id="llama-status" class="status-dot"></span>
  </header>

  <nav id="tabs">
    <button data-tab="peers" class="active">Peers</button>
    <button data-tab="inference">Inference</button>
    <button data-tab="alerts">Alerts</button>
    <button data-tab="settings">Settings</button>
  </nav>

  <main>
    <section id="tab-peers">
      <div id="peer-grid"></div>          <!-- peer cards injected here -->
    </section>

    <section id="tab-inference" hidden>
      <div id="model-selector"></div>
      <textarea id="prompt" placeholder="Enter prompt..."></textarea>
      <div id="inference-controls">
        <label>Max tokens <input id="max-tokens" type="number" value="512"></label>
        <label>Temp <input id="temperature" type="number" step="0.1" value="0.7"></label>
        <button id="generate">Generate</button>
        <button id="stop" hidden>Stop</button>
      </div>
      <pre id="output"></pre>
    </section>

    <section id="tab-alerts" hidden>
      <ul id="alert-list"></ul>
    </section>

    <section id="tab-settings" hidden>
      <form id="settings-form">…</form>
    </section>
  </main>

  <div id="peer-modal" hidden></div>      <!-- click peer for details -->

  <script src="app.js"></script>
</body>
</html>
```

### 7.2 `app.js` skeleton

```js
const POLL_MS = 5000;

async function refresh() {
  const [cluster, peers, alerts] = await Promise.all([
    fetch('/api/cluster').then(r => r.json()),
    fetch('/api/peers').then(r => r.json()),
    fetch('/api/alerts').then(r => r.json()),
  ]);
  renderClusterBar(cluster);
  renderPeerGrid(peers);
  renderAlertBadges(alerts);
}

function renderPeerGrid(peers) {
  const grid = document.getElementById('peer-grid');
  grid.innerHTML = '';
  for (const p of peers) {
    grid.appendChild(makePeerCard(p));
  }
}

function makePeerCard(p) {
  // returns a div with:
  //   - colored top border (green/yellow/red)
  //   - hostname + IP
  //   - GPU name + VRAM bar
  //   - RAM bar
  //   - CPU % (from heartbeat)
  //   - warning chips: NO_CUDA, LOW_VRAM, OUTDATED_DRIVER, RPC_DOWN
  //   - "Restart" button → POST /api/peers/{id}/restart
  //   - clicking the card opens #peer-modal with the full SystemReport
}

async function generate() {
  const res = await fetch('/api/inference', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt: prompt.value,
      max_tokens: +maxTokens.value,
      temperature: +temperature.value,
    }),
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  output.textContent = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    output.textContent += decoder.decode(value, { stream: true });
  }
}

setInterval(refresh, POLL_MS);
refresh();
```

### 7.3 Color-coding rules

| Status | Trigger |
|--------|---------|
| **green** | online, no warnings, rpc-server running |
| **yellow** | online but at least one `warning`-severity alert |
| **red** | offline OR any `error`-severity alert |
| **gray** | recently registered, no heartbeat yet |

### 7.4 Peer detail modal

Shows everything from `SystemReport` plus alert history with timestamps,
last 50 log lines from rpc-server (fetched on demand from the host, which
caches a tail from the peer's most recent error reports), and three
buttons: **Restart RPC**, **Update RPC**, **Forget peer** (removes from
registry — peer will simply re-register on next heartbeat unless its
service is stopped).

---

## 8. Build pipeline

### 8.1 `build/fetch_binaries.py`

Run once before building. Idempotent — skips downloads already present
with matching sha256.

```python
LLAMA_CPP_URL = "https://github.com/ggerganov/llama.cpp/releases/download/b4231/llama-b4231-bin-win-cuda-cu12.2.0-x64.zip"
LLAMA_CPP_SHA = "..."
NSSM_URL      = "https://nssm.cc/release/nssm-2.24.zip"
NSSM_SHA      = "..."

def main():
    fetch_and_extract(LLAMA_CPP_URL, LLAMA_CPP_SHA, "host/bundled/llama-server", keep=["llama-server.exe","*.dll"])
    fetch_and_extract(LLAMA_CPP_URL, LLAMA_CPP_SHA, "peer/bundled/rpc-server",  keep=["rpc-server.exe","*.dll"])
    fetch_and_extract(NSSM_URL,      NSSM_SHA,      "peer/bundled/",            keep=["nssm-2.24/win64/nssm.exe"], flatten=True)
```

### 8.2 `build/host.spec` (PyInstaller)

```python
# host.spec
block_cipher = None

a = Analysis(
    ['../host/main.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        ('../host/webroot', 'webroot'),
        ('../host/bundled/llama-server', 'llama-server'),
    ],
    hiddenimports=[
        'zeroconf._utils.ipaddress',
        'pydantic',
        'pydantic_core',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='llamagrid-host',
    debug=False, strip=False, upx=False, console=False,
    icon='icon_host.ico',
    version='version_info.txt',
)
```

### 8.3 `build/peer.spec`

Same shape, includes tkinter, bundles `peer/bundled/rpc-server/` and
`peer/bundled/nssm.exe`. Built with `console=False` for normal launches;
service mode uses the same exe with `--service` so we don't need two
binaries.

### 8.4 Build commands

```powershell
# From repo root, in a venv with pyinstaller installed.
python -m pip install -r requirements.txt
python build/fetch_binaries.py

pyinstaller --clean --noconfirm --distpath build/dist/host build/host.spec
pyinstaller --clean --noconfirm --distpath build/dist/peer build/peer.spec

# Produce installers.
makensis /DVERSION=0.1.0 build/host_installer.nsi
makensis /DVERSION=0.1.0 build/peer_installer.nsi

# Outputs:
#   build/output/llamagrid-host-0.1.0-setup.exe
#   build/output/llamagrid-peer-0.1.0-setup.exe
```

`scripts/build_all.ps1` runs the four commands above in sequence and
fails fast on the first non-zero exit.

### 8.5 NSIS installer scripts

**Host installer (`host_installer.nsi`) responsibilities:**

- Install to `%PROGRAMFILES%\LlamaGrid\`.
- Create `%PROGRAMDATA%\LlamaGrid\{logs,models,cache}`.
- Add firewall rule for TCP 8080 inbound.
- Optionally install host as a Windows service (checkbox in installer UI).
- Create Start Menu shortcut "LlamaGrid Dashboard" that opens
  `http://localhost:8080` and launches `llamagrid-host.exe` if not
  already running.
- Write uninstaller.

**Peer installer (`peer_installer.nsi`) responsibilities:**

- Install to `%PROGRAMFILES%\LlamaGrid\`.
- Create `%PROGRAMDATA%\LlamaGrid\{logs,rpc,cache}`.
- Launch `peer.exe` on completion — the tkinter wizard takes over from
  there (it's the wizard that installs CUDA / VC++ / firewall / service).
- Write uninstaller that stops both services, removes them, removes the
  firewall rule, and removes `%PROGRAMDATA%\LlamaGrid\`.

Both installers require admin elevation (`RequestExecutionLevel admin`).

---

## 9. requirements.txt

```
flask==3.0.3
pydantic==2.7.1
zeroconf==0.132.2
psutil==5.9.8
requests==2.32.3
pywin32==306
pyinstaller==6.7.0  ; only for build
```

No additional runtime deps. Tkinter and the Windows registry API ship
with Python.

---

## 10. Development order — incremental, testable slices

The project is built in **eight slices**. Each slice ends in something
that can be exercised without the next slice existing. Don't move
forward until the current slice's test bullet is green.

### Slice 1 — Shared protocol + sysreport

- Implement `shared/protocol.py`, `shared/sysreport.py`,
  `shared/versioning.py`, `shared/logging_setup.py`.
- Unit-test pydantic round-trips.
- Run `python -m shared.sysreport` on a real machine and confirm GPU,
  CUDA, RAM all populate correctly.

### Slice 2 — Host backend skeleton, no peers

- Implement `host/config.py`, `host/coordinator.py` (registry only),
  `host/api.py` (just `/api/version`, `/api/peers`, `/api/cluster`),
  `host/main.py`.
- `curl http://localhost:8080/api/version` returns version info.
- Manually POST to `/api/register` with `Invoke-RestMethod` and verify the
  peer appears in `/api/peers`.

### Slice 3 — Static dashboard against real backend

- Implement `host/webroot/{index.html,app.js,style.css}`.
- Confirm peer card appears for the manually registered peer from Slice 2.
- Cluster bar updates every 5 s.

### Slice 4 — Peer agent (no installer, no service yet)

- Implement `peer/main.py`, `peer/agent.py`, `peer/host_discovery.py`.
- Hardcode host IP in a dev config, run `python -m peer.main` from
  source on a second machine.
- Verify registration, heartbeat every 30 s, peer card stays green.
- Unplug the peer's network; verify card flips yellow then red within 90 s.

### Slice 5 — mDNS on both sides

- Implement `shared/mdns.py`; wire advertiser/browser into both apps.
- Remove hardcoded host IP from peer dev config.
- Verify peer finds host automatically on the same LAN.
- Verify host's mDNS browser logs peer arrivals/departures.

### Slice 6 — llama-server integration + inference UI

- Implement `host/llama_manager.py`, `host/model_scanner.py`.
- With one real peer running `rpc-server.exe` manually, launch
  `llama-server.exe --rpc <peer-ip>:50052` from the host and confirm
  inference works.
- Wire `/api/inference` and the dashboard Inference tab. Confirm
  streaming output.
- Plug in topology-change restarts: add/remove peer, watch `llama-server`
  restart with new `--rpc` arg.

### Slice 7 — Peer installer pipeline

- Implement `peer/installer.py`, `peer/service.py`, `peer/gui.py`,
  `peer/updater.py`.
- Run `peer.exe` on a clean Windows 11 VM (with no CUDA, no VC++, no
  llama.cpp).
- Confirm the wizard end-to-end: VC++ installs, CUDA installs, rpc-server
  downloads, firewall rule added, two services running, peer registers.
- Reboot VM, confirm services come back up and peer rejoins automatically.

### Slice 8 — Alerts, remote restart/update, final build

- Implement `host/alerts.py`. Verify each rule fires by simulating
  conditions (e.g. kill `rpc-server` to fire `RPC_NOT_RUNNING`).
- Wire `/api/peers/<id>/restart` and `/update` to the command queue;
  verify peer applies the command on next heartbeat.
- Bump pinned llama.cpp tag, push a "new" version via the host, watch all
  peers self-update.
- Finalize PyInstaller specs and NSIS scripts; produce both installers.
- Install both installers on fresh VMs; smoke-test 3-peer cluster.

---

## 11. Operational notes

- **Air-gapped installs**: pre-populate `%PROGRAMDATA%\LlamaGrid\cache\`
  on the peer with `VC_redist.x64.exe`, the CUDA installer, the
  llama.cpp zip, and `nssm.exe`. The installer prefers cached files over
  downloading.
- **Single subnet assumption**: mDNS is single-broadcast-domain. If
  peers are in another VLAN, use the HTTP fallback (manual IP entry in
  the wizard).
- **Auth token**: generated on first host launch, displayed once in the
  dashboard's Settings tab, and saved in `config.json`. Operators copy
  it into the peer wizard. If the token is rotated, all peers must be
  reconfigured — there's no SSO.
- **Model swap**: changing the selected model triggers `llama-server`
  restart. Inference is unavailable for ~5–30 s depending on model size
  and number of peers.
- **Peer departure**: when a peer goes offline, the host restarts
  `llama-server` without it. Currently-loaded model may not fit on
  the smaller pool — if so, `llama-server` exits and the host surfaces
  an `INSUFFICIENT_CLUSTER_VRAM` alert at cluster level.
- **Security posture**: this is a LAN tool. The dashboard has no auth in
  v0.1. RPC traffic is unencrypted (`llama.cpp` doesn't encrypt it).
  Operators are expected to keep the cluster on a trusted segment.

---

## 12. Out of scope for v0.1

These are explicitly *not* in the first release:

- Authentication on the dashboard itself (token-gated API is enough).
- TLS on REST or RPC.
- Multi-host federation.
- Heterogeneous OS support (Linux peers, macOS peers).
- Automatic model sharding decisions (we delegate to `llama.cpp`'s own
  layer assignment).
- Web-based peer log streaming (only error excerpts are surfaced).
- Driver auto-installation.

Tracked for v0.2+ in `README.md`.
