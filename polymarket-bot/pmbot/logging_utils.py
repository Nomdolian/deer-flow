"""Structured JSON logging to stdout, with rotation when a file is given."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
import time
from pathlib import Path

# Belt and braces: even a mistaken log call must not leak a key.
SECRET_RE = re.compile(r"0x[a-fA-F0-9]{64}")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": SECRET_RE.sub("0x<redacted>", record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO, path: str | Path | None = None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter())
    root.addHandler(stream)

    if path:
        rotating = logging.handlers.RotatingFileHandler(
            str(path), maxBytes=20 * 1024 * 1024, backupCount=5
        )
        rotating.setFormatter(JsonFormatter())
        root.addHandler(rotating)

    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
