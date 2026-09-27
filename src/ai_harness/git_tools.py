"""Actual Git status and diffs, executed with an isolated, sanitized Git config."""
from __future__ import annotations

import difflib
import os
import stat

from .execution import NamespaceRunner
from .repository_io import RepositoryIO, path_parts, protected
from .telemetry import Redactor
from .tool_types import CheckSpec, ToolError


def inspect_changes(io: RepositoryIO, runner: NamespaceRunner, redactor: Redactor) -> dict:
    with io.directory('.', internal=True) as root:
        try:
            info = os.stat('.git', dir_fd=root, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ToolError('get_changes requires a standalone Git repository at the workspace root') from exc
        if not stat.S_ISDIR(info.st_mode):
            raise ToolError('Linked Git worktrees and external Git directories are not supported')
    with io.directory('.git', internal=True):
        pass
    for forbidden in ('.git/commondir', '.git/objects/info/alternates', '.git/shallow'):
        if (io.workspace.root / forbidden).exists():
            raise ToolError('Shared object stores and shallow/linked repositories are not supported in Phase 2')
    # The launcher masks local config. Require it to exist for the read-only bind.
    io.read_bytes('.git/config', internal=True)
    prefix = ('/usr/bin/git', '--no-pager', '--git-dir=/git', '--work-tree=/workspace',
              '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/nonexistent',
              '-c', 'core.untrackedCache=false', '-c', 'core.attributesFile=/dev/null',
              '-c', 'diff.external=', '-c', 'diff.renames=false', '-c', 'safe.directory=/workspace')
    evidence = []

    def git(name: str, *args: str) -> str:
        result = runner.run(CheckSpec(name, prefix + args), readonly=True, git_mode=True)
        evidence.append({'command': list(args), 'exit_code': result.exit_code,
                         'duration_seconds': result.duration_seconds, 'command_started': result.command_started})
        if not result.passed:
            raise ToolError('Git inspection failed, timed out, or exceeded output limits; no clean status is claimed')
        return result.stdout

    status_text = git('git-status', 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=all')
    tokens = status_text.split('\0')
    entries = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token:
            continue
        if len(token) < 4 or token[2] != ' ':
            raise ToolError('Cannot parse Git status output')
        code, path = token[:2], token[3:]
        path_parts(path)
        entry = {'path': path, 'index_status': code[0], 'worktree_status': code[1],
                 'untracked': code == '??'}
        if code[0] in 'RC' or code[1] in 'RC':
            if i >= len(tokens) or not tokens[i]:
                raise ToolError('Incomplete rename entry in Git status')
            path_parts(tokens[i])
            entry['original_path'] = tokens[i]
            i += 1
        entries.append(entry)
    omitted = [entry['path'] for entry in entries if protected(path_parts(entry['path']))]
    exclusions = tuple(':(exclude,literal)' + p for p in omitted)
    options = ('--no-color', '--no-ext-diff', '--no-textconv', '--no-renames', '--ignore-submodules=all')
    unstaged = git('git-unstaged', 'diff', *options, '--', '.', *exclusions)
    staged = git('git-staged', 'diff', '--cached', *options, '--', '.', *exclusions)
    untracked = []
    size = len(unstaged.encode()) + len(staged.encode())
    truncated = size > io.limits.max_output_bytes
    if truncated:
        allowance = io.limits.max_output_bytes
        unstaged = unstaged.encode()[:allowance].decode('utf-8', errors='ignore')
        allowance -= len(unstaged.encode())
        staged = staged.encode()[:allowance].decode('utf-8', errors='ignore')
        size = io.limits.max_output_bytes
    for entry in entries:
        if not entry['untracked'] or entry['path'] in omitted:
            continue
        path = entry['path']
        data, _ = io.read_bytes(path)
        try:
            text = data.decode('utf-8')
            if '\0' in text:
                raise UnicodeError
        except UnicodeError:
            untracked.append({'path': path, 'binary': True, 'diff': None})
            continue
        patch = ''.join(difflib.unified_diff([], text.splitlines(keepends=True),
                                            fromfile='/dev/null', tofile='b/' + path))
        if size + len(patch.encode()) > io.limits.max_output_bytes:
            truncated = True
            untracked.append({'path': path, 'diff': None, 'omitted': 'output limit'})
        else:
            untracked.append({'path': path, 'binary': False, 'diff': redactor.text(patch)})
            size += len(patch.encode())
    # Preserve the separation: Git's normal diff does not contain untracked files.
    result = {'backend': 'git', 'comparison': 'index/worktree and HEAD/index; not a run-start snapshot',
              'status': entries, 'clean': not entries, 'unstaged_diff': unstaged,
              'staged_diff': staged, 'untracked_files': untracked,
              'omitted_sensitive_paths': omitted, 'truncated': truncated, 'execution': evidence}
    return redactor.clean(result)
