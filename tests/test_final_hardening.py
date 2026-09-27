import unittest
from ai_harness.test_selection import discover_checks
from ai_harness.tool_types import ToolError
from tool_support import ToolTestCase

class DiscoveryHardeningTests(ToolTestCase):
    def test_nested_nonpackage_tests_are_required_not_silently_skipped(self):
        self.write('tests/deep/test_nested.py','import unittest\nclass T(unittest.TestCase):\n def test_fail(self): self.fail("nested failure")\n')
        checks=discover_checks(self.tools.io)
        required=[c for c in checks if c.required]
        self.assertTrue(any('tests/deep' in c.argv for c in required))
        tools=self.new_tools(checks=checks)
        result=tools.call('run_checks',{'names':[c.name for c in required]})
        self.assertFalse(result['all_passed'])
        self.assertTrue(any('nested failure' in r['stderr'] for r in result['results']))
    def test_framework_discovery_does_not_stop_after_24_files(self):
        for n in range(25):self.write(f'tests/test_a{n:02}.py','import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n')
        self.write('tests/test_zzz.py','def test_bad(): assert False\n')
        self.assertIn('pytest',discover_checks(self.tools.io)[0].argv)
    def test_suffix_test_files_are_not_skipped(self):
        self.write('tests/extra_test.py','import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail()\n')
        self.assertTrue(any('*_test.py' in c.argv and c.required for c in discover_checks(self.tools.io)))
