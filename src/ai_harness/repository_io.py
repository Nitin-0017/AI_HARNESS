"""Descriptor-relative filesystem access. Symlinks and hardlinks fail closed.

Require an exclusively owned disposable workspace: descriptors do not stop a
separate privileged host process from moving directories during an operation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import PurePosixPath, PureWindowsPath
import stat
from typing import Iterator

from .tool_types import PathViolation, ToolError, ToolLimits
from .workspace import Workspace

IGNORED_DIRS = frozenset({'.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
                          'node_modules', '.mypy_cache', '.ruff_cache'})
PROTECTED = frozenset({'.git', '.ssh', '.aws', '.gnupg', '.netrc', '.npmrc', '.pypirc',
                       'credentials.json', 'secrets.json', 'id_rsa', 'id_ed25519'})


def path_parts(path: str, *, root_ok: bool = False) -> tuple[str, ...]:
    if (not isinstance(path, str) or not path or len(path) > 4096 or '\\' in path
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in path)
            or PureWindowsPath(path).drive or PurePosixPath(path).is_absolute()):
        raise PathViolation('Use a bounded, workspace-relative POSIX path without control characters')
    if '..' in path.split('/'):
        raise PathViolation('Parent traversal is forbidden')
    parts = tuple(p for p in path.split('/') if p not in ('', '.'))
    if not parts and not root_ok:
        raise PathViolation('A file path is required')
    return parts


def protected(parts: tuple[str, ...]) -> bool:
    return any(p.lower() in PROTECTED or (p.startswith('.env') and p != '.env.example')
               or p.endswith(('.pem', '.key', '.p12', '.pfx')) for p in parts)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RepositoryIO:
    def __init__(self, workspace: Workspace, limits: ToolLimits):
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
            raise ToolError('Repository tools require POSIX descriptor-relative file access')
        self.workspace = workspace
        self.limits = limits
        self._flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            self._fd = os.open(workspace.root, self._flags)
            info = os.fstat(self._fd)
            self._identity = (info.st_dev, info.st_ino)
        except OSError as exc:
            raise PathViolation('Cannot open the target workspace safely') from exc

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def ensure_root(self) -> None:
        if self._fd < 0:
            raise ToolError('Repository tools are closed')
        try:
            info = os.stat(self.workspace.root, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != self._identity or not stat.S_ISDIR(info.st_mode):
                raise PathViolation('Workspace root was replaced or moved')
        except OSError as exc:
            raise PathViolation('Workspace root is no longer available') from exc

    @contextmanager
    def directory(self, path: str = '.', *, internal: bool = False) -> Iterator[int]:
        self.ensure_root()
        parts = path_parts(path, root_ok=True)
        if not internal and protected(parts):
            raise PathViolation('Protected repository metadata or credential path')
        fd = os.dup(self._fd)
        try:
            for part in parts:
                new = os.open(part, self._flags, dir_fd=fd)
                os.close(fd)
                fd = new
                if os.fstat(fd).st_dev != self._identity[0]:
                    raise PathViolation('Cross-filesystem workspace traversal is forbidden')
            yield fd
        except OSError as exc:
            raise PathViolation('Directory is missing, inaccessible, or contains a link') from exc
        finally:
            os.close(fd)

    @contextmanager
    def parent(self, path: str, *, internal: bool = False) -> Iterator[tuple[int, str]]:
        parts = path_parts(path)
        if not internal and protected(parts):
            raise PathViolation('Protected repository metadata or credential path')
        with self.directory('/'.join(parts[:-1]) or '.', internal=internal) as fd:
            yield fd, parts[-1]

    def read_bytes(self, path: str, *, internal: bool = False) -> tuple[bytes, os.stat_result]:
        with self.parent(path, internal=internal) as (parent, name):
            return self.read_at(parent, name)

    def read_at(self, parent: int, name: str) -> tuple[bytes, os.stat_result]:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                self.validate_regular(before)
                if before.st_size > self.limits.max_file_bytes:
                    raise ToolError('File exceeds max_file_bytes')
                data = stream.read(self.limits.max_file_bytes + 1)
                after = os.fstat(stream.fileno())
                if len(data) > self.limits.max_file_bytes:
                    raise ToolError('File exceeds max_file_bytes')
                if self.signature(before) != self.signature(after):
                    raise ToolError('File changed during read; retry with an exclusive workspace')
                return data, after
        except OSError as exc:
            raise PathViolation('File is missing, inaccessible, or a symbolic link') from exc

    def validate_regular(self, info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_dev != self._identity[0]:
            raise PathViolation('Only ordinary, single-link files on the workspace filesystem are allowed')

    @staticmethod
    def signature(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)

    def walk(self, path: str = '.', *, include_ignored: bool = False,
             strict: bool = False) -> tuple[list[str], list[dict[str, str]]]:
        """Bounded traversal. Never follow links; strict is used before execution."""
        entries: list[str] = []
        skipped: list[dict[str, str]] = []
        visited = 0
        start = '/'.join(path_parts(path, root_ok=True))

        def descend(fd: int, prefix: str, depth: int) -> None:
            nonlocal visited
            if depth > self.limits.max_depth:
                raise ToolError('Repository exceeds max_depth')
            # scandir yields without first materializing an unlimited directory.
            with os.scandir(fd) as iterator:
                names = []
                for entry in iterator:
                    visited += 1
                    if visited > self.limits.max_files:
                        raise ToolError('Repository exceeds max_files; narrow the directory or increase its trusted limit')
                    names.append(entry.name)
            for name in sorted(names):
                relative = f'{prefix}/{name}' if prefix else name
                parts = path_parts(relative)
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or info.st_dev != self._identity[0]:
                    if strict:
                        raise PathViolation('Execution requires a workspace without links or nested filesystems')
                    skipped.append({'path': relative, 'reason': 'link_or_mount'})
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if not include_ignored and (name in IGNORED_DIRS or protected(parts)):
                        skipped.append({'path': relative, 'reason': 'excluded'})
                        continue
                    child = os.open(name, self._flags, dir_fd=fd)
                    try:
                        descend(child, relative, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    if include_ignored or not protected(parts):
                        entries.append(relative)
                    else:
                        skipped.append({'path': relative, 'reason': 'protected'})
                else:
                    if strict:
                        raise PathViolation('Execution requires no hardlinked files, sockets, FIFOs, or devices')
                    skipped.append({'path': relative, 'reason': 'hardlink_or_special'})
        try:
            with self.directory(path) as fd:
                descend(fd, start, 0)
        except OSError as exc:
            raise PathViolation('Directory changed or could not be read safely') from exc
        return entries, skipped

    def execution_preflight(self) -> None:
        self.walk(include_ignored=True, strict=True)
        # os.path.ismount does not detect same-device bind mounts; mountinfo does.
        mountinfo = '/proc/self/mountinfo'
        if os.path.exists(mountinfo):
            root = str(self.workspace.root).rstrip('/')
            with open(mountinfo, encoding='utf-8') as stream:
                for line in stream:
                    point = line.split(' ')[4]
                    for encoded, value in [('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')]:
                        point = point.replace(encoded, value)
                    if point.startswith(root + '/'):
                        raise PathViolation('Nested mounts are not permitted in a target workspace')
