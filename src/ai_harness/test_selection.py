"""Conservative deterministic check discovery and selection; no target imports."""
from __future__ import annotations
import ast
from pathlib import PurePosixPath
from .tool_types import CheckSpec, ToolError


def discover_checks(io) -> tuple[CheckSpec, ...]:
    files, _ = io.walk()
    tests = [p for p in files if p.endswith('.py') and
             (PurePosixPath(p).name.startswith('test_') or p.endswith('_test.py'))]
    kinds = set()
    scanned = 0
    for path in tests:
        try:
            data = io.read_bytes(path)[0]
            scanned += len(data)
            if scanned > io.limits.max_scan_bytes:
                raise ToolError('Test discovery exceeds max_scan_bytes; configure trusted checks explicitly')
            text = data.decode('utf-8')
            tree = ast.parse(text)
            unittest_used = any(isinstance(n, ast.ClassDef) and any(
                isinstance(b, ast.Attribute) and b.attr == 'TestCase' or isinstance(b, ast.Name) and b.id == 'TestCase'
                for b in n.bases) for n in ast.walk(tree))
            kinds.add('unittest' if unittest_used else 'pytest')
        except (UnicodeError, SyntaxError):
            # A syntax-broken test still needs to run; do not silently drop it.
            kinds.add('unittest')
    if not tests:
        return ()
    # Mixed frameworks use pytest (actual execution reports a missing dependency).
    runner = 'pytest' if 'pytest' in kinds else 'unittest'
    if runner == 'pytest':
        registry = [CheckSpec('discovered-full', ('{python}','-m','pytest','-q'), scope='broad', required=True)]
    else:
        # unittest does not recurse into non-package folders on every supported
        # Python version. Every observed directory/pattern gets a required check.
        groups = sorted({(str(PurePosixPath(p).parent), 'test_*.py' if PurePosixPath(p).name.startswith('test_') else '*_test.py') for p in tests})
        if len(groups) > 64:
            raise ToolError('Too many independent test directories; configure trusted checks explicitly')
        registry = [CheckSpec('discovered-full' if n == 0 else f'discovered-full-{n}',
                    ('{python}','-m','unittest','discover','-s',directory,'-p',pattern,'-v'),
                    scope='broad', required=True) for n,(directory,pattern) in enumerate(groups)]
    for number,path in enumerate(tests[:16]):
        p=PurePosixPath(path)
        argv=('{python}','-m','unittest','discover','-s',str(p.parent),'-p',p.name,'-v') if runner=='unittest' else ('{python}','-m','pytest','-q',path)
        source=p.name.removeprefix('test_')
        registry.append(CheckSpec(f'discovered-target-{number}',argv,scope='targeted',paths=(source,path),required=False))
    return tuple(registry)


def select_checks(registry: dict, modified: list[str], *, final: bool = False) -> list[str]:
    required = [name for name,spec in registry.items() if spec.required]
    if final:
        return required
    # Configuration/dependency or broad multi-file edits demand broad regression.
    broad_change = len(modified)>3 or any(PurePosixPath(p).name in {
        'pyproject.toml','requirements.txt','package.json','setup.cfg','Cargo.toml','go.mod'} for p in modified)
    if broad_change or not modified:
        return required
    relevant=[]
    for name,spec in registry.items():
        if spec.scope != 'targeted': continue
        if any(path == m or PurePosixPath(path).name == PurePosixPath(m).name
               for path in spec.paths for m in modified):
            relevant.append(name)
    return relevant or required
