"""Offline, idempotent setup. No pip, credential, network, or target repo needed."""
from pathlib import Path
import os
import subprocess
import sys
import venv


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required", file=sys.stderr)
        return 1
    if os.name != "posix":
        print("Use Linux, macOS, or WSL for this Makefile-based setup", file=sys.stderr)
        return 1
    target = root / ".venv"
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        print("Refusing to replace a symlinked or non-directory .venv", file=sys.stderr)
        return 1
    try:
        venv.EnvBuilder(with_pip=False).create(target)
        python = target / "bin" / "python"
        site = subprocess.check_output(
            [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True, timeout=30,
        ).strip()
        Path(site, "ai_harness_source.pth").write_text(str(root / "src") + "\n", encoding="utf-8")
        subprocess.run([str(python), "-I", "-c",
                        "import ai_harness; print('Harness import OK:', ai_harness.__version__)"],
                       check=True, timeout=30)
        (target / ".harness-ready").write_text("0.2.0\n", encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        print("Setup failed; check the Python venv installation and filesystem permissions.", file=sys.stderr)
        return 1
    print("Setup complete: standard-library runtime and tests; no packages downloaded.")
    print("Phase 2 execution requires Linux, util-linux unshare, /usr/bin/python3 and Git.")
    print("Run make test. Set AI_API_KEY in the environment, then run make run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
