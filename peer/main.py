from __future__ import annotations

import ctypes
import os
import sys

# Ensure repo root on sys.path when running from source
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Logging must be set up before anything else so we always have a log file
import shared.logging_setup as _ls
_ls.setup("peer_agent")

import logging
log = logging.getLogger(__name__)


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _elevate_and_exit() -> None:
    """Re-launch the current process with admin rights and exit."""
    if getattr(sys, "frozen", False):
        # Packaged exe — re-run the exe itself
        exe = sys.executable
        args = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        # Running from source — re-run python with same argv
        exe = sys.executable
        args = " ".join(f'"{a}"' for a in sys.argv)
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
    if ret > 32:
        sys.exit(0)
    # User cancelled UAC — fall through and continue without admin


def main() -> None:
    from peer.config import load_config, save_config
    cfg = load_config()

    service_mode = "--service" in sys.argv

    if service_mode:
        # Running as a Windows service — skip GUI, go straight to agent
        log.info("Starting in service mode")
        from peer.agent import Agent
        Agent(cfg).run()
        return

    # Require admin for service management and firewall rules.
    if not _is_admin():
        log.warning("Not running as administrator — requesting elevation")
        _elevate_and_exit()
        # If we reach here the user cancelled UAC; continue with limited functionality.
        log.warning("Continuing without admin — service control will be unavailable")

    # Check if already installed
    if cfg.installed:
        from peer.service import service_exists
        if service_exists("LlamaGridRPC"):
            import threading
            from peer.agent import Agent

            # Always run the heartbeat loop inside the exe — this keeps the peer
            # alive even if the LlamaGridAgent service has stopped or crashed.
            agent = Agent(cfg)
            threading.Thread(target=agent.run, daemon=True, name="agent-heartbeat").start()

            from peer.gui import FirstRunWizard
            import tkinter as tk
            wizard = FirstRunWizard.__new__(FirstRunWizard)
            wizard.cfg = cfg
            wizard.root = tk.Tk()
            wizard.root.title("LlamaGrid Peer")
            wizard.root.geometry("520x480")
            wizard.root.resizable(False, False)
            wizard.root.configure(bg="#1a1d27")
            wizard._frame = tk.Frame(wizard.root, bg="#1a1d27")
            wizard._frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)
            wizard._log_queue = __import__("queue").Queue()
            wizard._agent = agent
            wizard.show_status()
            agent.stop()
            return

    # First run — launch wizard
    log.info("First run — launching setup wizard")
    from peer.gui import run_first_run_wizard
    run_first_run_wizard(cfg)

    # After wizard completes, launch agent if auto_start
    cfg = load_config()  # reload — wizard may have updated it
    if cfg.installed and cfg.auto_start:
        from peer.agent import Agent
        log.info("Wizard complete — starting agent loop")
        Agent(cfg).run()


if __name__ == "__main__":
    main()
