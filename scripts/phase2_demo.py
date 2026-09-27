"""Explicitly scripted TOOL demonstration: real edits/checks, no model decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

# Only this development script imports fixture code. Production never does.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from fixture_repositories import create_fixture
from ai_harness.config import BudgetConfig
from ai_harness.state import RunState
from ai_harness.tool_types import CheckSpec
from ai_harness.tools import RepositoryTools
from ai_harness.workspace import Workspace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.runs' / 'phase2-demo')
    args = parser.parse_args()
    target = create_fixture(Path(tempfile.mkdtemp(prefix='harness-phase2-demo-')) / 'target')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(BudgetConfig(), workspace=str(target))
    checks = (CheckSpec('unit', ('{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-v')),)
    report = {'kind': 'scripted_tool_demo_not_model_evaluation', 'target_workspace': str(target),
              'steps': [], 'model_calls': 0}
    with RepositoryTools(Workspace.open(target, ROOT), checks=checks, state=state) as tools:
        for name, arguments in [
            ('list_files', {}),
            ('search_code', {'query': 'def average'}),
            ('read_file', {'path': 'calculator.py'}),
            ('run_checks', {}),
            ('apply_patch', {'edits': [{'path': 'calculator.py', 'old': 'return sum(numbers) / len(numbers)',
                                       'new': 'return 0'}]}),
            ('run_checks', {}),
            ('apply_patch', {'edits': [{'path': 'calculator.py', 'old': 'return 0',
                                       'new': 'return 0 if not numbers else sum(numbers) / len(numbers)'}]}),
            ('run_checks', {}),
            ('get_changes', {}),
        ]:
            value = tools.call(name, arguments)
            report['steps'].append({'tool': name, 'result': value})
            suffix = f" (all_passed={value['all_passed']})" if name == 'run_checks' else ''
            print(name + suffix)
    outcomes = [step['result']['all_passed'] for step in report['steps'] if step['tool'] == 'run_checks']
    report['check_outcomes'] = outcomes
    report['usage'] = state.to_dict()['usage']
    report['demo_completed'] = outcomes == [False, False, True]
    report['task_verification'] = 'NOT_ASSESSED_BY_AN_AUTONOMOUS_VERIFIER'
    destination = args.output_dir / 'demo_report.json'
    destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('Target retained for inspection:', target)
    print('Evidence:', destination)
    if not report['demo_completed']:
        print('Unexpected check outcomes. Inspect the actual report; no success is claimed.', file=sys.stderr)
        return 1
    print('Actual outcomes: baseline failed; incorrect patch failed; repaired code passed all 3 fixture tests.')
    print('No model was used. This does not establish official evaluation performance.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
