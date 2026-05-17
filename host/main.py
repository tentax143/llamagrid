from __future__ import annotations

import os
import sys

# Ensure repo root is on sys.path when running from source
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import shared.logging_setup as _ls
log = _ls.setup("host")

import logging
log = logging.getLogger(__name__)

from host.config import load_config
from host.coordinator import Coordinator
from host.llama_manager import LlamaManager
from host.api import build_flask_app


def main(auto_start: bool = True) -> None:
    cfg = load_config()
    log.info("LlamaGrid host starting — port %d, model: %s", cfg.port, cfg.model_path)
    log.info("Auth token: %s", cfg.auth_token)

    coord = Coordinator(cfg)
    coord.start()

    llama = LlamaManager(cfg, coord)
    if auto_start and cfg.auto_start_inference:
        llama.start()

    app = build_flask_app(coord, llama, cfg)

    try:
        log.info("Dashboard: http://localhost:%d", cfg.port)
        app.run(host="0.0.0.0", port=cfg.port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down")
        llama.stop()
        coord.stop()


if __name__ == "__main__":
    main()
