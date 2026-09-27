# Phase 2 repository tool contract

## Scope

This phase implements tool execution, not an agent or an autonomous verifier.
The current model-independent Python API is `RepositoryTools(Workspace.open(...))`.
Use it as a context manager to close its root directory descriptor. The existing
CLI adds `--tool NAME --tool-args JSON` to execute one explicit request.

## Signatures

```python
list_files(path=".", pattern="*")
search_code(query, path=".", pattern="*", case_sensitive=True)
read_file(path, start_line=1, end_line=None)
apply_patch(edits, dry_run=False)
run_checks(names=None)
get_changes()
```

`call(name, arguments)` accepts exactly these tools. Unknown tools, extra
arguments, invalid paths, and unsupported data fail with controlled errors.
Read/list/search support POSIX hosts. Process-backed checks and Git inspection
require the Linux isolation backend; no unrestricted fallback is present.

## Path and I/O contract

A tool path is relative to the configured target, not the harness or process CWD.
Absolute paths, Windows drives, backslashes, NUL/control characters, parent (`..`)
components, symlink components, hardlinked files, and nonregular file reads are
rejected. File contents are opened through directory descriptors with
`O_NOFOLLOW` and checked with `fstat` before and after reading. File and directory
counts, depth, scan volume, match count, and output are bounded. UTF-8 file bytes
are not newline-normalized. Binary/invalid UTF-8 reads are rejected; search skips
such files explicitly.

Listings/searches normally omit `.git`, `.venv`, `venv`, `__pycache__`, common
caches and `node_modules`. Credential-shaped paths such as `.env*` except
`.env.example`, `.ssh`, `.aws`, `.gnupg`, `*.pem`, `*.key`, `*.p12`, `*.pfx` are
protected. The Phase 1 `Workspace.resolve_path` helper can still canonicalize an
internal symlink, but the actual Phase 2 I/O layer applies the stricter no-links
policy. Execution preflight checks even directories ignored by search.

Descriptors pin traversed objects but do not provide transactions or defend
against a separate privileged host process concurrently moving a directory
outside the workspace. Give a run exclusive use of a disposable checkout.

## Exact patch format

Replacement:

```json
{"edits":[{"path":"app.py","operation":"replace","old":"return 1","new":"return 2"}]}
```

The default operation is `replace`. Old text must be nonempty and match exactly
once. New text must differ. An optional lowercase `expected_sha256`, from
`read_file`, rejects stale edits. No fuzzy matching is performed.

Creation:

```json
{"edits":[{"path":"tests/test_edge.py","operation":"create","new":"# regression test\n"}]}
```

The parent directory must already exist. Creation never replaces an existing
file. Empty `old` and no expected hash are required.

Deletion requires `operation="delete"`, no new text and the exact current
`expected_sha256`. An optional `old` value must match the whole original file.
Paths cannot be repeated within one batch, even through `./` aliases.

All batch members are validated before publication. Writes are staged beside
the destinations, synced, and atomically published one file at a time. Existing
file mode bits are preserved without granting setuid/setgid bits. Originals are
rechecked immediately before publication. I/O failures trigger best-effort
rollback; rollback failure is an explicit error requiring diff inspection.
**A multi-file batch is not a filesystem transaction or crash-atomic operation.**
Concurrent host writers remain unsupported. Dry runs validate without staging.

The patch layer validates the requested text change, not language semantics.
Syntactically invalid Python is a real patch and must be discovered by a real
configured check; tests exercise both syntax failure and repair.

## Check execution

Only a trusted registry of `CheckSpec(name, argv, cwd, timeout_seconds)` objects is
accepted. Configuration is loaded from a harness-owned file outside the target.
A tool caller supplies check names, never an arbitrary shell string. Executable
paths must be absolute approved system-bin paths or `{python}`. Shells and
privilege wrappers are rejected. Configured interpreters can run programs; the
registry is an operator trust boundary, not a general program validator.

The runner uses `Popen` with argument arrays, `shell=False`, `stdin=DEVNULL`, closed
inherited descriptors (apart from a private launcher-status pipe), a clean
allowlisted environment, and a new process session. It concurrently drains both
pipes into bounded buffers. A combined stdout/stderr output overflow kills the
run and is reported. Invalid output bytes are decoded with explicit replacement.
Duration uses a monotonic clock. Timeout includes startup/preflight elapsed time
when deriving the process deadline; OS process creation or pathological
filesystem delays cannot be made perfectly interruptible from this layer.

`unshare` creates private user/mount/PID/network/IPC/UTS namespaces. A private
launcher mounts only the target, read-only runtime directories and small tmpfs
scratch. It changes root, drops capability sets and the capability bounding set,
sets `no_new_privs`, and execs the actual target command. The launcher status pipe
closes on exec and is not available to the check. The parent uses that status plus
actual process results, not a string printed by target code, to determine whether
launch succeeded. A check can itself be dishonest about its assertions; a
zero-exit check is never a complete task-verification verdict here.

Timeout/output-limit cleanup kills the process group. `unshare --kill-child=KILL`
also tears down the namespace's PID 1; descendant processes are terminated even
when they started a new session. The tests include that case.

`/workspace` is the only writable host-backed data tree. System binary/library
roots are explicit read-only dependencies, not secret storage. `/tmp` is private
32 MiB memory scratch; namespace-root setup is a private 64 MiB tmpfs. Host `/proc`
is not exposed. Existing usual credential paths are masked. The child does not
inherit `AI_API_KEY`, cloud credentials, `PYTHONPATH`, `LD_PRELOAD`, or arbitrary
operator environment values. Host environment-dependent checks must be adapted
to the declared execution contract rather than silently inheriting credentials.

Per-process core, descriptor, file-size, and address-space limits are imposed.
There is no cgroup-wide aggregate process/memory/disk/CPU quota. Network namespaces
block access to the host listener in the regression suite; this is not a claim
against kernel exploits. Provision a dedicated evaluation host for hostile code.

## Git status and diffs

`get_changes` runs real Git commands inside a read-only sandbox. Repository-local
config is masked, global/system config is disabled, external diff/textconv helpers
and fsmonitor are disabled, and submodule traversal is suppressed. The index is
not refreshed or modified. `.git` must be a real directory at the target root.
Linked worktrees, alternates, shallow Git checkouts and unusual object formats
are deliberately unsupported; errors never become an invented clean status.

Results keep these distinct:

- porcelain status (index and worktree status codes);
- unstaged Git diff (worktree vs index);
- staged Git diff (index vs HEAD, including an unborn repository);
- untracked text-file diffs computed from their actual contents.

Sensitive file contents are withheld and listed as omitted; binary untracked
files are marked rather than decoded. Truncation is explicit. Redacted/truncated
or unterminated-line display diffs are for inspection and are not guaranteed
round-trippable patches. `get_changes` is not a run-start snapshot or proof that
all changes were authored by the current harness run. A later verification phase
must bind test evidence to a final repository snapshot.

## Resource and evidence integration

The existing state object records tool attempts, started check commands and
failures. Tool count, check count and elapsed-time limits prevent new work after
exhaustion. The global command-timeout budget caps each configured timeout.
All model counters remain zero. A tool log records start/completion/failure,
while the tool-result artifact carries actual stdout/stderr/code/duration.

Passing a named command does not prove any particular tests ran or that they
were meaningful. Phase 2 deliberately emits no `VERIFIED` task status. A later
verification component must detect zero-test runs and stale/insufficient evidence.

## Technical references used during implementation

- Python subprocess reference: https://docs.python.org/3/library/subprocess.html
- Python descriptor-relative OS APIs: https://docs.python.org/3/library/os.html
- util-linux unshare manual: https://man7.org/linux/man-pages/man1/unshare.1.html
- Linux mount interface: https://man7.org/linux/man-pages/man2/mount.2.html
- Git diff: https://git-scm.com/docs/git-diff
- Git porcelain status: https://git-scm.com/docs/git-status

These document the underlying mechanisms, not a third-party audit or certification
of this project. Actual project execution evidence is in `evidence/phase2/`.
