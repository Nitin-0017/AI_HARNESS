"""Clean real repositories and scripted decisions, never production model data."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from ai_harness.config import BudgetConfig
from ai_harness.controller import AgentController
from ai_harness.inputs import validate_task
from ai_harness.mock_model import MockModelAdapter
from ai_harness.model_types import ModelResponse
from ai_harness.state import RunState
from ai_harness.tools import RepositoryTools
from ai_harness.tool_types import CheckSpec
from ai_harness.workspace import Workspace
from fixture_repositories import git

PROJECT = Path(__file__).resolve().parents[1]
UNIT = CheckSpec('unit', ('{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-v'), timeout_seconds=5)


def act(kind, arguments=None):
    return ModelResponse('Perform ' + kind, kind, arguments or {}, 'tool_call',
                         reason='Inspect evidence and make the requested targeted correction.',
                         expected_outcome='An actual tool result, not an assumed success.')


def patch(path, old, new):
    return {'path': path, 'old': old, 'new': new}


def test_file(import_line, assertions):
    return 'import unittest\n' + import_line + '\nclass Behavior(unittest.TestCase):\n    def test_behavior(self):\n' + ''.join('        '+a+'\n' for a in assertions)


@dataclass
class CodingTask:
    task: str
    files: dict[str, str]
    script: list
    checks: tuple = (UNIT,)
    expected: str = 'COMPLETED'


def cases() -> dict[str, CodingTask]:
    cases = {}
    def one(name, task, source, old, new, assertions):
        cases[name] = CodingTask(task, {'app.py': source, 'tests/test_app.py': test_file('from app import solve', assertions)},
            [act('list_files'), act('read_file', {'path':'app.py'}),
             act('apply_patch', {'edits':[patch('app.py',old,new)]}), act('run_checks'), act('finish')])
    one('one_file', 'Fix solve(n) to return the absolute value of a negative or positive integer.',
        'def solve(n):\n    return -n\n', 'return -n', 'return abs(n)',
        ['self.assertEqual(solve(-4), 4)', 'self.assertEqual(solve(5), 5)'])
    one('empty_input', 'Make solve return zero for an empty list and the mean otherwise.',
        'def solve(values):\n    return sum(values) / len(values)\n', 'return sum(values) / len(values)',
        'return sum(values) / len(values) if values else 0',
        ['self.assertEqual(solve([]), 0)', 'self.assertEqual(solve([2,4]), 3)'])
    cases['multi_file'] = CodingTask('Implement convert(c) from Celsius to Fahrenheit using constants.py.',
        {'app.py':'from constants import SCALE, OFFSET\ndef convert(c):\n    return c + OFFSET\n',
         'constants.py':'SCALE = 1\nOFFSET = 32\n',
         'tests/test_app.py':test_file('from app import convert', ['self.assertEqual(convert(0), 32)', 'self.assertEqual(convert(100), 212)'])},
        [act('read_file',{'path':'app.py'}),act('read_file',{'path':'constants.py'}),
         act('apply_patch',{'edits':[patch('app.py','return c + OFFSET','return c * SCALE + OFFSET'),
                                    patch('constants.py','SCALE = 1','SCALE = 1.8')]}),act('run_checks'),act('finish')])
    one('existing_failure', 'Fix the existing inclusive range count for solve(n).',
        'def solve(n):\n    return len(range(n))\n','range(n)','range(n + 1)',
        ['self.assertEqual(solve(0), 1)', 'self.assertEqual(solve(4), 5)'])
    one('missing_test', 'Fix solve to retain zero as nonnegative and add a zero regression test.',
        'def solve(values):\n    return [v for v in values if v > 0]\n','v > 0','v >= 0',
        ['self.assertEqual(solve([-1, 2]), [2])'])
    cases['missing_test'].script.insert(3, act('apply_patch',{'edits':[{'path':'tests/test_zero.py','operation':'create',
        'new':test_file('from app import solve',['self.assertEqual(solve([-1,0,2]), [0,2])'])}]}))
    cases['syntax_recovery'] = CodingTask('Repair the syntax error in app.py; solve(3) must return 6.',
        {'app.py':'def solve(n)\n    return n * 2\n',
         'tests/test_app.py':test_file('from app import solve',['self.assertEqual(solve(3), 6)'])},
        [act('read_file',{'path':'app.py'}),act('run_checks'),
         act('apply_patch',{'edits':[patch('app.py','def solve(n)','def solve(n):')]}),act('run_checks'),act('finish')])
    cases['wrong_then_repair'] = CodingTask('Fix solve(n) to return the absolute value without breaking positive values.',
        dict(cases['one_file'].files),
        [act('read_file',{'path':'app.py'}),act('apply_patch',{'edits':[patch('app.py','return -n','return 0')]}),
         act('run_checks'),act('apply_patch',{'edits':[patch('app.py','return 0','return abs(n)')]}),act('run_checks'),act('finish')])
    cases['large_irrelevant'] = CodingTask(cases['empty_input'].task,
        {**cases['empty_input'].files,'assets/unrelated.txt':'UNRELATED_LARGE_MARKER\n'*20000},list(cases['empty_input'].script))
    slow=CheckSpec('slow',('{python}','-c','import time; time.sleep(2)'),timeout_seconds=0.08)
    cases['timeout'] = CodingTask('Complete the checks; do not modify the check registry.',dict(cases['one_file'].files),
        [act('run_checks')]*4,checks=(slow,),expected='FAILED')
    cases['ambiguous'] = CodingTask('Change solve to behave correctly. The required behavior is unspecified.',
        dict(cases['one_file'].files),[ModelResponse('The expected behavior needs clarification.',finish_reason='refused')],expected='BLOCKED')
    return cases


def build(root: Path, case: CodingTask) -> Path:
    root.mkdir(parents=True)
    for name, text in case.files.items():
        path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text,encoding='utf-8')
    (root/'.gitignore').write_text('__pycache__/\n*.pyc\n.pytest_cache/\n')
    git(root,'init','-q'); git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Clean task baseline')
    return root


def execute(root: Path, case: CodingTask, *, budgets=None):
    build(root,case)
    state=RunState(budgets or BudgetConfig(max_total_tokens=2000000), workspace=str(root), task=validate_task(case.task,'fixture',64000))
    model=MockModelAdapter(case.script); events=[]
    with RepositoryTools(Workspace.open(root,PROJECT),checks=case.checks,state=state,
            emit=lambda event,**data:events.append({'event':event,**data})) as tools:
        controller=AgentController(model,tools,state,emit=lambda event,**data:events.append({'event':event,**data}))
        result=controller.run()
    return result, state, model, events
