from dataclasses import replace
import unittest

from ai_harness.model import ModelAdapter
from ai_harness.mock_model import MockModelAdapter
from ai_harness.model_tools import repository_tool_definitions
from ai_harness.model_types import (ModelError, ModelMessage, ModelRequest, ModelResponse, TokenUsage,
                                    strict_json, validate_response)
from model_support import request


class ModelTypesTests(unittest.TestCase):
    def test_script_requests_exact_three_actions(self):
        model = MockModelAdapter([
            ModelResponse('', 'read_file', {'path': 'calculator.py'}, 'tool_call'),
            ModelResponse('', 'apply_patch', {'edits': [{'path': 'calculator.py', 'old': 'a', 'new': 'b'}]}, 'tool_call'),
            ModelResponse('', 'run_checks', {}, 'tool_call'),
        ])
        self.assertIsInstance(model, ModelAdapter)
        self.assertEqual([model.generate(request()).requested_action for _ in range(3)], ['read_file', 'apply_patch', 'run_checks'])
        self.assertEqual(len(model.requests), 3)

    def test_mock_is_honestly_labeled(self):
        model = MockModelAdapter([ModelResponse('done')])
        self.assertTrue(model.metadata()['is_mock'])
        self.assertFalse(model.health_check(live=True).network_checked)
        self.assertEqual(model.health_check().status, 'MOCK_ONLY')
        self.assertEqual(model.generate(request()).raw_metadata['source'], 'mock')

    def test_mock_exhaustion_is_not_success(self):
        with self.assertRaises(ModelError) as result:
            MockModelAdapter([]).generate(request())
        self.assertEqual(result.exception.code, 'MOCK_EXHAUSTED')

    def test_bad_script_does_not_block_next_response(self):
        model = MockModelAdapter(['not JSON', ModelResponse('next valid response')])
        with self.assertRaises(ModelError):
            model.generate(request())
        self.assertEqual(model.generate(request()).message, 'next valid response')

    def test_invalid_script_payloads_are_controlled(self):
        for value in [None, [], {'unknown': 1}, {'message': 1}, {'message': 'x', 'usage': []}]:
            with self.subTest(value=value), self.assertRaises(ModelError):
                MockModelAdapter([value]).generate(request())

    def test_scripted_errors_are_not_success(self):
        with self.assertRaises(ModelError) as error:
            MockModelAdapter([ModelError('TIMEOUT', 'Scripted failure')]).generate(request())
        self.assertEqual(error.exception.code, 'TIMEOUT')

    def test_before_attempt_runs_once(self):
        calls = []
        MockModelAdapter([ModelResponse('ok')]).generate(request(), before_attempt=lambda: calls.append(1))
        self.assertEqual(calls, [1])

    def test_missing_usage_stays_unknown(self):
        self.assertIsNone(ModelResponse('ok').usage.total_tokens)
        self.assertFalse(TokenUsage().known)

    def test_bad_usage_is_rejected(self):
        for args in [dict(total_tokens=-1), dict(input_tokens=True), dict(output_tokens=1.5),
                     dict(total_tokens=float('nan')), dict(input_tokens=4, output_tokens=8, total_tokens=5)]:
            with self.subTest(args=args), self.assertRaises(ModelError):
                TokenUsage(**args)

    def test_response_rejects_wrong_types(self):
        for args in [dict(message=1), dict(message='x', arguments=[]), dict(message='x', raw_metadata=[]),
                     dict(message='x', finish_reason='invented'), dict(message='x', requested_action=['x']),
                     dict(message='x', finish_reason='tool_call'), dict(message='x', arguments={'path': 'a'}),
                     dict(message='x', requested_action='read_file', finish_reason='length')]:
            with self.subTest(args=args), self.assertRaises(ModelError):
                ModelResponse(**args)

    def test_tool_name_and_schema_are_validated(self):
        for name, args in [('shell', {}), ('read_file', {}), ('read_file', {'path': 'a', 'extra': 1}),
                           ('read_file', {'path': 'a', 'start_line': True}), ('run_checks', {'names': 'unit'}),
                           ('apply_patch', {'edits': []}), ('apply_patch', {'edits': [{'path': 'a', 'new': 1}]})]:
            with self.subTest(name=name, args=args), self.assertRaises(ModelError):
                validate_response(ModelResponse('', name, args, 'tool_call'), request())

    def test_json_rejects_duplicates_nonfinite_and_trailing_data(self):
        for text in ['{"a":1,"a":2}', '{"a":NaN}', '{} {}', '\xff', '{"a":Infinity}']:
            with self.subTest(text=text), self.assertRaises(ModelError):
                strict_json(text)

    def test_json_size_and_depth_are_bounded(self):
        with self.assertRaises(ModelError):
            strict_json('"' + 'x' * 100 + '"', limit=50)
        with self.assertRaises(ModelError):
            strict_json('[' * 100 + '0' + ']' * 100)

    def test_arguments_are_detached_from_source(self):
        arguments = {'path': 'a'}
        response = ModelResponse('', 'read_file', arguments, 'tool_call')
        arguments['path'] = 'b'
        self.assertEqual(response.arguments['path'], 'a')

    def test_mutated_response_revalidated_before_execution(self):
        response = ModelResponse('', 'read_file', {'path': 'a'}, 'tool_call')
        response.arguments['unknown'] = True
        with self.assertRaises(ModelError):
            validate_response(response, request())

    def test_request_validation(self):
        for args in [dict(messages=()), dict(messages=('text',)), dict(messages=request().messages, max_tokens=0),
                     dict(messages=request().messages, max_retries=21), dict(messages=request().messages, timeout_seconds=True)]:
            with self.subTest(args=args), self.assertRaises(ModelError):
                ModelRequest(**args)

    def test_six_definitions_match_existing_tool_names(self):
        self.assertEqual({t.name for t in repository_tool_definitions()},
                         {'list_files', 'search_code', 'read_file', 'apply_patch', 'run_checks', 'get_changes'})
