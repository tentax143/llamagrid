import logging
import logging.handlers
import os
import sys

LOG_DIR = r"C:\LlamaGrid\logs"
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup(name: str) -> logging.Logger:
    global _configured

    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    if _configured:
        return logging.getLogger(name)

    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Rotating file handler — 5 MB × 3 files
    log_file = os.path.join(LOG_DIR, f"{name}.log")
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    _configured = True
    return logging.getLogger(name)
