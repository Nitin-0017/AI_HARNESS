"""Six real repository tools; no model logic and no invented execution results."""
from __future__ import annotations

from dataclasses import replace
import fnmatch
from functools import wraps
import os
import threading
from typing import Any, Callable

from .errors import BudgetExceeded
from .execution import NamespaceRunner
from .git_tools import inspect_changes
from .patching import apply
from .repository_io import RepositoryIO, digest, path_parts
from .state import RunState
from .telemetry import Redactor
from .tool_types import CheckSpec, PatchEdit, ToolError, ToolLimits, positive
from .workspace import Workspace


def action(function: Callable) -> Callable:
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            self.io.ensure_root()
            if self.state is not None:
                self.state.check_time_budget()
                if self.state.usage.tool_calls >= self.state.budgets.max_tool_calls:
                    raise BudgetExceeded('max_tool_calls exhausted')
                self.state.usage.tool_calls += 1
            if self.emit is not None:
                self.emit('tool.begin', tool=function.__name__)
            try:
                result = function(self, *args, **kwargs)
            except Exception:
                if self.state is not None:
                    self.state.usage.failures += 1
                if self.emit is not None:
                    self.emit('tool.failed', tool=function.__name__, level='ERROR')
                raise
            if self.emit is not None:
                self.emit('tool.complete', tool=function.__name__)
            return self.redactor.clean(result)
    return wrapped


class RepositoryTools:
    def __init__(self, workspace: Workspace, *, limits: ToolLimits | None = None,
                 checks: tuple[CheckSpec, ...] = (), state: RunState | None = None,
                 redactor: Redactor | None = None, emit: Callable | None = None):
        self.limits = limits or ToolLimits()
        self.redactor = redactor or Redactor(os.environ.get('AI_API_KEY'))
        self.state, self.emit = state, emit
        self._lock = threading.RLock()
        if any(not isinstance(spec, CheckSpec) for spec in checks):
            raise ToolError('Checks must be validated CheckSpec objects')
        self.checks = {spec.name: spec for spec in checks}
        if len(self.checks) != len(checks):
            raise ToolError('Check names must be unique')
        self.io = RepositoryIO(workspace, self.limits)
        self.runner = NamespaceRunner(self.io, self.limits, self.redactor,
                                      remaining_time=(lambda: state.budgets.max_seconds - state.elapsed_seconds) if state else None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self.io.close()

    @action
    def list_files(self, path: str = '.', pattern: str = '*') -> dict:
        if not isinstance(pattern, str) or len(pattern) > 512:
            raise ToolError('File pattern must be a bounded glob string')
        paths, skipped = self.io.walk(path)
        matching = [p for p in paths if fnmatch.fnmatchcase(p, pattern)]
        result, size = [], 0
        for item in matching:
            size += len(item.encode('utf-8')) + 4
            if size > self.limits.max_output_bytes:
                break
            result.append(item)
        return {'files': result, 'count': len(result), 'truncated': len(result) != len(matching),
                'skipped': skipped[:100], 'skipped_count': len(skipped)}

    @action
    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict:
        positive(start_line, 'start_line', integer=True)
        if end_line is not None:
            positive(end_line, 'end_line', integer=True)
            if end_line < start_line:
                raise ToolError('end_line must not precede start_line')
        data, _ = self.io.read_bytes(path)
        try:
            text = data.decode('utf-8')
        except UnicodeError as exc:
            raise ToolError('read_file supports UTF-8 text only') from exc
        if '\0' in text:
            raise ToolError('read_file does not return binary content')
        lines = text.splitlines(keepends=True)
        if start_line > len(lines) + 1:
            raise ToolError('start_line is beyond the file')
        end = min(end_line or len(lines), len(lines))
        selected = ''.join(lines[start_line - 1:end]).encode('utf-8')
        clipped = selected[:self.limits.max_output_bytes]
        return {'path': '/'.join(path_parts(path)), 'content': clipped.decode('utf-8', errors='ignore'),
                'start_line': start_line, 'end_line': end, 'total_lines': len(lines),
                'sha256': digest(data), 'size_bytes': len(data), 'truncated': len(clipped) < len(selected)}

    @action
    def search_code(self, query: str, path: str = '.', pattern: str = '*', case_sensitive: bool = True) -> dict:
        if not isinstance(query, str) or not query or len(query) > 4096 or '\0' in query or '\n' in query:
            raise ToolError('Search query must be a nonempty single-line literal string')
        if not isinstance(case_sensitive, bool) or not isinstance(pattern, str) or len(pattern) > 512:
            raise ToolError('Invalid search options')
        files, skipped = self.io.walk(path)
        matches: list[dict] = []
        scanned = total_bytes = output_bytes = 0
        truncated = False
        needle = query if case_sensitive else query.casefold()
        for file in files:
            if not fnmatch.fnmatchcase(file, pattern):
                continue
            try:
                data, _ = self.io.read_bytes(file)
                total_bytes += len(data)
                if total_bytes > self.limits.max_scan_bytes:
                    truncated = True
                    break
                text = data.decode('utf-8')
                if '\0' in text:
                    raise UnicodeError
            except UnicodeError:
                skipped.append({'path': file, 'reason': 'binary_or_non_utf8'})
                continue
            scanned += 1
            for number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    # Long source lines cannot consume unlimited context.
                    position = haystack.find(needle)
                    excerpt = line[max(0, position - 160):position + min(len(query), 1000) + 160]
                    length = len(excerpt.encode('utf-8')) + len(file.encode()) + 64
                    if len(matches) >= self.limits.max_search_matches or output_bytes + length > self.limits.max_output_bytes:
                        truncated = True
                        break
                    matches.append({'path': file, 'line': number, 'text': excerpt,
                                    'line_truncated': len(excerpt) < len(line)})
                    output_bytes += length
            if truncated:
                break
        return {'matches': matches, 'files_scanned': scanned, 'truncated': truncated,
                'skipped': skipped[:100], 'skipped_count': len(skipped), 'match_mode': 'literal'}

    @action
    def apply_patch(self, edits: list[PatchEdit | dict], dry_run: bool = False) -> dict:
        return apply(self.io, edits, dry_run=dry_run)

    @action
    def run_checks(self, names: list[str] | None = None) -> dict:
        if names is None:
            names = list(self.checks)
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
            raise ToolError('Provide one or more names of trusted configured checks')
        if len(set(names)) != len(names) or any(n not in self.checks for n in names):
            raise ToolError('Unknown or duplicate check name; arbitrary command arguments are not accepted')
        results = []
        for name in names:
            spec = self.checks[name]
            if self.state is not None:
                self.state.check_time_budget()
                if self.state.usage.test_executions >= self.state.budgets.max_test_executions:
                    raise BudgetExceeded('max_test_executions exhausted')
                remaining = self.state.budgets.max_seconds - self.state.elapsed_seconds
                spec = replace(spec, timeout_seconds=min(spec.timeout_seconds or self.limits.command_timeout_seconds, remaining))
            result = self.runner.run(spec)
            if self.state is not None:
                if result.command_started:
                    self.state.usage.test_executions += 1
                if not result.passed:
                    self.state.usage.failures += 1
            results.append(result.to_dict())
        return {'results': results, 'all_passed': bool(results) and all(r['passed'] for r in results),
                'verification_status': 'NOT_ASSESSED',
                'note': 'Exit codes are execution evidence, not an autonomous task-verification verdict.'}

    @action
    def get_changes(self) -> dict:
        return inspect_changes(self.io, self.runner, self.redactor)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict:
        if not isinstance(name, str) or name not in {'list_files', 'search_code', 'read_file', 'apply_patch', 'run_checks', 'get_changes'}:
            raise ToolError('Unknown repository tool')
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ToolError('Tool arguments must be a JSON object')
        try:
            return getattr(self, name)(**arguments)
        except TypeError as exc:
            raise ToolError('Invalid or unknown tool argument') from exc
