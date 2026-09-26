"""Canonical workspace guards; these are not an operating-system sandbox."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import os

from .errors import WorkspaceError


def overlaps(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def open(cls, candidate: Path, harness_root: Path) -> Workspace:
        try:
            root = candidate.resolve(strict=True)
            protected = harness_root.resolve(strict=True)
            if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
                raise WorkspaceError("Target workspace must be an existing readable directory")
            if overlaps(root, protected):
                raise WorkspaceError(
                    "Target workspace must be separate from the harness: equal, nested, and ancestor paths are forbidden"
                )
            return cls(root)
        except (OSError, ValueError, RuntimeError) as exc:
            raise WorkspaceError("Cannot resolve the target workspace directory") from exc

    def resolve_path(self, relative: str, *, must_exist: bool = True) -> Path:
        """Validate a future tool path, without reading or modifying its contents.

        Resolving a path is not a defense against concurrent filesystem changes;
        later tool/process isolation must address that separate boundary.
        """
        if not isinstance(relative, str) or not relative or "\0" in relative or "\\" in relative:
            raise WorkspaceError("Workspace paths must be nonempty relative paths")
        path = Path(relative)
        if path.is_absolute() or PureWindowsPath(relative).drive or ".." in path.parts:
            raise WorkspaceError("Absolute paths and parent traversal are forbidden")
        try:
            resolved = (self.root / path).resolve(strict=must_exist)
        except (OSError, ValueError, RuntimeError) as exc:
            raise WorkspaceError("Cannot resolve the requested workspace path") from exc
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError("Requested path or symlink escapes the target workspace")
        return resolved


def validate_output_dir(candidate: Path, *, workspace: Workspace | None, harness_root: Path) -> Path:
    """Keep harness-owned logs out of targets and reject symlinked output paths."""
    try:
        absolute = Path(os.path.abspath(candidate))
        for part in (absolute, *absolute.parents):
            if part.is_symlink():
                raise WorkspaceError("Log output paths must not contain symlinks")
            if part.exists() and not part.is_dir():
                raise WorkspaceError("Log output path contains a non-directory component")
        output = absolute.resolve()
        harness_root = harness_root.resolve()
        if output == harness_root or harness_root.is_relative_to(output):
            raise WorkspaceError("Log output must not be the harness root or an ancestor directory")
        if workspace is not None and overlaps(output, workspace.root):
            raise WorkspaceError("Log output and target workspace must not overlap")
        for protected_name in ("src", "tests", "scripts", ".venv", ".git"):
            if output.is_relative_to(harness_root / protected_name):
                raise WorkspaceError("Log output must not be inside harness code or environment directories")
        return output
    except (OSError, ValueError, RuntimeError) as exc:
        raise WorkspaceError("Cannot validate the log output directory") from exc
