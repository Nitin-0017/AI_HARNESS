"""Validated public types for the model-independent Phase 2 tools."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from pathlib import PurePosixPath
from typing import Any

from .errors import ConfigurationError, HarnessError


class ToolError(HarnessError):
    """An action was rejected or could not be completed; never a success result."""


class PathViolation(ToolError):
    pass


class PatchError(ToolError):
    pass


class ExecutionBlocked(ToolError):
    pass


def positive(value: Any, name: str, *, integer: bool = False) -> None:
    try:
        valid = (not isinstance(value, bool) and isinstance(value, int if integer else (int, float))
                 and math.isfinite(value) and value > 0)
    except (TypeError, OverflowError):
        valid = False
    if not valid:
        raise ConfigurationError(f"{name} must be a finite positive {'integer' if integer else 'number'}")


@dataclass(frozen=True)
class ToolLimits:
    max_file_bytes: int = 1_000_000
    max_output_bytes: int = 262_144
    max_files: int = 10_000
    max_search_matches: int = 200
    max_scan_bytes: int = 20_000_000
    max_patch_files: int = 20
    max_depth: int = 40
    command_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            positive(value, name, integer=name != 'command_timeout_seconds')


@dataclass(frozen=True)
class CheckSpec:
    """A trusted operator-defined command. Tool callers choose its name only."""
    name: str
    argv: tuple[str, ...]
    cwd: str = '.'
    timeout_seconds: float | None = None
    scope: str = 'broad'
    paths: tuple[str, ...] = ()
    required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or len(self.name) > 100 or not all(
                c.isalnum() or c in '_-' for c in self.name):
            raise ConfigurationError('Check name must contain only letters, digits, underscores or hyphens')
        if (not isinstance(self.argv, (list, tuple)) or not self.argv or len(self.argv) > 100
                or any(not isinstance(a, str) or '\0' in a or len(a) > 8192 for a in self.argv)):
            raise ConfigurationError('Check argv must be a bounded nonempty array of strings')
        object.__setattr__(self, 'argv', tuple(self.argv))
        command = self.argv[0]
        if command != '{python}' and (not command.startswith(('/usr/bin/', '/usr/local/bin/', '/bin/'))
                                      or '..' in PurePosixPath(command).parts):
            raise ConfigurationError('Check executable must be {python} or an absolute system-bin path')
        if PurePosixPath(command).name in {'sh', 'bash', 'dash', 'zsh', 'fish', 'ksh', 'env', 'sudo', 'su'}:
            raise ConfigurationError('Shell and privilege-wrapper executables are not permitted')
        if not isinstance(self.cwd, str) or not self.cwd:
            raise ConfigurationError('Check cwd must be a workspace-relative directory')
        if (PurePosixPath(self.cwd).is_absolute() or '..' in self.cwd.split('/') or '\\' in self.cwd
                or ':' in self.cwd or any(ord(c) < 32 for c in self.cwd)):
            raise ConfigurationError('Check cwd must not traverse outside the target workspace')
        if self.scope not in {'targeted', 'broad'} or type(self.required) is not bool:
            raise ConfigurationError('Invalid check scope/required flag')
        if not isinstance(self.paths, (tuple, list)) or len(self.paths)>100 or any(not isinstance(p,str) or PurePosixPath(p).is_absolute() or '..' in p.split('/') or '\\' in p for p in self.paths):
            raise ConfigurationError('Check paths must be bounded relative paths')
        object.__setattr__(self, 'paths', tuple(self.paths))
        if self.timeout_seconds is not None:
            positive(self.timeout_seconds, 'check.timeout_seconds')


@dataclass(frozen=True)
class PatchEdit:
    path: str
    old: str = ''
    new: str = ''
    operation: str = 'replace'
    expected_sha256: str | None = None


@dataclass
class ProcessResult:
    argv: list[str]
    cwd: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    command_started: bool
    timed_out: bool = False
    output_limit_exceeded: bool = False
    isolation: str = 'linux_namespaces'
    error: str | None = None
    name: str | None = None

    @property
    def passed(self) -> bool:
        # A zero wrapper return code alone is not execution evidence.
        return (self.command_started and self.exit_code == 0 and not self.timed_out
                and not self.output_limit_exceeded and self.error is None)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), 'passed': self.passed}
