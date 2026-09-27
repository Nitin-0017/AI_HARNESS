"""Phase 4: actual repository operations behind deterministic model decisions."""
import json
import unittest

from ai_harness.config import BudgetConfig
from ai_harness.controller import Action, AgentController
from ai_harness.inputs import validate_task
from ai_harness.mock_model import MockModelAdapter
from ai_harness.model_types import ModelError, ModelResponse
from ai_harness.state import AgentStatus, RunState
from ai_harness.tool_types import CheckSpec, ToolError
from model_support import ModelServer, json_response
from support import FoundationTestCase
from tool_support import ToolTestCase, UNIT


def action(name, arguments=None, message='Next action'):
    return ModelResponse(message, name, arguments or {}, 'tool_call')


READ = action('read_file', {'path': 'calculator.py'})
CHECK = action('run_checks')
FINISH = action('finish')
OLD = 'return sum(numbers) / len(numbers)'
FIXED = 'return 0 if not numbers else sum(numbers) / len(numbers)'


def edit(old=OLD, new=FIXED):
    return action('apply_patch', {'edits': [{'path': 'calculator.py', 'old': old, 'new': new}]})


def repair_script():
    return [action('list_files'), READ, edit(new='return 0'), CHECK,
            edit(old='return 0'), CHECK, FINISH]


class AgentLoopTests(ToolTestCase):
    def agent(self, script, *, budgets=None, checks=(UNIT,), task='Fix average([]) to return zero; preserve nonempty behavior.'):
        state = RunState(budgets or BudgetConfig(max_total_tokens=1000000), workspace=str(self.target),
                         task=validate_task(task, 'test', 64000))
        tools = self.new_tools(checks=checks, state=state)
        model = MockModelAdapter(script)
        events = []
        controller = AgentController(model, tools, state,
                                     emit=lambda event, **data: events.append((event, data)))
        return controller, model, state, events

    def test_full_mock_inspect_read_edit_failure_repair_test_success(self):
        controller, model, state, events = self.agent(repair_script())
        result = controller.run()
        self.assertEqual(result.status, 'COMPLETED', result.to_dict())
        self.assertEqual(result.task_result, 'VERIFIED')
        self.assertEqual([s['action'] for s in result.steps],
                         ['list_files', 'read_file', 'apply_patch', 'run_checks', 'apply_patch', 'run_checks', 'finish'])
        self.assertEqual(result.steps[3]['status'], 'CHECKS_FAILED')
        self.assertIn('FAILED', '\n'.join(m.content for m in model.requests[4].messages))
        self.assertEqual(state.usage.test_executions, 2)
        self.assertEqual(state.usage.model_calls, 7)
        self.assertGreater(state.usage.recovery_attempts, 0)
        self.assertIn('Ran 3 tests', result.verification['results'][0]['stderr'])
        self.assertEqual(result.verification['results'][0]['exit_code'], 0)
        self.assertTrue(result.verification['results'][0]['command_started'])
        self.assertIn(FIXED, result.changes['unstaged_diff'])
        self.assertIn(FIXED, (self.target / 'calculator.py').read_text())
        self.assertEqual(state.agent_status, AgentStatus.COMPLETED)
        self.assertEqual(state.to_dict()['task_result'], 'VERIFIED')
        seen = {data['state'] for event, data in events if event == 'agent.state'}
        self.assertTrue({'START', 'INSPECTING', 'PLANNING', 'ACTING', 'VERIFYING', 'RECOVERING', 'COMPLETED'} <= seen)
        self.assertTrue(model.metadata()['is_mock'])

    def test_premature_finish_executes_checks_and_returns_failure_feedback(self):
        controller, model, state, _ = self.agent([FINISH, edit(), FINISH])
        result = controller.run()
        self.assertEqual(result.status, 'COMPLETED', result.to_dict())
        self.assertEqual(state.usage.test_executions, 2)
        self.assertIn('ZeroDivisionError', '\n'.join(m.content for m in model.requests[1].messages))

    def test_model_success_prose_is_not_execution_evidence(self):
        controller, _, state, _ = self.agent([ModelResponse('I ran all tests and they passed!')],
            budgets=BudgetConfig(max_retries=0))
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertNotEqual(result.task_result, 'VERIFIED')
        self.assertEqual(state.usage.test_executions, 1)
        self.assertFalse(result.verification['passed'])

    def test_stale_pass_after_later_edit_is_rechecked(self):
        controller, _, state, _ = self.agent([edit(), CHECK, edit(old=FIXED, new='return 0'), FINISH],
            budgets=BudgetConfig(max_total_tokens=1000000, max_retries=0))
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(state.usage.test_executions, 2)
        self.assertFalse(result.verification['passed'])

    def test_later_failed_checks_invalidate_previous_success(self):
        failing = CheckSpec('fail', ('{python}', '-c', 'raise SystemExit(3)'))
        controller, _, _, _ = self.agent([edit(), action('run_checks', {'names': ['unit']}),
                                         action('run_checks', {'names': ['fail']}), FINISH], checks=(UNIT, failing),
                                         budgets=BudgetConfig(max_retries=0, max_total_tokens=1000000))
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertFalse(result.verification['complete_registry'])

    def test_subset_check_cannot_hide_failing_required_check(self):
        failing = CheckSpec('other', ('{python}', '-c', 'raise SystemExit(9)'))
        controller, _, state, _ = self.agent([edit(), action('run_checks', {'names': ['unit']}), FINISH],
                                             checks=(UNIT, failing))
        result = controller.run()
        self.assertNotEqual(result.task_result, 'VERIFIED')
        self.assertGreaterEqual(state.usage.test_executions, 3)
        self.assertFalse(result.verification['passed'])

    def test_no_checks_is_blocked_not_success(self):
        controller, _, state, _ = self.agent([FINISH], checks=())
        result = controller.run()
        self.assertEqual(result.status, 'BLOCKED')
        self.assertEqual(state.usage.test_executions, 0)

    def test_zero_unittest_tests_cannot_verify(self):
        (self.target / 'tests' / 'test_calculator.py').unlink()
        controller, _, _, _ = self.agent([FINISH], budgets=BudgetConfig(max_retries=0))
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertIn('Ran 0 tests', result.verification['results'][0]['stderr'])

    def test_malformed_model_response_recovers(self):
        controller, model, _, _ = self.agent(['{broken', edit(), FINISH])
        result = controller.run()
        self.assertEqual(result.status, 'COMPLETED', result.to_dict())
        self.assertIn('INVALID_RESPONSE', '\n'.join(m.content for m in model.requests[1].messages))

    def test_unknown_action_is_never_executed(self):
        controller, _, state, _ = self.agent([action('shell', {'command': 'anything'})],
                                             budgets=BudgetConfig(max_retries=0))
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(state.usage.tool_calls, 2)  # only controller startup inspection

    def test_path_escape_is_rejected_and_recovery_is_possible(self):
        before = (self.target / 'calculator.py').read_bytes()
        controller, _, _, _ = self.agent([action('read_file', {'path': '../outside.txt'}), edit(), FINISH])
        result = controller.run()
        self.assertEqual(result.status, 'COMPLETED', result.to_dict())
        self.assertEqual(result.steps[0]['status'], 'TOOL_ERROR')
        self.assertNotEqual((self.target / 'calculator.py').read_bytes(), before)

    def test_unconfigured_command_name_rejected(self):
        controller, _, state, _ = self.agent([action('run_checks', {'names': ['not-configured']})],
                                             budgets=BudgetConfig(max_retries=0))
        self.assertEqual(controller.run().status, 'FAILED')
        self.assertEqual(state.usage.test_executions, 0)

    def test_arbitrary_command_argument_rejected_before_execution(self):
        controller, _, state, _ = self.agent([action('run_checks', {'argv': ['anything']})],
                                             budgets=BudgetConfig(max_retries=0))
        self.assertEqual(controller.run().status, 'FAILED')
        self.assertEqual(state.usage.test_executions, 0)

    def test_repeated_failure_terminates_within_retry_limit(self):
        missing = action('read_file', {'path': 'absent.py'})
        controller, model, _, _ = self.agent([missing] * 8)
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(len(model.requests), 3)

    def test_iteration_limit_terminates_successful_read_loop(self):
        controller, model, _, _ = self.agent([READ] * 10, budgets=BudgetConfig(max_iterations=2))
        result = controller.run()
        self.assertEqual(result.status, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(model.requests), 2)

    def test_model_call_limit_is_preserved(self):
        controller, model, _, _ = self.agent([READ, READ], budgets=BudgetConfig(max_model_calls=1))
        self.assertEqual(controller.run().status, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(model.requests), 1)

    def test_tool_budget_is_preserved(self):
        controller, model, state, _ = self.agent([READ], budgets=BudgetConfig(max_tool_calls=2))
        self.assertEqual(controller.run().status, 'BUDGET_EXHAUSTED')
        self.assertEqual(state.usage.tool_calls, 2)

    def test_verification_budget_cannot_be_bypassed_by_finish(self):
        controller, _, state, _ = self.agent([CHECK, edit(), FINISH],
            budgets=BudgetConfig(max_test_executions=1, max_total_tokens=1000000))
        self.assertEqual(controller.run().status, 'BUDGET_EXHAUSTED')
        self.assertEqual(state.usage.test_executions, 1)

    def test_time_budget_before_start_executes_nothing(self):
        controller, _, state, _ = self.agent([READ])
        state._clock = lambda: state._started_clock + state.budgets.max_seconds
        self.assertEqual(controller.run().status, 'BUDGET_EXHAUSTED')
        self.assertEqual(state.usage.tool_calls, 0)

    def test_context_stays_bounded_and_latest_failure_survives(self):
        controller, model, _, _ = self.agent(repair_script(),
            budgets=BudgetConfig(max_context_chars=9000, max_total_tokens=1000000))
        result = controller.run()
        self.assertEqual(result.status, 'COMPLETED', result.to_dict())
        for req in model.requests:
            self.assertLessEqual(sum(len(m.content) for m in req.messages) +
                len(json.dumps([{'name': t.name, 'description': t.description, 'parameters': t.parameters} for t in req.tools])), 9000)

    def test_interrupt_is_graceful_and_never_verified(self):
        def interrupted_script():
            raise KeyboardInterrupt
            yield READ
        controller, _, state, _ = self.agent(interrupted_script())
        result = controller.run()
        self.assertEqual(result.status, 'INCOMPLETE')
        self.assertIsNone(state.current_action)
        self.assertNotEqual(state.verification_status, 'VERIFIED')

    def test_refusal_blocks_without_target_checks(self):
        controller, _, state, _ = self.agent([ModelResponse('Cannot continue', finish_reason='refused')])
        self.assertEqual(controller.run().status, 'BLOCKED')
        self.assertEqual(state.usage.test_executions, 0)

    def test_mock_exhaustion_is_honest_blocker(self):
        controller, _, _, _ = self.agent([])
        self.assertEqual(controller.run().status, 'BLOCKED')

    def test_mismatched_workspace_is_blocked_before_tools(self):
        controller, _, state, _ = self.agent([READ])
        state.workspace = str(self.base)
        self.assertEqual(controller.run().status, 'BLOCKED')
        self.assertEqual(state.usage.tool_calls, 0)

    def test_missing_task_is_blocked(self):
        controller, _, state, _ = self.agent([READ])
        state.task = None
        self.assertEqual(controller.run().status, 'BLOCKED')

    def test_run_is_single_use(self):
        controller, _, _, _ = self.agent([])
        controller.run()
        with self.assertRaises(ToolError):
            controller.run()

    def test_source_changes_during_checks_cannot_verify(self):
        mutate = CheckSpec('mutate', ('{python}', '-c',
            'from pathlib import Path; Path("calculator.py").write_text("def average(n): return 0\\n")'))
        controller, _, _, _ = self.agent([FINISH], checks=(mutate,), budgets=BudgetConfig(max_retries=0))
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertFalse(result.verification['stable_workspace'])

    def test_real_command_timeout_cannot_verify(self):
        slow = CheckSpec('slow', ('{python}', '-c', 'import time; time.sleep(5)'), timeout_seconds=0.2)
        controller, _, _, _ = self.agent([FINISH], checks=(slow,), budgets=BudgetConfig(max_retries=0))
        result = controller.run()
        self.assertEqual(result.status, 'FAILED')
        self.assertTrue(result.verification['results'][0]['timed_out'])

    def test_action_schema_rejects_unknown_fields_and_unsafe_paths(self):
        for kind, args in [('finish', {'verified': True}), ('run_checks', {'command': 'x'}),
                           ('read_file', {'path': 1})]:
            with self.subTest(kind=kind), self.assertRaises(ModelError):
                Action(kind, args)
        with self.assertRaises(ToolError):
            Action('apply_patch', {'edits': [{'path': '../bad', 'old': 'a', 'new': 'b'}]}).validate_workspace(self.tools)
        item = Action('read_file', {'path': 'calculator.py'}, 'Inspect the code', 'Receive file text')
        self.assertEqual(item.expected_outcome, 'Receive file text')
        with self.assertRaises(ModelError):
            Action('read_file', {'path': 'calculator.py'}, reason=False)

    def test_secret_is_redacted_before_model_and_results(self):
        controller, model, _, _ = self.agent([FINISH], task='Inspect ' + self.secret)
        result = controller.run()
        self.assertNotIn(self.secret, json.dumps([r.to_dict() for r in model.requests]))
        self.assertNotIn(self.secret, json.dumps(result.to_dict()))


class AgentCliTests(FoundationTestCase):
    def test_agent_requires_real_provider_configuration_no_mock_fallback(self):
        code, out, err = self.cli(['--agent', '--workspace', str(self.target), '--task', 'Fix bug', '--non-interactive'])
        self.assertNotEqual(code, 0)
        self.assertNotIn('Traceback', out + err)
        self.assertEqual(json.loads(out)['usage']['model_calls'], 0)

    def test_agent_is_mutually_exclusive_with_step_and_tool(self):
        for extra in (['--model-step'], ['--tool', 'list_files']):
            with self.subTest(extra=extra):
                code, out, err = self.cli(['--agent', *extra])
                self.assertNotEqual(code, 0)

    def test_cli_real_http_adapter_enters_agent_loop(self):
        # Real local HTTP protocol fixture, not a live provider.
        server = ModelServer([{'body': json_response(action='finish', arguments={})}])
        self.addCleanup(server.close)
        # Foundation fixture isn't a standalone Git checkout: use the real Git
        # fixture so startup inspection and verification exercise real commands.
        from fixture_repositories import create_fixture
        target = create_fixture(self.base / 'agent-target')
        config = self.base / 'checks.toml'
        config.write_text('[[checks]]\nname="unit"\nargv=["{python}","-m","unittest","discover","-s","tests","-v"]\n')
        env = {**self.env, 'AI_PROVIDER': 'deepseek', 'AI_MODEL_ID': 'organizer-id',
               'AI_API_ENDPOINT': server.endpoint, 'AI_REQUEST_FORMAT': 'chat_completions',
               'AI_RESPONSE_FORMAT': 'chat_json', 'AI_MAX_RETRIES': '0'}
        code, out, err = self.cli(['--agent', '--workspace', str(target), '--task', 'Fix average',
                                  '--config', str(config), '--max-retries', '0', '--non-interactive'], env=env)
        self.assertEqual(code, 1, out + err)
        result = json.loads(out)
        self.assertEqual(result['model_result']['status'], 'FAILED')
        self.assertEqual(result['usage']['test_executions'], 1)
        self.assertEqual(len(server.requests), 1)
        self.assertNotEqual(result['task_result'], 'VERIFIED')


if __name__ == '__main__':
    unittest.main()
