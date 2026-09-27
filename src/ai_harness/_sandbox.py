"""Private Linux namespace launcher. Run only through execution.py.

Trusted helper code runs before chroot. The target executable sees no host home,
root, harness, or credentials. Only /workspace is a writable host bind mount.
/usr and library/bin directories are explicit read-only runtime dependencies.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import resource
import sys

MS_RDONLY, MS_NOSUID, MS_NODEV = 1, 2, 4
MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 32, 4096, 16384, 1 << 18
libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
libc.mount.restype = ctypes.c_int


def mount(source: str | None, target: Path | str, kind: str | None = None,
          flags: int = 0, data: str | None = None) -> None:
    convert = lambda v: os.fsencode(v) if v is not None else None
    if libc.mount(convert(source), convert(target), convert(kind), flags, convert(data)):
        raise OSError(ctypes.get_errno(), 'Sandbox mount failed')


def bind(source: Path | str, destination: Path, *, readonly: bool = True, device: bool = False) -> None:
    source = Path(source)
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.touch(exist_ok=False)
    mount(str(source), destination, flags=MS_BIND)
    flags = MS_BIND | MS_REMOUNT | MS_NOSUID | (0 if device else MS_NODEV)
    mount(None, destination, flags=flags | (MS_RDONLY if readonly else 0))


def drop_capabilities() -> None:
    # Empty bounding set prevents uid 0 in this namespace regaining capabilities
    # after exec. no_new_privs also blocks setuid/file-capability elevation.
    if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'Cannot set no_new_privs')
    for capability in range(64):
        if libc.prctl(24, capability, 0, 0, 0) and ctypes.get_errno() != errno.EINVAL:
            raise OSError(ctypes.get_errno(), 'Cannot drop bounding capability')

    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                    ('inheritable', ctypes.c_uint32)]

    if libc.capset(ctypes.byref(Header(0x20080522, 0)), ctypes.byref((Data * 2)())):
        raise OSError(ctypes.get_errno(), 'Cannot drop capabilities')


def launch(spec: dict, status_fd: int) -> None:
    if os.getpid() != 1 or os.geteuid() != 0:
        raise RuntimeError('Sandbox helper must be PID 1 in a mapped user namespace')
    root = Path(spec['root'])
    workspace = Path(spec['workspace'])
    # Do not propagate private mounts to the host or another namespace.
    mount(None, '/', flags=MS_REC | MS_PRIVATE)
    mount('tmpfs', root, 'tmpfs', MS_NOSUID | MS_NODEV, 'size=64m,mode=755')
    for name in ('usr', 'bin', 'sbin', 'lib', 'lib64'):
        source = Path('/') / name
        if source.exists():
            bind(source, root / name)
    for name in ('workspace', 'tmp', 'dev', 'etc'):
        (root / name).mkdir(exist_ok=True)
    mount('tmpfs', root / 'tmp', 'tmpfs', MS_NOSUID | MS_NODEV, 'size=32m,mode=1777')
    for name in ('null', 'zero', 'random', 'urandom'):
        source = Path('/dev') / name
        if source.exists():
            bind(source, root / 'dev' / name, readonly=False, device=True)
    # Host identity files, DNS configuration and /proc are deliberately absent.
    (root / 'etc' / 'passwd').write_text('root:x:0:0:Sandbox:/tmp:/bin/false\n')
    (root / 'etc' / 'group').write_text('root:x:0:\n')
    if Path('/etc/ld.so.cache').is_file():
        bind('/etc/ld.so.cache', root / 'etc' / 'ld.so.cache')
    bind(workspace, root / 'workspace', readonly=spec.get('readonly', False))
    if spec.get('git_mode'):
        bind(workspace / '.git', root / 'git')
        # Local repository config is not trusted. Mask it rather than letting
        # include directives, filters, fsmonitor, or hooks execute on the host.
        config = root / 'etc' / 'git-safe-config'
        config.write_text('[core]\nrepositoryformatversion = 0\nfilemode = true\nbare = false\n')
        bind(config, root / 'git' / 'config')
    else:
        # Checks cannot rewrite Git metadata or read usual credential paths.
        for relative in spec.get('masked_paths', []):
            destination = root / 'workspace' / relative
            if destination.is_dir():
                mount('tmpfs', destination, 'tmpfs', MS_RDONLY | MS_NOSUID | MS_NODEV, 'size=4k')
            else:
                empty = root / 'etc' / 'empty'
                empty.touch(exist_ok=True)
                bind(empty, destination)
    # The namespace root itself is read-only, as are the runtime bind mounts.
    mount(None, root, flags=MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV)
    os.chdir(root)
    os.chroot('.')
    os.chdir('/workspace' + (('/' + spec['cwd']) if spec['cwd'] != '.' else ''))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    # This is a per-process memory ceiling, not a cgroup-wide aggregate limit.
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    drop_capabilities()
    os.set_inheritable(status_fd, False)
    os.write(status_fd, b'{"ready":true}\n')
    os.execve(spec['argv'][0], spec['argv'], spec['env'])


def main() -> int:
    status_fd = int(sys.argv[1])
    try:
        spec = json.loads(sys.argv[2])
        launch(spec, status_fd)
    except Exception as exc:
        # Do not echo request strings, paths, or environment values.
        message = {'error': 'Sandbox setup or executable launch failed', 'type': type(exc).__name__,
                   'errno': getattr(exc, 'errno', None)}
        try:
            os.write(status_fd, (json.dumps(message) + '\n').encode())
        except OSError:
            pass
        print('Sandbox setup or executable launch failed.', file=sys.stderr)
        return 126
    return 126


if __name__ == '__main__':
    raise SystemExit(main())
