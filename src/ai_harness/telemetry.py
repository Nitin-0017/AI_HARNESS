"""JSON Lines events and a redacted state snapshot, in a harness-owned run dir."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus
from uuid import uuid4


class Redactor:
    def __init__(self, secret: str | None = None):
        secret = secret if isinstance(secret, str) else None
        self._secrets = sorted(
            {v for v in (secret, quote(secret, safe="", errors="replace") if secret else None,
                          quote_plus(secret, errors="replace") if secret else None) if v}, key=len, reverse=True,
        )

    def __repr__(self) -> str:
        return "Redactor(<protected>)"

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Path):
            return self.text(str(value))
        if isinstance(value, Mapping):
            sensitive = {"ai_api_key", "api_key", "authorization", "password", "secret", "access_token"}
            return {self.text(str(k)): "[REDACTED]" if str(k).lower() in sensitive else self.clean(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return self.text(str(value))


class JsonFormatter(logging.Formatter):
    def __init__(self, run_id: str, redactor: Redactor):
        super().__init__()
        self.run_id = run_id
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "run_id": self.run_id,
            "event": record.getMessage(),
            "data": getattr(record, "data", {}),
        }
        return json.dumps(self.redactor.clean(payload), ensure_ascii=False, allow_nan=False)


class StrictStreamHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Standard logging can silently suppress I/O errors. Startup must not.
        raise OSError("Unable to persist a structured log event") from None


class EventLog:
    def __init__(self, run_dir: Path, run_id: str, redactor: Redactor, level: str, *, filename: str = "events.jsonl"):
        if filename not in {"events.jsonl", "tool-events.jsonl", "model-events.jsonl"}:
            raise ValueError("Unsupported event log filename")
        path = run_dir / filename
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        self._stream = os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8")
        self._logger = logging.Logger(f"ai_harness.{run_id}", level=level)
        self._logger.propagate = False
        self._handler = StrictStreamHandler(self._stream)
        self._handler.setFormatter(JsonFormatter(run_id, redactor))
        self._logger.addHandler(self._handler)

    def emit(self, event: str, *, level: str = "INFO", **data: Any) -> None:
        self._logger.log(getattr(logging, level), event, extra={"data": data})

    def close(self) -> None:
        self._handler.flush()
        self._logger.removeHandler(self._handler)
        self._handler.close()
        self._stream.close()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def create_run_dir(output_dir: Path, run_id: str) -> Path:
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = output_dir / run_id
    directory.mkdir(mode=0o700, exist_ok=False)
    return directory


def write_state(run_dir: Path, snapshot: dict[str, Any], redactor: Redactor) -> Path:
    target = run_dir / "run_state.json"
    temporary = run_dir / (".state-" + uuid4().hex + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(temporary, flags, 0o600), "w", encoding="utf-8") as stream:
            json.dump(redactor.clean(snapshot), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
