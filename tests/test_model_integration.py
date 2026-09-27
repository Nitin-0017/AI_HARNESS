from dataclasses import replace
import io
import json
from pathlib import Path
from unittest.mock import patch

from ai_harness.config import BudgetConfig
from ai_harness.controller import ModelStepController
from ai_harness.mock_model import MockModelAdapter
from ai_harness.model_providers import DeepSeekAdapter, QwenAdapter
from ai_harness.model_types import ModelError, ModelMessage, ModelResponse, TokenUsage
from ai_harness.state import RunState
from model_support import ModelServer, json_response, native_response
from tool_support import ToolTestCase, UNIT
from support import FoundationTestCase


READ = ModelResponse('', 'read_file', {'path': 'calculator.py'}, 'tool_call')
PATCH = ModelResponse('', 'apply_patch', {'edits': [{
    'path': 'calculator.py', 'old': 'return sum(numbers) / len(numbers)',
    'new': 'return 0 if not numbers else sum(numbers) / len(numbers)'}]}, 'tool_call')
CHECK = ModelResponse('', 'run_checks', {'names': ['unit']}, 'tool_call')
MESSAGES = (ModelMessage('user', 'Fix empty-list average behavior.'),)


class ModelIntegrationTests(ToolTestCase):
    def controller(self, script, *, budgets=None):
        state = RunState(budgets or BudgetConfig(), workspace=str(self.target))
        model = MockModelAdapter(script)
        tools = self.new_tools(state=state, checks=(UNIT,))
        return ModelStepController(model, tools, state), model, state

    def test_mock_drives_existing_real_tools_read_patch_checks(self):
        baseline = self.new_tools(checks=(UNIT,)).run_checks()
        self.assertFalse(baseline['all_passed'])
        controller, model, state = self.controller([READ, PATCH, CHECK])
        messages = list(MESSAGES)
        results = []
        for _ in range(3):
            result = controller.step(messages)
            self.assertEqual(result.status, 'ACTION_COMPLETED', result.to_dict())
            messages.append(result.feedback())
            results.append(result)
        self.assertEqual([r.response.requested_action for r in results], ['read_file', 'apply_patch', 'run_checks'])
        self.assertIn('def average', results[0].tool_result['content'])
        process = results[2].tool_result['results'][0]
        self.assertTrue(process['command_started'])
        self.assertEqual(process['exit_code'], 0)
        self.assertIn('Ran 3 tests', process['stderr'])
        self.assertTrue(results[2].tool_result['all_passed'])
        self.assertEqual(state.usage.model_calls, 3)
        self.assertEqual(state.usage.tool_calls, 3)
        self.assertEqual(state.usage.test_executions, 1)
        self.assertEqual(state.usage.unknown_model_usage_calls, 3)
        self.assertEqual(state.usage.total_tokens, 0)
        self.assertGreater(state.usage.estimated_tokens, 0)
        self.assertEqual(state.verification_status, 'NOT_ASSESSED')
        self.assertEqual(len(model.requests), 3)
        diff = self.new_tools().get_changes()
        self.assertIn('return 0 if not numbers', json.dumps(diff))

    def test_malformed_response_does_not_crash_and_next_step_works(self):
        controller, model, state = self.controller(['{broken', READ])
        result = controller.step(MESSAGES)
        self.assertEqual(result.status, 'MODEL_ERROR')
        self.assertEqual(state.usage.tool_calls, 0)
        self.assertEqual(controller.step(MESSAGES).status, 'ACTION_COMPLETED')
        self.assertEqual(state.usage.model_calls, 2)

    def test_missing_file_is_tool_error_not_model_success(self):
        controller, model, state = self.controller([ModelResponse('', 'read_file', {'path': 'missing.py'}, 'tool_call')])
        self.assertEqual(controller.step(MESSAGES).status, 'TOOL_ERROR')
        self.assertEqual(state.usage.failures, 1)

    def test_model_cannot_escape_workspace(self):
        outside = self.write('../outside.txt', 'not accessible')
        controller, model, state = self.controller([ModelResponse('', 'read_file', {'path': '../outside.txt'}, 'tool_call')])
        result = controller.step(MESSAGES)
        self.assertEqual(result.status, 'TOOL_ERROR')
        self.assertIsNone(result.tool_result)
        self.assertEqual(outside.read_text(), 'not accessible')

    def test_model_cannot_supply_arbitrary_command(self):
        controller, model, state = self.controller([ModelResponse('', 'run_checks', {'argv': ['bad']}, 'tool_call')])
        self.assertEqual(controller.step(MESSAGES).status, 'MODEL_ERROR')
        self.assertEqual(state.usage.tool_calls, 0)

    def test_invalid_patch_does_not_modify_target(self):
        original = (self.target / 'calculator.py').read_bytes()
        controller, model, state = self.controller([ModelResponse('', 'apply_patch', {
            'edits': [{'path': 'calculator.py', 'old': 'nonexistent exact text', 'new': 'new'}]}, 'tool_call')])
        self.assertEqual(controller.step(MESSAGES).status, 'TOOL_ERROR')
        self.assertEqual((self.target / 'calculator.py').read_bytes(), original)

    def test_failed_checks_are_real_failure_feedback(self):
        controller, model, state = self.controller([CHECK])
        result = controller.step(MESSAGES)
        self.assertEqual(result.status, 'CHECKS_FAILED')
        self.assertFalse(result.tool_result['all_passed'])
        self.assertIn('ZeroDivisionError', result.feedback().content)

    def test_unknown_action_prevents_tool_execution(self):
        controller, model, state = self.controller([ModelResponse('', 'shell', {}, 'tool_call')])
        self.assertEqual(controller.step(MESSAGES).status, 'MODEL_ERROR')
        self.assertEqual(state.usage.tool_calls, 0)

    def test_model_call_budget_stops_before_next_script_entry(self):
        controller, model, state = self.controller([READ, READ], budgets=BudgetConfig(max_model_calls=1))
        self.assertEqual(controller.step(MESSAGES).status, 'ACTION_COMPLETED')
        self.assertEqual(controller.step(MESSAGES).status, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(model.requests), 1)

    def test_iteration_budget_is_preserved(self):
        controller, model, state = self.controller([READ, READ], budgets=BudgetConfig(max_iterations=1))
        controller.step(MESSAGES)
        self.assertEqual(controller.step(MESSAGES).status, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(model.requests), 1)

    def test_context_and_token_budgets_block_before_model(self):
        for budget in (BudgetConfig(max_context_chars=2), BudgetConfig(max_total_tokens=1)):
            with self.subTest(budget=budget):
                controller, model, state = self.controller([READ], budgets=budget)
                self.assertEqual(controller.step(MESSAGES).status, 'BUDGET_EXHAUSTED')
                self.assertEqual(len(model.requests), 0)

    def test_tool_budget_still_enforced_by_existing_tools(self):
        controller, model, state = self.controller([READ, READ], budgets=BudgetConfig(max_tool_calls=1))
        controller.step(MESSAGES)
        self.assertEqual(controller.step(MESSAGES).status, 'BUDGET_EXHAUSTED')
        self.assertEqual(state.usage.tool_calls, 1)

    def test_terminal_message_never_sets_verified(self):
        controller, model, state = self.controller([ModelResponse('Everything is verified!')])
        result = controller.step(MESSAGES)
        self.assertEqual(result.status, 'MESSAGE')
        self.assertEqual(state.verification_status, 'NOT_RUN')
        self.assertEqual(state.usage.tool_calls, 0)
        self.assertIsNone(state.to_dict()['task_result'])

    def test_truncated_message_does_not_get_a_completion_status(self):
        controller, model, state = self.controller([ModelResponse('cut off', finish_reason='length')])
        self.assertEqual(controller.step(MESSAGES).status, 'MODEL_STOPPED')
        self.assertEqual(state.usage.tool_calls, 0)

    def test_recorded_usage_replaces_last_reservation(self):
        controller, model, state = self.controller([replace(READ, usage=TokenUsage(12, 8, 20))])
        self.assertEqual(controller.step(MESSAGES).status, 'ACTION_COMPLETED')
        self.assertEqual(state.usage.total_tokens, 20)
        self.assertEqual(state.usage.estimated_tokens, 0)
        self.assertEqual(state.usage.unknown_model_usage_calls, 0)

    def test_retry_budget_counts_every_actual_http_attempt(self):
        server = ModelServer([{'status': 503}, {'body': json_response()}]); self.addCleanup(server.close)
        state = RunState(BudgetConfig(max_model_calls=1))
        model = DeepSeekAdapter(server.config(max_retries=1), env={'AI_API_KEY': self.secret})
        controller = ModelStepController(model, self.new_tools(state=state), state)
        self.assertEqual(controller.step(MESSAGES).status, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(state.usage.model_calls, 1)
        self.assertEqual(state.usage.tool_calls, 0)

    def test_both_real_provider_adapters_share_same_controller(self):
        for cls, provider in [(DeepSeekAdapter, 'deepseek'), (QwenAdapter, 'qwen')]:
            with self.subTest(provider=provider):
                server = ModelServer([{'body': json_response()}]); self.addCleanup(server.close)
                state = RunState(BudgetConfig())
                model = cls(server.config(provider), env={'AI_API_KEY': self.secret})
                result = ModelStepController(model, self.new_tools(state=state), state).step(MESSAGES)
                self.assertEqual(result.status, 'ACTION_COMPLETED')
                self.assertIn('def average', result.tool_result['content'])

    def test_controller_imports_no_provider_or_mock(self):
        import ast
        import ai_harness.controller as module
        tree = ast.parse(Path(module.__file__).read_text())
        imports = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn('model_providers', imports)
        self.assertNotIn('mock_model', imports)
        self.assertIn('model', imports)


class ModelCliTests(FoundationTestCase):
    def args(self):
        return ['--workspace', str(self.target), '--task', 'Inspect main.py', '--non-interactive']

    def server_env(self, server, **extra):
        return {**self.env, 'AI_PROVIDER': 'deepseek', 'AI_MODEL_ID': 'organizer-id',
                'AI_API_ENDPOINT': server.endpoint, 'AI_REQUEST_FORMAT': 'chat_completions',
                'AI_RESPONSE_FORMAT': 'chat_json', 'AI_MAX_RETRIES': '0', **extra}

    def test_default_run_still_makes_no_model_call(self):
        server = ModelServer([]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--json'], env=self.server_env(server))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['model_execution'], 'NOT_RUN')
        self.assertEqual(server.requests, [])

    def test_explicit_model_step_runs_real_read(self):
        server = ModelServer([{'body': json_response(arguments={'path': 'main.py'})}]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--model-step'], env=self.server_env(server))
        self.assertEqual(code, 0, err + out)
        snapshot = json.loads(out)
        self.assertEqual(snapshot['usage']['model_calls'], 1)
        self.assertIn('def add', snapshot['model_result']['tool_result']['content'])
        self.assertEqual(snapshot['model_execution'], 'COMPLETED')
        self.assertEqual(snapshot['verification_status'], 'NOT_RUN')
        self.assertTrue(Path(snapshot['artifacts']['model_result_file']).exists())
        self.assertNotIn(self.secret, out)

    def test_malformed_response_persists_error_no_traceback(self):
        server = ModelServer([{'body': b'{invalid'}]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--model-step'], env=self.server_env(server))
        self.assertEqual(code, 1, err + out)
        snapshot = json.loads(out)
        self.assertEqual(snapshot['model_result']['status'], 'MODEL_ERROR')
        self.assertEqual(snapshot['usage']['tool_calls'], 0)
        self.assertNotIn('Traceback', out + err)

    def test_missing_configuration_never_returns_model_success(self):
        code, out, err = self.cli(self.args() + ['--model-step'])
        self.assertEqual(code, 2, err + out)
        self.assertEqual(json.loads(out)['model_result']['status'], 'BLOCKED')
        self.assertEqual(json.loads(out)['usage']['model_calls'], 0)

    def test_missing_credentials_controlled_by_existing_startup(self):
        code, out, err = self.cli(self.args() + ['--model-step'], env={})
        self.assertEqual(code, 2)
        self.assertIn('AI_API_KEY', err)
        self.assertNotIn('Traceback', err)

    def test_local_health_does_not_claim_live_success(self):
        server = ModelServer([]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--model-health'], env=self.server_env(server))
        self.assertEqual(code, 0, err)
        result = json.loads(out)['model_result']
        self.assertEqual(result['status'], 'CONFIGURED_NOT_PROBED')
        self.assertIsNone(result['response_valid'])
        self.assertEqual(server.requests, [])

    def test_live_health_counts_actual_probe(self):
        server = ModelServer([{'body': json_response(action=None, message='ready')}]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--model-health', '--live-health'], env=self.server_env(server))
        self.assertEqual(code, 0, err + out)
        result = json.loads(out)
        self.assertEqual(result['model_result']['status'], 'REACHABLE')
        self.assertEqual(result['usage']['model_calls'], 1)
        self.assertEqual(result['usage']['total_tokens'], 20)
        self.assertEqual(result['usage']['tool_calls'], 0)

    def test_model_modes_cannot_mix_with_direct_tool(self):
        code, out, err = self.cli(self.args() + ['--model-step', '--tool', 'read_file'])
        self.assertEqual(code, 2)
        self.assertNotIn('Traceback', err)

    def test_live_health_requires_health_flag(self):
        code, out, err = self.cli(self.args() + ['--live-health'])
        self.assertEqual(code, 2)
        self.assertIn('--model-health', err)

    def test_provider_settings_available_via_cli(self):
        server = ModelServer([{'body': json_response(arguments={'path': 'main.py'})}]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--model-step', '--provider', 'qwen', '--model-id', 'given',
            '--endpoint', server.endpoint, '--request-format', 'chat_completions', '--response-format', 'chat_json',
            '--temperature', '0.1', '--max-tokens', '128', '--api-timeout', '2', '--api-retries', '0'])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(server.requests[0]['body']['model'], 'given')
        self.assertEqual(server.requests[0]['body']['max_tokens'], 128)

    def test_no_task_model_step_is_blocked(self):
        server = ModelServer([]); self.addCleanup(server.close)
        code, out, err = self.cli(['--model-step', '--non-interactive'], env=self.server_env(server))
        self.assertEqual(code, 2)
        self.assertEqual(server.requests, [])

    def test_live_health_cannot_bypass_token_budget(self):
        server = ModelServer([]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--model-health', '--live-health', '--max-total-tokens', '1'], env=self.server_env(server))
        self.assertEqual(code, 2, out + err)
        self.assertEqual(json.loads(out)['model_result']['status'], 'BUDGET_EXHAUSTED')
        self.assertEqual(server.requests, [])

    def test_failed_probe_keeps_unknown_usage_estimate(self):
        server = ModelServer([{'status': 500}]); self.addCleanup(server.close)
        code, out, err = self.cli(self.args() + ['--model-health', '--live-health'], env=self.server_env(server))
        self.assertEqual(code, 2, out + err)
        usage = json.loads(out)['usage']
        self.assertEqual(usage['unknown_model_usage_calls'], 1)
        self.assertGreater(usage['estimated_tokens'], 0)
