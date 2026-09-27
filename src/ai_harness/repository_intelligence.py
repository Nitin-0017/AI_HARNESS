"""Bounded local index of observed paths and already-read Python syntax."""
from __future__ import annotations
import ast
from pathlib import PurePosixPath


class RepositoryIndex:
    def __init__(self, limit: int = 128):
        self.limit = max(1, min(limit, 512))
        self.files: list[str] = []
        self.observed: dict[str, dict] = {}

    def structure(self, files: list[str]):
        self.files = sorted(dict.fromkeys(files))[:self.limit]

    def read(self, path: str, text: str):
        info={'symbols':[], 'imports':[], 'parse_error':None}
        if path.endswith('.py') and len(text.encode()) <= 128000:
            try:
                tree=ast.parse(text)
                for node in ast.walk(tree):
                    if len(info['symbols'])<128 and isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                        info['symbols'].append({'name':node.name,'line':node.lineno,'kind':type(node).__name__})
                    if isinstance(node,ast.Import):
                        info['imports'].extend(n.name for n in node.names)
                    elif isinstance(node,ast.ImportFrom):
                        info['imports'].append('.'*node.level+(node.module or ''))
                    info['imports']=info['imports'][:128]
            except (SyntaxError, ValueError, RecursionError):
                info['parse_error']='syntax_unavailable; inspect actual compiler/check output'
        self.observed.pop(path,None); self.observed[path]=info
        while len(self.observed)>self.limit: self.observed.pop(next(iter(self.observed)))

    def remove(self, path):
        self.observed.pop(path,None)
        self.files=[p for p in self.files if p!=path]

    def summary(self):
        def test(p):
            return 'tests' in PurePosixPath(p).parts or PurePosixPath(p).name.startswith('test_')
        dependencies={'requirements.txt','pyproject.toml','poetry.lock','uv.lock','package.json','package-lock.json','Cargo.toml','Cargo.lock','go.mod','go.sum'}
        configs={'Makefile','pytest.ini','setup.cfg','tox.ini','tsconfig.json','pyproject.toml'}
        files=list(dict.fromkeys(self.files+list(self.observed)))[:self.limit]
        symbols=[]; references=[]; relationships=[]
        for path,info in self.observed.items():
            symbols.extend({'path':path,**s} for s in info['symbols'])
            for module in info['imports']:
                base=module.lstrip('.').replace('.','/')
                options={base+'.py','src/'+base+'.py',base+'/__init__.py'}
                if module.startswith('.'):
                    options.add(str(PurePosixPath(path).parent/(base+'.py')))
                for candidate in options & set(files):
                    references.append({'from':path,'to':candidate,'kind':'observed_import'})
                    if test(path): relationships.append({'test':path,'source':candidate,'basis':'import'})
        for path in files:
            if test(path):
                basename=PurePosixPath(path).name.removeprefix('test_')
                for source in files:
                    if not test(source) and PurePosixPath(source).name==basename:
                        relationships.append({'test':path,'source':source,'basis':'filename_heuristic'})
        return {'directory_structure':sorted({str(PurePosixPath(p).parent) for p in files})[:self.limit],
                'source_files':[p for p in files if not test(p) and PurePosixPath(p).suffix in {'.py','.js','.ts','.go','.rs','.java'}],
                'test_files':[p for p in files if test(p)],
                'configuration_files':[p for p in files if PurePosixPath(p).name in configs],
                'dependency_files':[p for p in files if PurePosixPath(p).name in dependencies],
                'symbols':symbols[:self.limit], 'references':references[:self.limit],
                'test_source_relationships':relationships[:self.limit],
                'scope':'Bounded observed files; static relationships are hints, not complete dependency analysis'}
