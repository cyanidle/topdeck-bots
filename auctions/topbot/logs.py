from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module", "exc_info",
    "exc_text", "stack_info", "lineno", "funcName", "created", "msecs", "relativeCreated", "thread",
    "threadName", "processName", "process", "taskName", "message", "asctime",
}

NOISY = {
    "aiogram.event": logging.WARNING,
    "aiogram.dispatcher.event.bases": logging.WARNING,
    "aiohttp.access": logging.WARNING,
}


def extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value for key, value in record.__dict__.items()
        if key not in RESERVED and not key.startswith("_")
    }


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = extra_fields(record)
        if not extras:
            return base
        rendered = " ".join(f"{key}={value}" for key, value in extras.items())
        return f"{base} | {rendered}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(extra_fields(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", fmt: str = "text", file: str | None = None,
                  max_bytes: int = 10 * 1024 * 1024, backups: int = 5) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    root.setLevel(level.upper())
    formatter: logging.Formatter
    if fmt == "json":
        formatter = JsonFormatter()
    else:
        formatter = TextFormatter("%(asctime)s %(levelname)-7s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if file:
        os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
        rotating = RotatingFileHandler(file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        rotating.setFormatter(JsonFormatter())
        root.addHandler(rotating)

    for noisy, noisy_level in NOISY.items():
        logging.getLogger(noisy).setLevel(noisy_level)


class Timer:
    def __init__(self) -> None:
        import time as _time

        self._time = _time
        self.started = _time.perf_counter()

    @property
    def ms(self) -> int:
        return int((self._time.perf_counter() - self.started) * 1000)
