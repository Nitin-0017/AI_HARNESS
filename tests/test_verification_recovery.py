import unittest
from ai_harness.verification import assess, failure_category, fingerprint
from ai_harness.tool_types import CheckSpec
import test_agent_loop as agent_support


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.spec=CheckSpec('unit',('{python}','-m','unittest','discover'))
        self.good={'name':'unit','command_started':True,'exit_code':0,'timed_out':False,
                   'output_limit_exceeded':False,'error':None,'stdout':'','stderr':'Ran 3 tests in 0.01s\nOK',
                   'duration_seconds':0.01}
    def test_actual_pass_latest_state(self):
        r=assess([self.good],{'unit':self.spec},'a','a')
        self.assertTrue(r.passed); self.assertEqual(r.status,'VERIFIED'); self.assertEqual(r.duration,0.01)
    def test_zero_tests_are_not_verification(self):
        r=assess([{**self.good,'stderr':'Ran 0 tests in 0.0s\nOK'}],{'unit':self.spec},'a','a')
        self.assertFalse(r.passed)
    def test_stale_checks_are_not_verification(self):
        self.assertFalse(assess([self.good],{'unit':self.spec},'a','b').passed)
    def test_output_and_exit_must_agree(self):
        r=assess([{**self.good,'stderr':'Ran 3 tests\nFAILED (failures=1)'}],{'unit':self.spec},'a','a')
        self.assertFalse(r.passed)
    def test_all_failure_categories(self):
        cases=[({'stderr':'SyntaxError: bad'},'syntax_error'),({'stderr':'AssertionError: bad'},'test_assertion_failure'),
               ({'stderr':'ModuleNotFoundError: x'},'missing_dependency'),({'timed_out':True},'timeout'),
               ({'command_started':False},'environment_failure'),({'stderr':'PatchError mismatch'},'invalid_patch'),
               ({'exit_code':7},'command_failure'),({},'unknown_failure')]
        for value,expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(failure_category({'command_started':True,**value}),expected)
    def test_fingerprint_normalizes_volatile_parts(self):
        a=fingerprint('test_assertion_failure',['python'],'File /tmp/run1/a.py at 0xabcd','app.py')
        b=fingerprint('test_assertion_failure',['python'],'File /tmp/run2/a.py at 0x1234','app.py')
        self.assertEqual(a,b)
        self.assertNotEqual(a,fingerprint('syntax_error',['python'],'File /tmp/run2/a.py at 0x1234','app.py'))
