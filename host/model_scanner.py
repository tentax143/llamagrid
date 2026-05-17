from __future__ import annotations

import logging
import os
import re

from shared.protocol import ModelInfo

log = logging.getLogger(__name__)

_QUANT_RE = re.compile(r"(Q\d+_K_[SM]|Q\d+_[0-9]|Q\d+|IQ\d+_[A-Z]+|f16|f32)", re.IGNORECASE)


def scan(model_dir: str) -> list[ModelInfo]:
    models: list[ModelInfo] = []
    if not os.path.isdir(model_dir):
        log.warning("Model directory does not exist: %s", model_dir)
        return models

    for root, _dirs, files in os.walk(model_dir):
        for fname in files:
            if not fname.lower().endswith(".gguf"):
                continue
            full = os.path.join(root, fname)
            try:
                size_gb = os.path.getsize(full) / (1024 ** 3)
            except OSError:
                continue

            m = _QUANT_RE.search(fname)
            quant = m.group(0).upper() if m else "unknown"

            models.append(
                ModelInfo(
                    name=fname,
                    path=full,
                    size_gb=round(size_gb, 2),
                    quant=quant,
                )
            )

    models.sort(key=lambda m: m.name)
    log.debug("Found %d model(s) in %s", len(models), model_dir)
    return models
