"""Structured logging with the current run ID on every record (NFR-10).

``run_context(run_id)`` binds a run ID for the code running inside it (graph nodes, LLM calls, renderers); every
log record then carries ``run_id``. ``LOG_FORMAT=json`` switches to one JSON object per line.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

run_id_var: ContextVar[str] = ContextVar("run_id", default="-")


class RunIdFilter(logging.Filter):
    """Adds ``record.run_id`` from the context variable."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


_configured = False


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install one handler on the root logger (idempotent)."""
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler()
    handler.addFilter(RunIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(run_id)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    if "pytest" not in __import__("sys").modules:  # a run log people can send when something goes wrong
        try:
            from logging.handlers import RotatingFileHandler
            from pathlib import Path

            Path("storage/logs").mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler("storage/logs/app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            file_handler.addFilter(RunIdFilter())
            file_handler.setFormatter(handler.formatter)
            root.addHandler(file_handler)
        except OSError:
            pass
    root.setLevel(level.upper())
    _configured = True


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    """Bind ``run_id`` to every log record emitted inside the block."""
    token = run_id_var.set(run_id)
    try:
        yield
    finally:
        run_id_var.reset(token)
