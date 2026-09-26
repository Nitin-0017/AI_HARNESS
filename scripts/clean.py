"""Clean only harness-owned build artifacts. Preserve .runs and all targets."""
from pathlib import Path
import os
import shutil


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for name in (".venv", "build", "dist"):
        candidate = root / name
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
        elif candidate.exists():
            shutil.rmtree(candidate)
    for name in ("src", "tests", "scripts"):
        base = root / name
        if base.is_symlink():
            continue
        for directory, dirs, _ in os.walk(base, followlinks=False):
            for child in list(dirs):
                if child == "__pycache__" or child.endswith(".egg-info"):
                    candidate = Path(directory, child)
                    if candidate.is_symlink():
                        candidate.unlink()
                    else:
                        shutil.rmtree(candidate)
                    dirs.remove(child)
    print("Cleaned .venv, build artifacts, and caches; logs and target workspaces were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
