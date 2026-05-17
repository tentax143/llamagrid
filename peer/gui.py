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

    def _btn(self, parent, text, command, primary=False):
        bg = "#3b82f6" if primary else "#2e3148"
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

        from peer.service import service_running
        rpc_running = service_running("LlamaGridRPC")

        self._label(f, "🦙 LlamaGrid Peer", size=16, bold=True, color="#3b82f6").pack(pady=(10, 4))

        status_color = "#22c55e" if rpc_running else "#ef4444"
        status_text = "rpc-server: RUNNING" if rpc_running else "rpc-server: STOPPED"
        self._label(f, status_text, size=12, color=status_color).pack(pady=8)

        self._label(f, f"Peer ID: {self.cfg.peer_id[:16]}...", size=9, color="#64748b").pack()
        self._label(f, f"Host: {self.cfg.host_ip}:{self.cfg.host_port}", size=10, color="#8892a4").pack(pady=4)

        import webbrowser
        btn_row = tk.Frame(f, bg="#1a1d27")
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=8)
        self._btn(btn_row, "Open Dashboard", lambda: webbrowser.open(f"http://{self.cfg.host_ip}:{self.cfg.host_port}"), primary=True).pack(side=tk.RIGHT)
        self._btn(btn_row, "View Log", lambda: os.startfile(r"C:\LlamaGrid\logs\peer_agent.log")).pack(side=tk.LEFT)
        self.root.mainloop()


def run_first_run_wizard(cfg: PeerConfig) -> None:
    wizard = FirstRunWizard(cfg)


import os  # noqa: E402
