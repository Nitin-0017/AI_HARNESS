"""Bounded, text-only task input. Input is data, never executed or fetched."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import stat
from typing import TextIO

from .errors import InputError


@dataclass(frozen=True)
class TaskInput:
    text: str = field(repr=False)
    source: str
    size_bytes: int


def validate_task(text: str, source: str, max_bytes: int) -> TaskInput:
    if not isinstance(text, str) or not text.strip():
        raise InputError("Task text must not be empty")
    if "\0" in text:
        raise InputError("Task text must not contain NUL characters")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise InputError("Task text must be valid UTF-8") from exc
    if size > max_bytes:
        raise InputError("Task exceeds the configured max_task_bytes limit")
    return TaskInput(text.strip(), source, size)


def _read_file(path: Path, max_bytes: int) -> str:
    try:
        # Reject FIFOs/devices before opening; avoid blocking on non-files.
        if not stat.S_ISREG(path.stat().st_mode):
            raise InputError("Task input must be a regular UTF-8 text file")
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise InputError("Task file exceeds the configured max_task_bytes limit")
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InputError("Cannot read task file as UTF-8 text") from exc


def load_task(
    *, text: str | None, file: Path | None, from_stdin: bool,
    env: Mapping[str, str], stdin: TextIO, cwd: Path, max_bytes: int,
) -> TaskInput | None:
    if sum((text is not None, file is not None, from_stdin)) > 1:
        raise InputError("Choose exactly one task input source")
    if text is not None:
        return validate_task(text, "cli", max_bytes)
    if file is not None:
        path = file.expanduser()
        path = path if path.is_absolute() else cwd / path
        return validate_task(_read_file(path, max_bytes), "file", max_bytes)
    if from_stdin:
        try:
            value = stdin.read(max_bytes + 1)
        except (OSError, UnicodeError) as exc:
            raise InputError("Cannot read task text from stdin") from exc
        return validate_task(value, "stdin", max_bytes)
    if "HARNESS_TASK" in env and "HARNESS_TASK_FILE" in env:
        raise InputError("Set only HARNESS_TASK or HARNESS_TASK_FILE, not both")
    if "HARNESS_TASK" in env:
        return validate_task(env["HARNESS_TASK"], "environment", max_bytes)
    if "HARNESS_TASK_FILE" in env:
        if not env["HARNESS_TASK_FILE"].strip():
            raise InputError("HARNESS_TASK_FILE must be a nonempty path")
        path = Path(env["HARNESS_TASK_FILE"]).expanduser()
        path = path if path.is_absolute() else cwd / path
        return validate_task(_read_file(path, max_bytes), "file", max_bytes)
    return None
