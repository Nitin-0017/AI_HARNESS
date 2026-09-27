"""Phase 5 selection/retention tests plus real-tool controller regressions."""
from dataclasses import asdict
import json
import secrets
import unittest

from ai_harness.cli import build_parser
from ai_harness.config import BudgetConfig, load_config
from ai_harness.context import ContextManager, SECTIONS, encode, measure_request, request_size
from ai_harness.controller import AgentController, ModelStepController, agent_tool_definitions
from ai_harness.errors import BudgetExceeded, ConfigurationError
from ai_harness.inputs import validate_task
from ai_harness.mock_model import MockModelAdapter
from ai_harness.model_types import ModelMessage, ModelRequest, ModelResponse
from ai_harness.state import RunState
from ai_harness.telemetry import Redactor
from support import FoundationTestCase
from tool_support import ToolTestCase, UNIT
from test_agent_loop import repair_script, action, READ, FINISH, edit


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.secret = secrets.token_urlsafe(32)
        self.state = RunState(BudgetConfig(), task=validate_task('Fix average empty input in calculator.py', 'test', 64000))
        self.context = ContextManager(self.state, redactor=Redactor(self.secret))
        self.system = ModelMessage('system', 'Use actual tools, not claimed results.')
        self.definitions = agent_tool_definitions()

    def observe(self, name, result=None, args=None, status='ACTION_COMPLETED', message=''):
        self.context.observe({'status': status, 'response': {
            'requested_action': name, 'arguments': args or {}, 'message': message}, 'tool_result': result})

    def read(self, path='calculator.py', content='def average(numbers):\n    return sum(numbers) / len(numbers)\n', sha='a'):
        self.observe('read_file', {'path': path, 'content': content, 'sha256': sha,
            'start_line': 1, 'end_line': len(content.splitlines()), 'truncated': False}, {'path': path})

    def render(self):
        return self.context.messages(self.system, self.definitions)

    def text(self):
        return '\n'.join(m.content for m in self.render())

    def check_result(self, *, passed=False, output=''):
        # Unit input fixture for the context projection; integration below uses
        # actual subprocess results. This fixture never produces a task verdict.
        return {'all_passed': passed, 'results': [{'name': 'unit', 'command_started': True,
            'exit_code': 0 if passed else 1, 'timed_out': False, 'output_limit_exceeded': False,
            'error': None, 'duration_seconds': 0.02, 'stdout': '', 'stderr': output}]}

    def test_all_explicit_sections(self):
        text = self.text()
        for section in SECTIONS:
            self.assertIn('\n' + section + '\n', text)
        self.assertIn(self.state.task.text, text)

    def test_original_task_remains_canonical(self):
        original = self.state.task
        self.read()
        self.render()
        self.assertIs(self.state.task, original)
        self.assertEqual(self.state.context['original_task'], 'run_state.task')

    def test_plan_is_labeled_as_model_proposal_not_verification(self):
        self.observe('finish', message='I already ran all checks successfully')
        text = self.text()
        self.assertIn('CURRENT PLAN (model proposal, not execution evidence)', text)
        self.assertIn('I already ran all checks successfully', text)
        self.assertEqual(self.state.verification_status, 'NOT_RUN')
        self.assertFalse(any(i.section == 'VERIFICATION' for i in self.context.items.values()))

    def test_failed_patch_does_not_record_modified_file(self):
        self.observe('apply_patch', None, {'edits': [{'path': 'calculator.py'}]}, 'TOOL_ERROR')
        self.assertFalse(any(i.section == 'CURRENT CHANGES' for i in self.context.items.values()))

    def test_read_deduplicates_and_keeps_latest_version(self):
        self.read(content='old average code\n', sha='old')
        for _ in range(20):
            self.read(content='new average code\n', sha='new')
        text = self.text()
        self.assertNotIn('old average code', text)
        self.assertEqual(text.count('new average code'), 1)
        self.assertEqual(sum(i.key.startswith('read:') for i in self.context.items.values()), 1)

    def test_search_is_replaced_by_read_without_duplicate_code(self):
        self.observe('search_code', {'matches': [{'path': 'calculator.py', 'line': 1, 'text': 'average_marker'}]}, {'query': 'average'})
        self.read(content='average_marker\n')
        self.assertEqual(self.text().count('average_marker'), 1)

    def test_search_after_read_does_not_repeat_same_code(self):
        self.read(content='average_marker\n')
        self.observe('search_code', {'matches': [{'path': 'calculator.py', 'line': 1, 'text': 'average_marker'}]}, {'query': 'average'})
        self.assertEqual(self.text().count('average_marker'), 1)

    def test_search_results_track_paths_and_line_numbers(self):
        self.observe('search_code', {'matches': [{'path': 'calculator.py', 'line': 42, 'text': 'average(())'}]}, {'query': 'average'})
        self.assertIn('"line": 42', self.text())
        self.assertIn('average(())', self.text())

    def test_tests_are_separate_from_source(self):
        self.read('tests/test_calculator.py', 'def test_average_empty(): pass\n')
        item = next(i for i in self.context.items.values() if i.key.startswith('read:'))
        self.assertEqual(item.section, 'TESTS')

    def test_large_file_is_relevant_snippets_not_whole_file(self):
        content = '# unrelated\n' * 2000 + 'def average_empty_input():\n    return 0\n' + '# unrelated\n' * 2000
        self.read(content=content)
        text = self.text()
        self.assertIn('def average_empty_input', text)
        self.assertIn('Selected snippet', text)
        self.assertNotIn(content, text)
        self.assertLess(text.count('# unrelated'), 100)

    def test_repository_listing_is_subset_and_not_contents(self):
        paths = [f'noise/{n}.txt' for n in range(1000)] + ['calculator.py']
        self.observe('list_files', {'files': paths, 'count': len(paths), 'truncated': False})
        text = self.text()
        self.assertIn('calculator.py', text)
        self.assertLess(sum(p in text for p in paths), 100)
        self.assertLessEqual(len(self.context.items), self.state.budgets.max_context_items)

    def test_irrelevant_old_content_not_sent_every_request(self):
        self.read('unrelated.txt', 'UNRELATED_MARKER\n')
        for _ in range(15): self.read()
        text = self.text()
        self.assertIn('def average', text)
        self.assertNotIn('UNRELATED_MARKER', text)

    def test_memory_and_requests_bounded_over_many_unique_actions(self):
        self.state.budgets = BudgetConfig(max_context_items=12, max_context_item_chars=350,
            max_context_memory_bytes=5000, max_context_chars=9500, max_context_bytes=14000)
        for n in range(250):
            self.read(f'src/average_{n}.py', 'average repeated text\n' * 300)
            if n % 10 == 0:
                messages = self.render()
                chars, size = request_size(messages, self.definitions)
                self.assertLessEqual(chars, 9500)
                self.assertLessEqual(size, 14000)
            self.assertLessEqual(len(self.context.items), 12)
            self.assertLessEqual(self.state.usage.context_memory_bytes, 5000)
        self.assertGreater(self.context.evicted, 0)
        self.assertTrue(self.state.context['retained_subset'])

    def test_repeated_identical_observation_does_not_accumulate_history(self):
        for _ in range(100):
            self.observe('list_files', {'files': ['calculator.py'], 'count': 1, 'truncated': False})
        self.assertLess(len(self.context.items), 6)
        self.assertGreater(self.context.deduplicated, 90)

    def test_recent_failure_tail_survives_large_output(self):
        self.state.budgets = BudgetConfig(max_context_chars=9000)
        self.observe('run_checks', self.check_result(output='noise\n' * 5000 + 'ZeroDivisionError: latest failure'))
        self.assertIn('ZeroDivisionError: latest failure', self.text())
        self.assertIn('truncated', self.text())

    def test_failure_history_cannot_displace_all_code(self):
        for n in range(20):
            self.context.observe({'error': {'code': 'OLD_ERROR', 'message': str(n)}})
        self.read(content='def average(): return 0\n')
        self.context.observe({'error': {'code': 'LATEST_ERROR'}})
        text = self.text()
        self.assertIn('LATEST_ERROR', text)
        self.assertIn('def average()', text)
        self.assertLessEqual(sum(i.section == 'FAILURES' for i in self.context.items.values()), 8)

    def test_patch_invalidates_cached_code_and_verification(self):
        self.read(content='STALE_CODE average\n')
        self.context.verification({'passed': True, 'snapshot_sha256': 'old'})
        self.observe('apply_patch', {'applied': True, 'changes': [{'path': 'calculator.py', 'operation': 'replace', 'after_sha256': 'new'}]})
        text = self.text()
        self.assertNotIn('STALE_CODE', text)
        self.assertIn('STALE / NOT_RUN', text)
        self.assertIn('after_sha256', text)
        self.assertNotIn('"passed": true', text)

    def test_dry_run_never_becomes_actual_changes(self):
        self.read()
        self.observe('apply_patch', {'applied': False, 'dry_run': True, 'changes': [{'path': 'calculator.py'}]})
        self.assertFalse(any(i.key.startswith('patch:') for i in self.context.items.values()))
        self.assertIn('def average', self.text())

    def test_successful_recheck_replaces_current_check_failure(self):
        self.observe('run_checks', self.check_result(output='FAILED unit'))
        self.observe('run_checks', self.check_result(passed=True, output='Ran 3 tests\nOK'))
        self.assertNotIn(('FAILURES', 'check:unit'), self.context.items)
        self.assertIn('Ran 3 tests', self.text())

    def test_verification_assessment_keeps_actual_source(self):
        self.context.verification({'passed': False, 'stable_workspace': False,
            'source': 'RepositoryTools.run_checks', 'results': []})
        self.assertIn('"stable_workspace": false', self.text())
        self.assertEqual(self.state.verification_status, 'NOT_RUN')  # context never sets the verdict

    def test_actual_git_status_and_diff_tracked(self):
        self.observe('get_changes', {'clean': False, 'truncated': False, 'status': [{'path': 'calculator.py', 'worktree_status': 'M'}],
            'unstaged_diff': 'diff --git a/calculator.py b/calculator.py\n+average changed\n',
            'staged_diff': '', 'untracked_files': []})
        text = self.text()
        self.assertIn('average changed', text)
        self.assertIn('worktree_status', text)

    def test_secrets_are_redacted_in_retained_state_and_messages(self):
        self.read(content='average ' + self.secret + '\n')
        self.observe('read_file', message='plan ' + self.secret)
        self.context.observe_tool('read_file', {}, {'path': self.secret, 'content': self.secret})
        self.render()
        self.assertNotIn(self.secret, encode(self.state.context))
        self.assertNotIn(self.secret, self.text())

    def test_task_and_sections_too_large_fail_before_generation(self):
        self.state.budgets = BudgetConfig(max_context_chars=10)
        with self.assertRaises(BudgetExceeded): self.render()

    def test_memory_limit_too_small_fails_honestly(self):
        self.state.budgets = BudgetConfig(max_context_memory_bytes=1)
        with self.assertRaises(BudgetExceeded): self.render()

    def test_utf8_bytes_and_json_escaping_are_bounded(self):
        self.state.budgets = BudgetConfig(max_context_chars=64000, max_context_bytes=11000)
        self.read(content='average ' + ('漢字😀\\\n' * 4000))
        messages = self.render()
        chars, size = request_size(messages, self.definitions)
        self.assertLessEqual(size, 11000)
        self.assertGreater(size, chars)

    def test_unknown_token_count_is_null_and_estimate_is_labeled(self):
        request = ModelRequest(self.render(), self.definitions)
        estimated = measure_request(request, MockModelAdapter([]), self.state)
        self.assertIsNone(self.state.usage.context_tokens)
        self.assertEqual(self.state.usage.context_token_source, 'unavailable')
        self.assertEqual(estimated, self.state.usage.context_bytes + 512)
        self.assertIn('not a tokenizer', self.state.usage.context_estimation_method)
        self.assertEqual(self.state.usage.input_tokens, 0)

    def test_optional_exact_input_counter_used(self):
        class ByteTokenFixture:
            """A toy byte-token input codec, not a DeepSeek/Qwen tokenizer."""
            def count_input_tokens(self, request):
                return len(encode({'messages': [asdict(m) for m in request.messages],
                                   'tools': [asdict(t) for t in request.tools]}).encode('utf-8'))
        request = ModelRequest(self.render(), self.definitions)
        adapter = ByteTokenFixture()
        count = measure_request(request, adapter, self.state)
        self.assertEqual(count, adapter.count_input_tokens(request))
        self.assertEqual(self.state.usage.context_tokens, count)
        self.assertEqual(self.state.usage.context_token_source, 'adapter_exact_input')

    def test_invalid_or_failed_exact_counter_falls_back_without_claim(self):
        class Counter:
            def __init__(self, value): self.value = value
            def count_input_tokens(self, request):
                if isinstance(self.value, Exception): raise self.value
                return self.value
        request = ModelRequest(self.render(), self.definitions)
        for value in (True, -1, '10', RuntimeError(self.secret)):
            with self.subTest(value=type(value).__name__):
                measured = measure_request(request, Counter(value), self.state)
                self.assertEqual(measured, self.state.usage.context_bytes + 512)
                self.assertIsNone(self.state.usage.context_tokens)
                self.assertEqual(self.state.usage.context_token_source, 'unavailable_counter_error')
                self.assertNotIn(self.secret, encode(asdict(self.state.usage)))

    def test_exact_counter_none_means_unavailable(self):
        class Counter:
            def count_input_tokens(self, request): return None
        measure_request(ModelRequest(self.render(), self.definitions), Counter(), self.state)
        self.assertEqual(self.state.usage.context_token_source, 'unavailable')

    def test_context_peaks_do_not_shrink(self):
        large = ModelRequest((ModelMessage('user', 'a' * 1000),))
        small = ModelRequest((ModelMessage('user', 'a'),))
        adapter = MockModelAdapter([])
        measure_request(large, adapter, self.state)
        peak = self.state.usage.peak_context_bytes
        measure_request(small, adapter, self.state)
        self.assertEqual(self.state.usage.peak_context_bytes, peak)
        self.assertLess(self.state.usage.context_bytes, peak)


class ContextConfigTests(FoundationTestCase):
    def test_new_limits_use_existing_environment_cli_and_toml_precedence(self):
        config = self.base / 'context.toml'
        config.write_text('[budgets]\nmax_context_bytes=20000\nmax_context_items=24\n')
        result = load_config(self.project, config_path=config, env={'HARNESS_MAX_CONTEXT_BYTES': '21000'},
                             overrides={'budgets': {'max_context_items': 30}})
        self.assertEqual(result.budgets.max_context_bytes, 21000)
        self.assertEqual(result.budgets.max_context_items, 30)
        args = build_parser().parse_args(['--max-context-memory-bytes', '12000', '--max-context-item-chars', '1500'])
        self.assertEqual(args.max_context_memory_bytes, 12000)
        self.assertEqual(args.max_context_item_chars, 1500)

    def test_invalid_new_limits_are_rejected(self):
        for key in ('max_context_bytes', 'max_context_items', 'max_context_memory_bytes', 'max_context_item_chars'):
            for value in (0, -1, True):
                with self.subTest(key=key, value=value), self.assertRaises(ConfigurationError):
                    load_config(self.project, env={}, overrides={'budgets': {key: value}})


class ContextLoopTests(ToolTestCase):
    def setup_agent(self, script, **budgets):
        state = RunState(BudgetConfig(max_total_tokens=1000000, **budgets), workspace=str(self.target),
                         task=validate_task('Fix average empty input in calculator.py', 'test', 64000))
        model = MockModelAdapter(script)
        tools = self.new_tools(checks=(UNIT,), state=state)
        return AgentController(model, tools, state), model, state

    def test_real_mock_loop_selects_evidence_and_tracks_state(self):
        controller, model, state = self.setup_agent(repair_script(), max_context_chars=10000)
        result = controller.run()
        self.assertEqual(result.status, 'COMPLETED', result.to_dict())
        self.assertEqual(result.steps[3]['status'], 'CHECKS_FAILED')
        self.assertIn('FAILED', '\n'.join(m.content for m in model.requests[4].messages))
        self.assertEqual(state.usage.test_executions, 2)
        self.assertEqual(state.usage.context_selections, 7)
        for request in model.requests:
            chars, size = request_size(request.messages, request.tools)
            self.assertLessEqual(chars, 10000)
            self.assertLessEqual(size, state.budgets.max_context_bytes)
            for section in SECTIONS:
                self.assertIn('\n' + section + '\n', request.messages[-1].content)
        self.assertGreater(state.usage.context_bytes, 0)
        self.assertIsNone(state.usage.context_tokens)
        self.assertIn('Controller assessment', json.dumps(state.to_dict()['context']))
        self.assertTrue(any(i.path == 'calculator.py' and i.section == 'CURRENT CHANGES'
                            for i in controller.context.items.values()))

    def test_unread_irrelevant_repository_content_never_reaches_model(self):
        self.write('unrelated.txt', 'SHOULD_NOT_REACH_MODEL\n' * 4000)
        controller, model, _ = self.setup_agent([READ, edit(), FINISH])
        result = controller.run()
        self.assertEqual(result.status, 'COMPLETED', result.to_dict())
        self.assertNotIn('SHOULD_NOT_REACH_MODEL', json.dumps([r.to_dict() for r in model.requests]))

    def test_step_path_also_measures_context_and_honors_byte_limit(self):
        controller, model, state = self.setup_agent([READ], max_context_bytes=100)
        result = controller.step((ModelMessage('user', 'read calculator.py'),))
        self.assertEqual(result.status, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(model.requests), 0)
        self.assertGreater(state.usage.context_bytes, 100)

    def test_unrepresentably_small_context_memory_terminates_gracefully(self):
        controller, model, state = self.setup_agent([READ], max_context_memory_bytes=1)
        result = controller.run()
        self.assertEqual(result.status, 'BUDGET_EXHAUSTED')
        self.assertEqual(len(model.requests), 0)
        self.assertNotEqual(state.task_result, 'VERIFIED')

    def test_exact_counter_is_used_for_budget_reservation(self):
        controller, _, state = self.setup_agent([READ])
        class CountingMock(MockModelAdapter):
            def count_input_tokens(self, request): return 17  # capability contract test fixture only
        model = CountingMock([READ])
        step = ModelStepController(model, controller.tools, state, max_tokens=50)
        result = step.step((ModelMessage('user', 'read calculator.py'),))
        self.assertEqual(result.status, 'ACTION_COMPLETED', result.to_dict())
        self.assertEqual(state.usage.context_tokens, 17)
        self.assertEqual(state.usage.estimated_tokens, 67)  # unreported billing reservation, not actual usage
        self.assertEqual(state.usage.input_tokens, 0)


if __name__ == '__main__':
    unittest.main()
