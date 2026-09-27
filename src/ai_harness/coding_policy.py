"""Agent-only edit preconditions, layered over the existing guarded tools."""
from __future__ import annotations
import ast
from pathlib import PurePosixPath
import re

from .repository_io import digest, path_parts
from .tool_types import ToolError
from .repository_intelligence import RepositoryIndex
from .context import encode


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return any(x in {'test', 'tests'} for x in p.parts) or p.name.startswith('test_') or p.stem.endswith('_test')


class CodingPolicy:
    def __init__(self, tools, context, state):
        self.tools, self.context, self.state = tools, context, state
        self.inspected: dict[str, tuple[str, str]] = {}
        self.index = RepositoryIndex(state.budgets.max_context_items)

    def prime(self, files: list[str]) -> None:
        """Read a small deterministic source/test selection before the first decision."""
        self.index.structure(files)
        self.publish_index()
        task = self.state.task.text.casefold()
        terms = set(re.findall(r'[a-zA-Z_][a-zA-Z_0-9]*', task))
        candidates = [p for p in files if PurePosixPath(p).suffix in {'.py', '.js', '.ts', '.go', '.rs', '.java'}]
        candidates.sort(key=lambda p: (-len(terms & set(re.findall(r'[a-zA-Z_][a-zA-Z_0-9]*', p.casefold()))), is_test(p), p))
        selected = candidates[:4]
        tests = [p for p in candidates if is_test(p)]
        selected = list(dict.fromkeys(selected + tests[:2]))
        for path in selected:
            self.state.check_time_budget()
            try:
                data, _ = self.tools.io.read_bytes(path)
            except ToolError:
                continue
            if len(data) > min(32000, self.tools.limits.max_file_bytes):
                continue
            try:
                text = data.decode('utf-8')
            except UnicodeError:
                continue
            self.state.usage.inspection_reads += 1
            self.state.usage.inspection_bytes += len(data)
            result = {'path': path, 'content': text, 'sha256': digest(data), 'start_line': 1,
                      'end_line': len(text.splitlines()), 'total_lines': len(text.splitlines()), 'truncated': False}
            self.observe('read_file', {}, result)
            self.context.observe_tool('read_file', {}, result)

    def observe(self, action: str, arguments: dict, result: dict) -> None:
        if action == 'read_file' and not result.get('truncated'):
            path = result['path']
            self.inspected[path] = (result['sha256'], result['content'])
            if result.get('start_line', 1) == 1:
                self.index.read(path, result['content'])
                self.publish_index()
        elif action == 'apply_patch' and result.get('applied'):
            edits = {'/'.join(path_parts(e['path'])): e for e in arguments.get('edits', [])}
            for change in result.get('changes', []):
                path = change['path']; edit = edits.get(path, {})
                old = self.inspected.get(path, ('', ''))[1]
                op = edit.get('operation', 'replace')
                text = edit.get('new', '') if op == 'create' else old.replace(edit.get('old', ''), edit.get('new', ''), 1)
                if op == 'delete':
                    self.inspected.pop(path, None)
                    self.index.remove(path)
                else:
                    self.inspected[path] = (change['after_sha256'], text)
                    self.index.read(path, text)
            self.publish_index()

    def publish_index(self):
        # Receipts are bounded separately from selected context. Eviction forces
        # a real reread before a subsequent edit; it never authorizes stale code.
        while self.inspected and (len(self.inspected) > self.state.budgets.max_context_items or
               sum(len(p.encode()) + len(v[1].encode()) + 64 for p,v in self.inspected.items()) > self.state.budgets.max_context_memory_bytes):
            self.inspected.pop(next(iter(self.inspected)))
        summary = self.tools.redactor.clean(self.index.summary())
        self.state.repository_intelligence = summary
        self.context._put('REPOSITORY', 'intelligence', encode(summary))
        for row in summary['test_source_relationships'][:8]:
            self.context._put('TESTS', 'relationship:' + row['test'], encode(row), row['test'])

    def validate(self, action) -> None:
        if action.type != 'apply_patch':
            return
        for edit in action.arguments['edits']:
            path = '/'.join(path_parts(edit['path'])); op = edit.get('operation', 'replace')
            name = PurePosixPath(path).name
            if name in {'conftest.py', 'pytest.ini', 'tox.ini', 'sitecustomize.py', 'usercustomize.py', '.gitignore'}:
                raise ToolError('Agent edits to execution/discovery controls are blocked; use operator configuration')
            if is_test(path) and op == 'delete':
                raise ToolError('Deleting regression tests is prohibited')
            if op == 'create':
                continue
            current, _ = self.tools.io.read_bytes(path)
            inspected = self.inspected.get(path)
            if not inspected or inspected[0] != digest(current):
                raise ToolError('INSPECTION_REQUIRED: read the current file before editing it')
            if op == 'replace' and edit.get('old', '') not in inspected[1]:
                raise ToolError('INSPECTION_REQUIRED: read the code section being changed before editing it')
            if is_test(path):
                before = current.decode('utf-8')
                after = before.replace(edit.get('old', ''), edit.get('new', ''), 1)
                # Conservative additive-only existing tests. A new test file is allowed.
                if not after.startswith(before):
                    raise ToolError('Existing tests are protected; add regression coverage without replacing assertions')
                try:
                    old_tree, added = ast.parse(before), ast.parse(after[len(before):])
                except SyntaxError as exc:
                    raise ToolError('Additive regression tests must be valid Python') from exc
                existing = {getattr(n, 'name', '') for n in old_tree.body}
                if any(getattr(n, 'name', '') in existing - {''} for n in added.body):
                    raise ToolError('Regression additions may not shadow existing tests')
                if any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)) for n in added.body):
                    raise ToolError('Regression additions may not override existing test bindings')
                if re.search(r'\b(skip|skipIf|skipUnless|xfail|exit|quit|setattr)\s*\(', after[len(before):]):
                    raise ToolError('Regression additions may not suppress or skip checks')
