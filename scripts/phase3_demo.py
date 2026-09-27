"""Development-only mock decisions -> existing real Phase 2 tools and tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from fixture_repositories import create_fixture
from ai_harness.config import BudgetConfig
from ai_harness.controller import ModelStepController
from ai_harness.mock_model import MockModelAdapter
from ai_harness.model_types import ModelMessage, ModelResponse
from ai_harness.state import RunState
from ai_harness.tool_types import CheckSpec
from ai_harness.tools import RepositoryTools
from ai_harness.workspace import Workspace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.runs' / 'phase3-demo')
    args = parser.parse_args()
    target = create_fixture(Path(tempfile.mkdtemp(prefix='harness-phase3-demo-')) / 'target')
    state = RunState(BudgetConfig(), workspace=str(target))
    model = MockModelAdapter([
        ModelResponse('Read the function before editing.', 'read_file', {'path': 'calculator.py'}, 'tool_call'),
        ModelResponse('Add the empty-input behavior.', 'apply_patch', {'edits': [{
            'path': 'calculator.py', 'old': 'return sum(numbers) / len(numbers)',
            'new': 'return 0 if not numbers else sum(numbers) / len(numbers)'}]}, 'tool_call'),
        ModelResponse('Execute the existing tests.', 'run_checks', {'names': ['unit']}, 'tool_call'),
    ])
    check = CheckSpec('unit', ('{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-v'), timeout_seconds=10)
    report = {'kind': 'mock_model_decisions_real_repository_operations', 'provider': model.metadata(),
              'target_workspace': str(target), 'steps': [], 'external_model_api_calls': 0}
    with RepositoryTools(Workspace.open(target, ROOT), checks=(check,), state=state) as tools:
        report['baseline'] = tools.run_checks()
        print('Actual baseline all_passed:', report['baseline']['all_passed'])
        controller = ModelStepController(model, tools, state)
        messages = [ModelMessage('user', 'Fix average([]) to return zero without changing non-empty input behavior.')]
        for _ in range(3):
            result = controller.step(messages)
            report['steps'].append(result.to_dict())
            print('Mock requested:', result.response.requested_action if result.response else 'invalid response', '->', result.status)
            messages.append(result.feedback())
            if result.status != 'ACTION_COMPLETED':
                break
        report['changes'] = tools.get_changes()
    report['usage'] = state.to_dict()['usage']
    report['verification_status'] = 'NOT_ASSESSED_BY_AN_AUTONOMOUS_VERIFIER'
    report['demo_completed'] = (not report['baseline']['all_passed'] and len(report['steps']) == 3
                                and report['steps'][-1]['tool_result'] is not None
                                and report['steps'][-1]['tool_result'].get('all_passed') is True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / 'demo_report.json'
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('Report:', path)
    print('Retained target:', target)
    print('Decisions are scripted; patches, checks and diffs are actual execution. No live-model compatibility is claimed.')
    return 0 if report['demo_completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
