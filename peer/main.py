from __future__ import annotations

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

    # Check if already installed
    if cfg.installed:
        from peer.service import service_exists
        if service_exists("LlamaGridRPC"):
            # Already set up — show status screen if interactive, else just exit
            if sys.stdout.isatty() or os.environ.get("LLAMAGRID_GUI"):
                from peer.gui import FirstRunWizard
                wizard = FirstRunWizard.__new__(FirstRunWizard)
                wizard.cfg = cfg
                import tkinter as tk
                from tkinter import ttk
                wizard.root = tk.Tk()
                wizard.root.title("LlamaGrid Peer Status")
                wizard.root.geometry("520x320")
                wizard.root.resizable(False, False)
                wizard.root.configure(bg="#1a1d27")
                wizard._frame = tk.Frame(wizard.root, bg="#1a1d27")
                wizard._frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)
                wizard._log_queue = __import__("queue").Queue()
                wizard.show_status()
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
