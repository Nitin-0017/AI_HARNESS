from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile

from ai_harness.config import BudgetConfig
from ai_harness.state import RunState
from ai_harness.tool_types import CheckSpec, ToolError, ToolLimits
from fixture_repositories import create_fixture, git
from tool_support import ToolTestCase, UNIT


class Phase2IntegrationTests(ToolTestCase):
    def test_inspect_edit_fail_repair_test_actual_diff(self):
        state=RunState(BudgetConfig())
        t=self.new_tools(checks=(UNIT,),state=state)
        self.assertIn('calculator.py',t.list_files()['files'])
        self.assertEqual(t.search_code('def average',pattern='calculator.py')['matches'][0]['line'],1)
        old=t.read_file('calculator.py')
        self.assertFalse(t.run_checks()['all_passed'])
        # Deliberately wrong code, not a fake failed-test response.
        t.apply_patch([{'path':'calculator.py','old':'return sum(numbers) / len(numbers)',
                       'new':'return 0','expected_sha256':old['sha256']}])
        failed=t.run_checks()
        self.assertFalse(failed['all_passed'])
        self.assertIn('FAIL',failed['results'][0]['stderr'])
        t.apply_patch([{'path':'calculator.py','old':'return 0',
                       'new':'return 0 if not numbers else sum(numbers) / len(numbers)'}])
        passed=t.run_checks()
        self.assertTrue(passed['all_passed'],passed)
        self.assertIn('Ran 3 tests',passed['results'][0]['stderr'])
        changes=t.get_changes()
        self.assertIn('+    return 0 if not numbers',changes['unstaged_diff'])
        self.assertEqual([e['path'] for e in changes['status']],['calculator.py'])
        self.assertEqual(state.usage.model_calls,0)
        self.assertEqual(state.usage.test_executions,3)
        self.assertEqual(state.usage.failures,2)
        self.assertIsNone(state.to_dict()['task_result'])

    def test_all_fixture_scenarios_are_real_repositories(self):
        for scenario in ['average','multi_file','syntax_error','command_failure','timeout','irrelevant_files']:
            with self.subTest(scenario=scenario):
                path=create_fixture(self.base/scenario,scenario)
                self.assertTrue((path/'.git/HEAD').is_file())
                self.assertEqual(git(path,'status','--porcelain').stdout,'')

    def test_nested_credential_file_is_masked_from_command(self):
        self.write('config/.env.local',self.secret)
        result=self.run_python('print(repr(open("config/.env.local").read()))')
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['stdout'],"''\n")
        self.assertEqual((self.target/'config/.env.local').read_text(),self.secret)

    def test_nested_git_metadata_is_masked(self):
        self.write('nested/.git/HEAD','ref: refs/heads/main\n')
        result=self.run_python('from pathlib import Path\ntry: Path("nested/.git/HEAD").write_text("bad")\nexcept OSError: print("denied")\nelse: raise AssertionError("writable")')
        self.assertTrue(result['passed'],result)
        self.assertEqual((self.target/'nested/.git/HEAD').read_text(),'ref: refs/heads/main\n')

    def test_get_changes_cannot_alter_metadata_via_config(self):
        # The local config is masked even when it asks for a different worktree.
        with (self.target/'.git/config').open('a') as stream:
            stream.write('\n[core]\nworktree = '+str(self.base)+'\n')
        result=self.tools.get_changes()
        self.assertTrue(result['clean'])

    def test_invalid_action_type_is_controlled(self):
        for name in [None,{},[],42]:
            with self.assertRaises(ToolError): self.tools.call(name)

    def test_huge_numeric_limits_fail_validation(self):
        from ai_harness.errors import ConfigurationError
        with self.assertRaises(ConfigurationError): ToolLimits(max_files=10**1000)

    def test_check_timeout_uses_global_limit(self):
        t=self.new_tools(limits=replace(ToolLimits(),command_timeout_seconds=.2),
                         checks=(CheckSpec('slow',('{python}','-c','import time; time.sleep(30)'),timeout_seconds=10),))
        result=t.run_checks()['results'][0]
        self.assertTrue(result['timed_out'])
        self.assertLess(result['duration_seconds'],3)
