"""Real, bounded subprocess execution; no shell and no unsandboxed fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable

from .errors import BudgetExceeded

from .repository_io import RepositoryIO, path_parts, protected
from .telemetry import Redactor
from .tool_types import CheckSpec, ExecutionBlocked, ProcessResult, ToolLimits


def child_environment() -> dict[str, str]:
    """Allowlist, not a copy of the parent's environment or API credentials."""
    return {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMPDIR': '/tmp', 'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_PAGER': 'cat'}


class NamespaceRunner:
    """Linux user/mount/PID/network/IPC namespaces, plus a reduced filesystem.

    System /usr, /bin, /sbin and library roots are read-only dependencies.
    /workspace is the sole writable host tree. /tmp is private memory scratch.
    Kernel exploits and hostile concurrent host writers are outside this boundary.
    """
    def __init__(self, io: RepositoryIO, limits: ToolLimits, redactor: Redactor,
                 scratch_dir: Path | None = None, remaining_time: Callable[[], float] | None = None):
        self.io, self.limits, self.redactor = io, limits, redactor
        self.scratch_dir = scratch_dir
        self.remaining_time = remaining_time
        if scratch_dir is not None:
            root = scratch_dir.resolve()
            if root == io.workspace.root or root.is_relative_to(io.workspace.root):
                raise ExecutionBlocked('Runner scratch must be outside the target workspace')

    def run(self, check: CheckSpec, *, readonly: bool = False, git_mode: bool = False) -> ProcessResult:
        start = time.monotonic()
        self.io.execution_preflight()
        cwd = '/'.join(path_parts(check.cwd, root_ok=True)) or '.'
        with self.io.directory(cwd):
            pass
        argv = ['/usr/bin/python3' if a == '{python}' else a for a in check.argv]
        if not sys.platform.startswith('linux'):
            raise ExecutionBlocked('Isolated checks require Linux with user namespaces; no local fallback is enabled')
        unshare = shutil.which('unshare', path='/usr/bin:/bin')
        if unshare is None:
            raise ExecutionBlocked('Install util-linux unshare to execute isolated checks')
        timeout = min(check.timeout_seconds or self.limits.command_timeout_seconds,
                      self.limits.command_timeout_seconds)
        if self.remaining_time is not None:
            remaining = self.remaining_time()
            if remaining <= 0:
                raise BudgetExceeded('Run time budget exhausted before subprocess execution')
            timeout = min(timeout, remaining)
        files, _ = self.io.walk(include_ignored=True)
        masks = []
        for file in files:
            parts = Path(file).parts
            for index, part in enumerate(parts):
                if protected((part,)):
                    masks.append('/'.join(parts[:index + 1]))
                    break
        # A .git directory may be empty and thus absent from the file list.
        if (self.io.workspace.root / '.git').exists():
            masks.append('.git')
        with tempfile.TemporaryDirectory(prefix='harness-sandbox-', dir=self.scratch_dir) as temporary:
            read_status, write_status = os.pipe()
            spec = {'root': temporary, 'workspace': str(self.io.workspace.root), 'cwd': cwd,
                    'argv': argv, 'readonly': readonly, 'git_mode': git_mode,
                    'masked_paths': sorted(set(masks)), 'env': child_environment()}
            command = [unshare, '--user', '--map-root-user', '--mount', '--pid', '--net',
                       '--ipc', '--uts', '--fork', '--kill-child=KILL',
                       sys.executable, '-I', '-S', str(Path(__file__).with_name('_sandbox.py')),
                       str(write_status), json.dumps(spec)]
            try:
                process = subprocess.Popen(command, shell=False, cwd=self.io.workspace.root,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, env=child_environment(),
                                           start_new_session=True, close_fds=True, pass_fds=(write_status,))
            except OSError as exc:
                os.close(read_status)
                raise ExecutionBlocked('Cannot start the isolated command runner') from exc
            finally:
                os.close(write_status)
            try:
                out, err, meta, timed_out, overflow = self._collect(process, read_status, start + timeout)
            except BaseException:
                self._kill(process)
                process.wait()
                raise
            finally:
                os.close(read_status)
                process.stdout.close()
                process.stderr.close()
            started = False
            error = None
            for line in meta.decode('utf-8', errors='replace').splitlines():
                try:
                    payload = json.loads(line)
                    if payload.get('ready'):
                        started = True
                    if payload.get('error'):
                        started = False
                        error = f"{payload['error']} ({payload.get('type')}, errno={payload.get('errno')})"
                except (ValueError, AttributeError):
                    error = 'Invalid sandbox launcher status'
            if not started and error is None:
                error = 'Isolation could not be established; user/mount/network namespaces may be disabled'
            return ProcessResult(argv, cwd, self.redactor.text(out.decode('utf-8', errors='replace')),
                                 self.redactor.text(err.decode('utf-8', errors='replace')),
                                 process.returncode, round(time.monotonic() - start, 6), started,
                                 timed_out=timed_out, output_limit_exceeded=overflow,
                                 error=error, name=check.name)

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # unshare --kill-child also kills PID 1 if the wrapper is terminated;
        # the kernel then tears down descendants, even those that used setsid().

    def _collect(self, process: subprocess.Popen, status_fd: int,
                 deadline: float) -> tuple[bytes, bytes, bytes, bool, bool]:
        output = {'stdout': bytearray(), 'stderr': bytearray(), 'status': bytearray()}
        timed_out = overflow = False
        total = 0
        with selectors.DefaultSelector() as selector:
            for fd, kind in ((process.stdout.fileno(), 'stdout'), (process.stderr.fileno(), 'stderr'),
                             (status_fd, 'status')):
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_READ, kind)
            while selector.get_map():
                now = time.monotonic()
                if now >= deadline and not timed_out:
                    timed_out = True
                    self._kill(process)
                for key, _ in selector.select(0.03):
                    try:
                        chunk = os.read(key.fd, 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    if key.data == 'status':
                        output['status'].extend(chunk[:max(0, 8192 - len(output['status']))])
                        continue
                    room = max(0, self.limits.max_output_bytes - total)
                    output[key.data].extend(chunk[:room])
                    total += len(chunk[:room])
                    if len(chunk) > room and not overflow:
                        overflow = True
                        self._kill(process)
                if timed_out and time.monotonic() > deadline + 2:
                    break  # a hard drain bound even if a broken OS retains a pipe
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill(process)
                process.wait(timeout=2)
        return bytes(output['stdout']), bytes(output['stderr']), bytes(output['status']), timed_out, overflow
