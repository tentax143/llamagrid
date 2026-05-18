from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from peer.config import PeerConfig, save_config
from peer.host_discovery import probe_host

log = logging.getLogger(__name__)


class FirstRunWizard:
    """Three-screen tkinter wizard for peer first-run setup."""

    def __init__(self, cfg: PeerConfig):
        self.cfg = cfg
        self.root = tk.Tk()
        self.root.title("LlamaGrid Peer Setup")
        self.root.geometry("520x420")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1d27")
        self._center()

        self._log_queue: queue.Queue = queue.Queue()
        self._install_results: list = []
        self._cancelled = False
        self._finished = False

        self._frame = tk.Frame(self.root, bg="#1a1d27")
        self._frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)

        self._show_welcome()
        self.root.mainloop()

    def _center(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

    def _clear(self):
        for w in self._frame.winfo_children():
            w.destroy()

    def _label(self, parent, text, size=12, bold=False, color="#e2e8f0", **kwargs):
        font = ("Segoe UI", size, "bold" if bold else "normal")
        return tk.Label(parent, text=text, font=font, fg=color, bg="#1a1d27", **kwargs)

    def _btn(self, parent, text, command, primary=False, danger=False):
        bg = "#ef4444" if danger else ("#3b82f6" if primary else "#2e3148")
        fg = "#ffffff"
        b = tk.Button(
            parent, text=text, command=command,
            font=("Segoe UI", 10), fg=fg, bg=bg,
            relief=tk.FLAT, padx=16, pady=7, cursor="hand2",
            activebackground=bg, activeforeground=fg,
        )
        return b

    # ── Screen 1: Welcome ──────────────────────────────────────────────

    def _show_welcome(self):
        self._clear()
        f = self._frame

        self._label(f, "🦙 LlamaGrid Peer Setup", size=18, bold=True, color="#3b82f6").pack(pady=(10, 4))
        self._label(f, "v0.1.0", size=10, color="#64748b").pack()

        tk.Frame(f, height=1, bg="#2e3148").pack(fill=tk.X, pady=16)

        self._label(f, "This wizard will:", size=11).pack(anchor=tk.W)
        for item in [
            "  ✓  Check and install required dependencies (VC++, CUDA)",
            "  ✓  Install or detect rpc-server.exe",
            "  ✓  Add a Windows Firewall rule for port 50052",
            "  ✓  Install rpc-server as a Windows service (auto-start on boot)",
            "  ✓  Register this machine with your LlamaGrid host",
        ]:
            self._label(f, item, size=10, color="#8892a4").pack(anchor=tk.W, pady=1)

        tk.Frame(f, height=1, bg="#2e3148").pack(fill=tk.X, pady=16)

        self._label(f, "Administrator privileges are required.", size=10, color="#eab308").pack()

        btn_row = tk.Frame(f, bg="#1a1d27")
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=8)
        self._btn(btn_row, "Cancel", self.root.destroy).pack(side=tk.LEFT)
        self._btn(btn_row, "Next →", self._show_config, primary=True).pack(side=tk.RIGHT)

    # ── Screen 2: Configuration ────────────────────────────────────────

    def _show_config(self):
        self._clear()
        f = self._frame

        self._label(f, "Host Configuration", size=14, bold=True).pack(anchor=tk.W, pady=(0, 12))

        # Discovery mode
        mode_var = tk.StringVar(value="manual")
        mode_frame = tk.Frame(f, bg="#1a1d27")
        mode_frame.pack(fill=tk.X, pady=4)
        self._label(mode_frame, "Host discovery:").pack(anchor=tk.W)
        tk.Radiobutton(mode_frame, text="Enter host IP manually", variable=mode_var, value="manual",
                       bg="#1a1d27", fg="#e2e8f0", selectcolor="#2e3148",
                       font=("Segoe UI", 10), activebackground="#1a1d27").pack(anchor=tk.W)
        tk.Radiobutton(mode_frame, text="Auto-discover via mDNS (same LAN)", variable=mode_var, value="mdns",
                       bg="#1a1d27", fg="#e2e8f0", selectcolor="#2e3148",
                       font=("Segoe UI", 10), activebackground="#1a1d27").pack(anchor=tk.W)

        # Host IP
        ip_frame = tk.Frame(f, bg="#1a1d27")
        ip_frame.pack(fill=tk.X, pady=8)
        self._label(ip_frame, "Host IP address:").pack(anchor=tk.W)
        ip_var = tk.StringVar(value=self.cfg.host_ip or "172.16.71.183")
        ip_entry = tk.Entry(ip_frame, textvariable=ip_var, font=("Segoe UI", 11),
                            bg="#222536", fg="#e2e8f0", insertbackground="#e2e8f0",
                            relief=tk.FLAT, bd=6)
        ip_entry.pack(fill=tk.X, pady=4)

        # Auth token
        token_frame = tk.Frame(f, bg="#1a1d27")
        token_frame.pack(fill=tk.X, pady=4)
        self._label(token_frame, "Auth token (from host dashboard Settings tab):").pack(anchor=tk.W)
        token_var = tk.StringVar(value=self.cfg.auth_token)
        token_entry = tk.Entry(token_frame, textvariable=token_var, font=("Segoe UI", 10),
                               bg="#222536", fg="#e2e8f0", insertbackground="#e2e8f0",
                               relief=tk.FLAT, bd=6)
        token_entry.pack(fill=tk.X, pady=4)

        # Status label
        status_lbl = self._label(f, "", size=10, color="#64748b")
        status_lbl.pack()

        def test_connection():
            ip = ip_var.get().strip()
            port = self.cfg.host_port
            status_lbl.config(text=f"Testing {ip}:{port}...", fg="#eab308")
            self.root.update()
            ep = probe_host(ip, port, timeout=4)
            if ep:
                status_lbl.config(text=f"✓ Connected — host version {ep.version}", fg="#22c55e")
            else:
                status_lbl.config(text="✗ Could not connect — check IP and that host is running", fg="#ef4444")

        def do_next():
            ip = ip_var.get().strip()
            if not ip and mode_var.get() == "manual":
                messagebox.showerror("Missing IP", "Enter the host IP address.")
                return
            self.cfg.host_ip = ip
            self.cfg.auth_token = token_var.get().strip()
            save_config(self.cfg)
            self._show_progress()

        btn_row = tk.Frame(f, bg="#1a1d27")
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=8)
        self._btn(btn_row, "← Back", self._show_welcome).pack(side=tk.LEFT)
        self._btn(btn_row, "Test Connection", test_connection).pack(side=tk.LEFT, padx=8)
        self._btn(btn_row, "Install →", do_next, primary=True).pack(side=tk.RIGHT)

    # ── Screen 3: Installation progress ────────────────────────────────

    def _show_progress(self):
        self._clear()
        f = self._frame

        self._label(f, "Installing...", size=14, bold=True).pack(anchor=tk.W, pady=(0, 8))

        # Scrolled log
        log_frame = tk.Frame(f, bg="#0f1117", relief=tk.FLAT, bd=1)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        log_text = tk.Text(
            log_frame, font=("Consolas", 9), bg="#0f1117", fg="#8892a4",
            relief=tk.FLAT, state=tk.DISABLED, wrap=tk.WORD
        )
        scroll = tk.Scrollbar(log_frame, command=log_text.yview)
        log_text.config(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        status_lbl = self._label(f, "Running checks...", size=10, color="#eab308")
        status_lbl.pack()

        btn_row = tk.Frame(f, bg="#1a1d27")
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=8)
        finish_btn = self._btn(btn_row, "Finish", self.root.destroy, primary=True)
        finish_btn.pack(side=tk.RIGHT)
        finish_btn.config(state=tk.DISABLED)

        viewlog_btn = self._btn(btn_row, "View Log", lambda: os.startfile(r"C:\LlamaGrid\logs\peer_agent.log"))
        viewlog_btn.pack(side=tk.LEFT)

        def append_log(msg: str, color="#8892a4"):
            log_text.config(state=tk.NORMAL)
            log_text.insert(tk.END, msg + "\n")
            log_text.config(state=tk.DISABLED)
            log_text.see(tk.END)

        def progress_cb(msg: str):
            self._log_queue.put(("log", msg))

        def install_thread():
            from peer.installer import run_all_checks
            from peer.service import service_running
            import os

            results = run_all_checks(self.cfg, progress_cb)

            # Register with host
            self._log_queue.put(("log", "Registering with host..."))
            try:
                from peer.host_discovery import probe_host
                from peer.agent import Agent
                agent = Agent(self.cfg)
                if agent._ensure_host():
                    ok = agent._register_once()
                    msg = "✓ Registered with host" if ok else "⚠ Could not register — check host IP and auth token"
                    self._log_queue.put(("log", msg))
                else:
                    self._log_queue.put(("log", "⚠ Host not reachable — will retry on next startup"))
            except Exception as e:
                self._log_queue.put(("log", f"Registration error: {e}"))

            self.cfg.installed = all(r[1] for r in results)
            save_config(self.cfg)
            self._log_queue.put(("done", results))

        threading.Thread(target=install_thread, daemon=True).start()

        def poll_queue():
            try:
                while True:
                    kind, data = self._log_queue.get_nowait()
                    if kind == "log":
                        append_log(data)
                    elif kind == "done":
                        results = data
                        all_ok = all(r[1] for r in results)
                        if all_ok:
                            status_lbl.config(text="✓ Installation complete!", fg="#22c55e")
                        else:
                            failed = [r[0] for r in results if not r[1]]
                            status_lbl.config(text=f"⚠ Some steps failed: {', '.join(failed)}", fg="#ef4444")
                        finish_btn.config(state=tk.NORMAL)
                        return
            except queue.Empty:
                pass
            self.root.after(100, poll_queue)

        poll_queue()

    # ── Status screen (already installed) ─────────────────────────────

    def show_status(self):
        self._clear()
        f = self._frame

        self._label(f, "LlamaGrid Peer", size=16, bold=True, color="#3b82f6").pack(pady=(4, 0))
        self._label(f, f"ID: {self.cfg.peer_id[:20]}...", size=9, color="#4a5568").pack()

        tk.Frame(f, height=1, bg="#2e3148").pack(fill=tk.X, pady=8)

        # ── Service status ────────────────────────────────────────────
        svc_row = tk.Frame(f, bg="#1a1d27")
        svc_row.pack(pady=1)
        rpc_lbl = self._label(svc_row, "rpc-server: checking...", size=11, color="#eab308")
        rpc_lbl.pack(side=tk.LEFT, padx=(0, 16))
        agent_lbl = self._label(svc_row, "agent svc: checking...", size=11, color="#eab308")
        agent_lbl.pack(side=tk.LEFT)

        # ── Heartbeat pulse ───────────────────────────────────────────
        hb_row = tk.Frame(f, bg="#1a1d27")
        hb_row.pack(pady=4)
        pulse_lbl = self._label(hb_row, "●", size=16, color="#4a5568")
        pulse_lbl.pack(side=tk.LEFT, padx=(0, 8))
        hb_lbl = self._label(hb_row, "Waiting for first heartbeat...", size=11, color="#64748b")
        hb_lbl.pack(side=tk.LEFT)

        host_lbl = self._label(f, f"Host: {self.cfg.host_ip}:{self.cfg.host_port}", size=10, color="#8892a4")
        host_lbl.pack()
        count_lbl = self._label(f, "", size=9, color="#4a5568")
        count_lbl.pack()

        tk.Frame(f, height=1, bg="#2e3148").pack(fill=tk.X, pady=8)

        # ── Last sent to host ─────────────────────────────────────────
        self._label(f, "Last sent to host", size=9, bold=True, color="#64748b").pack(anchor=tk.W)
        payload_box = tk.Frame(f, bg="#0f1117", relief=tk.FLAT, bd=0)
        payload_box.pack(fill=tk.X, pady=(2, 0))
        gpu_lbl  = tk.Label(payload_box, text="  GPU:  —",   font=("Consolas", 9), fg="#8892a4", bg="#0f1117", anchor=tk.W)
        vram_lbl = tk.Label(payload_box, text="  VRAM: —",   font=("Consolas", 9), fg="#8892a4", bg="#0f1117", anchor=tk.W)
        ram_lbl  = tk.Label(payload_box, text="  RAM:  —",   font=("Consolas", 9), fg="#8892a4", bg="#0f1117", anchor=tk.W)
        disk_lbl = tk.Label(payload_box, text="  Disk: —",   font=("Consolas", 9), fg="#8892a4", bg="#0f1117", anchor=tk.W)
        rpc_payload_lbl = tk.Label(payload_box, text="  RPC:  —", font=("Consolas", 9), fg="#8892a4", bg="#0f1117", anchor=tk.W)
        for lbl in (gpu_lbl, vram_lbl, ram_lbl, disk_lbl, rpc_payload_lbl):
            lbl.pack(fill=tk.X, padx=4, pady=0)

        tk.Frame(f, height=1, bg="#2e3148").pack(fill=tk.X, pady=8)

        # ── Buttons ───────────────────────────────────────────────────
        import webbrowser
        btn_row = tk.Frame(f, bg="#1a1d27")
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=2)

        stop_btn = self._btn(btn_row, "⬛ Stop All", None, danger=True)
        stop_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btn_row, "View Log",
                  lambda: os.startfile(r"C:\LlamaGrid\logs\peer_agent.log")).pack(side=tk.LEFT)
        self._btn(btn_row, "Open Dashboard",
                  lambda: webbrowser.open(f"http://{self.cfg.host_ip}:{self.cfg.host_port}"),
                  primary=True).pack(side=tk.RIGHT)

        def do_stop_all():
            if not messagebox.askyesno(
                "Stop All",
                "Stop rpc-server and agent services and close?\n\n"
                "This peer will go offline. Open the exe again to bring it back.",
                icon="warning",
            ):
                return
            stop_btn.config(state=tk.DISABLED, text="Stopping…")
            self.root.update()

            def _worker():
                import subprocess
                from peer.service import stop_service
                stop_service("LlamaGridAgent")
                stop_service("LlamaGridRPC")
                subprocess.run(["taskkill", "/F", "/IM", "rpc-server.exe"], capture_output=True)
                agent = getattr(self, "_agent", None)
                if agent:
                    agent.stop()
                self.root.after(0, self.root.destroy)

            threading.Thread(target=_worker, daemon=True).start()

        stop_btn.config(command=do_stop_all)

        # ── Live poll ─────────────────────────────────────────────────
        self._pulse_state = False
        self._svc_tick = 0

        def poll():
            import json, datetime, os as _os
            STATUS_FILE = r"C:\LlamaGrid\peer_status.json"

            # Service status — check every 2 s (every 4th 500ms tick)
            self._svc_tick = (self._svc_tick + 1) % 4
            if self._svc_tick == 0:
                try:
                    from peer.service import service_running
                    rpc_ok = service_running("LlamaGridRPC")
                    rpc_lbl.config(
                        text="rpc-server: RUNNING" if rpc_ok else "rpc-server: STOPPED",
                        fg="#22c55e" if rpc_ok else "#ef4444",
                    )
                    agent_ok = service_running("LlamaGridAgent")
                    agent_lbl.config(
                        text="agent svc: RUNNING" if agent_ok else "agent svc: STOPPED",
                        fg="#22c55e" if agent_ok else "#eab308",
                    )
                except Exception:
                    pass

            # Heartbeat + payload — every tick
            try:
                if not _os.path.isfile(STATUS_FILE):
                    raise FileNotFoundError
                with open(STATUS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)

                last_hb = datetime.datetime.fromisoformat(data["last_heartbeat"])
                elapsed = (datetime.datetime.now() - last_hb).total_seconds()

                if elapsed < 4:
                    self._pulse_state = not self._pulse_state
                    dot_color = "#22c55e" if self._pulse_state else "#16a34a"
                elif elapsed < 20:
                    dot_color = "#eab308"
                else:
                    dot_color = "#ef4444"
                pulse_lbl.config(fg=dot_color)

                age = f"{elapsed:.1f}s ago" if elapsed < 60 else f"{int(elapsed/60)}m {int(elapsed%60)}s ago"
                hb_lbl.config(text=f"Sending heartbeat every 2s  ·  last {age}", fg="#e2e8f0")

                host_lbl.config(text=f"Host: {data.get('host_ip', self.cfg.host_ip)}:{data.get('host_port', self.cfg.host_port)}")
                count_lbl.config(text=f"{data.get('heartbeat_count', 0)} heartbeats sent this session")

                # Payload panel
                if "gpu_name" in data:
                    gpu_lbl.config(text=f"  GPU:  {data['gpu_name']}")
                    vfree = data.get("vram_free_mb", 0)
                    vtotal = data.get("vram_total_mb", 0)
                    vram_lbl.config(
                        text=f"  VRAM: {vfree/1024:.1f} GB free / {vtotal/1024:.1f} GB total",
                        fg="#22c55e" if vfree > 1024 else "#ef4444",
                    )
                rfree = data.get("ram_free_mb", 0)
                rtotal = data.get("ram_total_mb", 0)
                if rtotal:
                    ram_lbl.config(text=f"  RAM:  {rfree/1024:.1f} GB free / {rtotal/1024:.1f} GB total")
                if "disk_free_gb" in data:
                    disk_lbl.config(text=f"  Disk: {data['disk_free_gb']} GB free")
                rpc_payload_lbl.config(
                    text=f"  RPC:  {'RUNNING' if data.get('rpc_running') else 'STOPPED'}",
                    fg="#22c55e" if data.get("rpc_running") else "#ef4444",
                )

            except Exception:
                hb_lbl.config(text="Waiting for first heartbeat...", fg="#64748b")
                pulse_lbl.config(fg="#4a5568")

            self.root.after(500, poll)

        poll()
        self.root.mainloop()


def run_first_run_wizard(cfg: PeerConfig) -> None:
    wizard = FirstRunWizard(cfg)


import os  # noqa: E402
