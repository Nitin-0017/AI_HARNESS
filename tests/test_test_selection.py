import unittest
from ai_harness.test_selection import discover_checks, select_checks
from ai_harness.tool_types import CheckSpec
from ai_harness.config import BudgetConfig
import test_agent_loop as agent_support
from test_agent_loop import READ, CHECK, FINISH, edit
from tool_support import ToolTestCase, UNIT

class DiscoveryTests(ToolTestCase):
    def test_discovers_unittest_without_importing_target(self):
        specs=discover_checks(self.tools.io)
        self.assertTrue(any(s.required and s.scope=='broad' for s in specs))
        self.assertTrue(any(s.scope=='targeted' for s in specs))
        self.assertTrue(all('{python}'==s.argv[0] for s in specs))
    def test_no_tests_is_not_fake_verification(self):
        (self.target/'tests/test_calculator.py').unlink()
        self.assertEqual(discover_checks(self.tools.io),())
    def test_relevant_selection_and_full_final(self):
        registry={'unit':CheckSpec('unit',UNIT.argv,scope='targeted',paths=('calculator.py',),required=False),
                  'all':CheckSpec('all',UNIT.argv)}
        self.assertEqual(select_checks(registry,['calculator.py']),['unit'])
        self.assertEqual(select_checks(registry,['calculator.py'],final=True),['all'])
        self.assertEqual(select_checks(registry,['pyproject.toml']),['all'])

class SelectionLoop(ToolTestCase):
    agent = agent_support.AgentLoopTests.agent
    def test_targeted_pass_cannot_hide_broad_failure(self):
        targeted=CheckSpec('target',UNIT.argv,scope='targeted',paths=('calculator.py',),required=False)
        broad=CheckSpec('broad',('{python}','-c','raise SystemExit(7)'))
        controller,_,state,_=self.agent([READ,edit(),CHECK,FINISH],checks=(targeted,broad),budgets=BudgetConfig(max_retries=0,max_total_tokens=1000000))
        result=controller.run()
        self.assertEqual(result.status,'FAILED',result.to_dict())
        self.assertEqual([r['name'] for r in state.check_history],['target','broad'])
        self.assertEqual(state.check_history[0]['exit_code'],0)
        self.assertEqual(state.check_history[1]['exit_code'],7)
