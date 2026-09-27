from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import socket
import time
from unittest import mock

from ai_harness.config import BudgetConfig
from ai_harness.errors import BudgetExceeded, ConfigurationError
from ai_harness.execution import child_environment
from ai_harness.state import RunState
from ai_harness.tool_types import CheckSpec, ExecutionBlocked, PathViolation, ToolError, ToolLimits
from tool_support import ToolTestCase, UNIT


class ExecutionTests(ToolTestCase):
    def test_real_stdout_stderr_exit_duration(self):
        result = self.run_python('import sys; print("actual stdout"); print("actual stderr",file=sys.stderr)')
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['stdout'], 'actual stdout\n')
        self.assertEqual(result['stderr'], 'actual stderr\n')
        self.assertEqual(result['exit_code'], 0)
        self.assertGreater(result['duration_seconds'], 0)
        self.assertTrue(result['command_started'])

    def test_command_failure_is_not_success(self):
        result = self.run_python('import sys; print("failing",file=sys.stderr); sys.exit(7)')
        self.assertEqual(result['exit_code'], 7)
        self.assertFalse(result['passed'])
        self.assertIn('failing', result['stderr'])

    def test_real_failing_fixture_tests(self):
        result = self.new_tools(checks=(UNIT,)).run_checks()
        self.assertFalse(result['all_passed'])
        self.assertIn('ZeroDivisionError', result['results'][0]['stderr'])
        self.assertIn('Ran 3 tests', result['results'][0]['stderr'])

    def test_syntax_error_is_reported_then_repaired(self):
        t = self.new_tools(checks=(CheckSpec('syntax', ('{python}', '-m', 'py_compile', 'broken.py')),))
        t.apply_patch([{'path':'broken.py','operation':'create','new':'def invalid(:\n    return 1\n'}])
        bad = t.run_checks()['results'][0]
        self.assertFalse(bad['passed'])
        self.assertIn('SyntaxError', bad['stderr'])
        t.apply_patch([{'path':'broken.py','old':'def invalid(:','new':'def invalid():'}])
        self.assertTrue(t.run_checks()['all_passed'])

    def test_timeout_returns_captured_partial_output(self):
        result = self.run_python('import time; print("started",flush=True); time.sleep(30)', timeout=0.2)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['passed'])
        self.assertIn('started', result['stdout'])
        self.assertLess(result['duration_seconds'], 3)
        self.assertNotEqual(result['exit_code'], 0)

    def test_timeout_kills_detached_descendant(self):
        code = ('import os,time\npid=os.fork()\n'
                'if pid == 0:\n    os.setsid()\n    time.sleep(.6)\n    open("late.txt","w").write("bad")\n'
                'else:\n    time.sleep(20)\n')
        result = self.run_python(code, timeout=0.2)
        self.assertTrue(result['timed_out'])
        time.sleep(0.7)
        self.assertFalse((self.target / 'late.txt').exists())

    def test_stdin_is_closed(self):
        result = self.run_python('import sys; print(repr(sys.stdin.read()))')
        self.assertEqual(result['stdout'], "''\n")
        self.assertTrue(result['passed'])

    def test_output_limit_terminates_and_marks_failure(self):
        result = self.run_python('import os\nwhile True: os.write(1,b"x"*4096)',
                                 limits=replace(ToolLimits(), max_output_bytes=2048))
        self.assertTrue(result['output_limit_exceeded'])
        self.assertFalse(result['passed'])
        self.assertLessEqual(len(result['stdout'].encode()) + len(result['stderr'].encode()), 2048)

    def test_stdout_and_stderr_are_drained_without_deadlock(self):
        result = self.run_python('import os\nfor i in range(5):\n os.write(1,b"a"*4096)\n os.write(2,b"b"*4096)')
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['stdout']), 5 * 4096)
        self.assertEqual(len(result['stderr']), 5 * 4096)

    def test_invalid_utf8_output_is_explicitly_replaced(self):
        result = self.run_python('import os; os.write(1,b"\\xff")')
        self.assertTrue(result['passed'])
        self.assertEqual(result['stdout'], '\ufffd')

    def test_credentials_are_not_in_child_environment(self):
        with mock.patch.dict(os.environ, {'AI_API_KEY': self.secret, 'AWS_SECRET_ACCESS_KEY': self.secret,
                                          'PYTHONPATH': str(self.base), 'LD_PRELOAD': self.secret}):
            result = self.run_python('import os; print(dict(os.environ))')
        self.assertTrue(result['passed'], result)
        self.assertNotIn('AI_API_KEY', result['stdout'])
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', result['stdout'])
        self.assertNotIn('LD_PRELOAD', result['stdout'])
        self.assertNotIn(self.secret, result['stdout'] + result['stderr'])

    def test_credential_in_target_output_is_redacted(self):
        self.write('print_value.py', 'print(' + repr(self.secret) + ')\n')
        result = self.new_tools(checks=(CheckSpec('print', ('{python}', 'print_value.py')),)).run_checks()
        self.assertTrue(result['all_passed'])
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertIn('[REDACTED]', json.dumps(result))

    def test_shell_characters_are_literal_arguments(self):
        value = '; touch injected.txt && echo bad'
        t = self.new_tools(checks=(CheckSpec('literal', ('{python}', '-c', 'import sys; print(sys.argv[1])', value)),))
        result = t.run_checks()['results'][0]
        self.assertTrue(result['passed'])
        self.assertIn(value, result['stdout'])
        self.assertFalse((self.target / 'injected.txt').exists())

    def test_shell_executable_is_rejected(self):
        for command in ['/bin/sh', '/usr/bin/bash', '/usr/bin/env']:
            with self.assertRaises(ConfigurationError): CheckSpec('bad', (command, '-c', 'true'))

    def test_arbitrary_check_request_cannot_change_argv(self):
        with self.assertRaises(ToolError):
            self.tools.call('run_checks', {'argv':['/bin/sh','-c','true']})

    def test_no_configured_checks_is_not_a_pass(self):
        with self.assertRaises(ToolError): self.tools.run_checks()

    def test_unknown_check_name_is_rejected(self):
        with self.assertRaises(ToolError): self.new_tools(checks=(UNIT,)).run_checks(['unknown'])

    def test_missing_executable_records_launch_failure(self):
        result = self.new_tools(checks=(CheckSpec('missing',('/usr/bin/no-such-phase2-command',)),)).run_checks()['results'][0]
        self.assertFalse(result['passed'])
        self.assertFalse(result['command_started'])
        self.assertEqual(result['exit_code'], 126)
        self.assertIsNotNone(result['error'])

    def test_missing_unshare_fails_closed(self):
        with mock.patch('ai_harness.execution.shutil.which', return_value=None):
            with self.assertRaises(ExecutionBlocked): self.new_tools(checks=(UNIT,)).run_checks()

    def test_denied_namespace_creation_does_not_fake_execution(self):
        # Fault injection substitutes a real failing executable for the launcher.
        with mock.patch('ai_harness.execution.shutil.which', return_value='/usr/bin/false'):
            result = self.new_tools(checks=(UNIT,)).run_checks()['results'][0]
        self.assertFalse(result['command_started'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['exit_code'], 1)

    def test_child_cwd_is_workspace_relative(self):
        self.write('nested/value.txt', 'nested')
        t = self.new_tools(checks=(CheckSpec('cwd',('{python}','-c','import os; print(os.getcwd()); print(open("value.txt").read())'),cwd='nested'),))
        result = t.run_checks()['results'][0]
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['stdout'], '/workspace/nested\nnested\n')

    def test_traversal_cwd_rejected(self):
        with self.assertRaises(ConfigurationError): CheckSpec('bad',('{python}','-V'),cwd='../')

    def test_sandbox_denies_host_file_reads_and_writes(self):
        outside = self.base / 'host-private.txt'; outside.write_text(self.secret)
        script = ('from pathlib import Path\np=Path(' + repr(str(outside)) + ')\n'
                  'for mode in ("r", "w"):\n'
                  ' try:\n  with p.open(mode) as f: f.read() if mode=="r" else f.write("bad")\n'
                  ' except OSError: print("denied",mode)\n'
                  ' else: raise AssertionError("outside access allowed")\n'
                  'Path("inside.txt").write_text("allowed")\n')
        result = self.run_python(script)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['stdout'], 'denied r\ndenied w\n')
        self.assertEqual(outside.read_text(), self.secret)
        self.assertEqual((self.target / 'inside.txt').read_text(), 'allowed')

    def test_parent_traversal_in_child_cannot_write_host(self):
        result = self.run_python('from pathlib import Path\ntry: Path("../outside.txt").write_text("bad")\nexcept OSError: print("denied")\nelse: raise AssertionError("outside writable")')
        self.assertTrue(result['passed'])
        self.assertEqual(result['stdout'], 'denied\n')
        self.assertFalse((self.base / 'outside.txt').exists())

    def test_git_metadata_is_readonly_and_masked_to_checks(self):
        before = (self.target / '.git/HEAD').read_text()
        result = self.run_python('from pathlib import Path\ntry: Path(".git/HEAD").write_text("bad")\nexcept OSError: print("denied")\nelse: raise AssertionError("git metadata writable")')
        self.assertTrue(result['passed'], result)
        self.assertEqual((self.target / '.git/HEAD').read_text(), before)

    def test_target_dotenv_is_masked(self):
        self.write('.env', self.secret)
        result = self.run_python('print(repr(open(".env").read()))')
        self.assertTrue(result['passed'])
        self.assertEqual(result['stdout'], "''\n")
        self.assertEqual((self.target / '.env').read_text(), self.secret)

    def test_network_namespace_cannot_reach_host_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); listener.listen()
            port = listener.getsockname()[1]
            result = self.run_python('import socket\ns=socket.socket(); s.settimeout(.2)\n'
                                     f'try: s.connect(("127.0.0.1",{port}))\n'
                                     'except OSError: print("isolated")\nelse: raise AssertionError("host reachable")')
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['stdout'], 'isolated\n')

    def test_execution_rejects_hardlinked_workspace(self):
        outside = self.base / 'host-file'; outside.write_text('original')
        os.link(outside, self.target / 'hardlink')
        with self.assertRaises(PathViolation): self.new_tools(checks=(UNIT,)).run_checks()
        self.assertEqual(outside.read_text(), 'original')

    def test_execution_rejects_symlinked_workspace_member(self):
        (self.target / 'alias').symlink_to('calculator.py')
        with self.assertRaises(PathViolation): self.new_tools(checks=(UNIT,)).run_checks()

    def test_multiple_checks_keep_actual_individual_results(self):
        t = self.new_tools(checks=(CheckSpec('ok',('{python}','-c','print("ok")')),
                                  CheckSpec('bad',('{python}','-c','raise SystemExit(4)'))))
        result = t.run_checks(['bad','ok'])
        self.assertFalse(result['all_passed'])
        self.assertEqual([r['exit_code'] for r in result['results']], [4,0])

    def test_resource_accounting_and_no_task_verdict(self):
        state = RunState(BudgetConfig())
        t = self.new_tools(checks=(CheckSpec('ok',('{python}','-c','print("ok")')),), state=state)
        self.assertTrue(t.run_checks()['all_passed'])
        self.assertEqual(state.usage.tool_calls, 1)
        self.assertEqual(state.usage.test_executions, 1)
        self.assertEqual(state.usage.model_calls, 0)
        self.assertIsNone(state.to_dict()['task_result'])

    def test_tool_call_budget_is_enforced(self):
        state = RunState(replace(BudgetConfig(),max_tool_calls=1))
        t=self.new_tools(state=state); t.list_files()
        with self.assertRaises(BudgetExceeded): t.read_file('calculator.py')

    def test_check_execution_budget_is_enforced(self):
        state=RunState(replace(BudgetConfig(),max_test_executions=1))
        t=self.new_tools(checks=(CheckSpec('ok',('{python}','-V')),),state=state)
        t.run_checks()
        with self.assertRaises(BudgetExceeded): t.run_checks()

    def test_no_new_privileges_and_no_host_proc(self):
        result = self.run_python('import ctypes,os\nlibc=ctypes.CDLL(None)\nprint(libc.prctl(39,0,0,0,0))\nprint(os.path.exists("/proc/1/environ"))')
        self.assertTrue(result['passed'])
        self.assertEqual(result['stdout'], '1\nFalse\n')
