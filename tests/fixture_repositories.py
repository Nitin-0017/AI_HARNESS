"""Real disposable repository fixtures, used by tests and the explicit demo only."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess

FIXTURES = Path(__file__).parent / 'fixtures'


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'LC_ALL': 'C.UTF-8'}
    return subprocess.run(['/usr/bin/git', '-C', str(root), *args], shell=False, env=env,
                          capture_output=True, text=True, timeout=10, check=True)


def create_fixture(root: Path, scenario: str = 'average', *, commit: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / 'average', root, dirs_exist_ok=True)
    (root / '.gitignore').write_text('__pycache__/\n*.pyc\n.pytest_cache/\n', encoding='utf-8')
    if scenario == 'multi_file':
        (root / 'constants.py').write_text('FACTOR = 2\n')
        (root / 'app.py').write_text('from constants import FACTOR\ndef double(n):\n    return n + FACTOR\n')
    elif scenario == 'syntax_error':
        (root / 'broken.py').write_text('def broken(:\n    return 1\n')
    elif scenario == 'command_failure':
        (root / 'check.py').write_text('import sys\nprint("intentional failure", file=sys.stderr)\nsys.exit(7)\n')
    elif scenario == 'timeout':
        (root / 'check.py').write_text('import time\nprint("started", flush=True)\ntime.sleep(30)\n')
    elif scenario == 'irrelevant_files':
        (root / 'node_modules').mkdir()
        (root / 'node_modules' / 'ignore.txt').write_text('average should not be searched here\n')
        (root / 'notes.txt').write_text('Not related to the task\n')
    elif scenario not in {'average', 'multi_file'}:
        raise ValueError('Unknown fixture scenario')
    git(root, 'init', '-q')
    if commit:
        git(root, 'add', '.')
        git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost', 'commit', '-qm', 'Fixture baseline')
    return root
