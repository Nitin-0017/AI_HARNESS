from tool_support import ToolTestCase
from ai_harness.config import BudgetConfig
import test_agent_loop as agent_support
from test_agent_loop import READ, CHECK, FINISH, edit, OLD, FIXED

class RecoveryTests(ToolTestCase):
    agent = agent_support.AgentLoopTests.agent
    def test_first_repair_succeeds_and_context_is_compact(self):
        controller,model,state,_=self.agent([READ,edit(new='return 0'),CHECK,edit(old='return 0'),CHECK,FINISH])
        result=controller.run();self.assertEqual(result.status,'COMPLETED',result.to_dict())
        self.assertIn('failing_checks',state.recovery);self.assertIn('remaining_budget',state.recovery)
        self.assertIn('calculator.py',state.recovery['changed_code'])
        self.assertTrue(state.recovery['failure_fingerprint'])
    def test_second_repair_succeeds(self):
        controller,_,state,_=self.agent([READ,edit(new='return 0'),CHECK,
            edit(old='return 0',new='return 1'),CHECK,edit(old='return 1'),CHECK,FINISH])
        result=controller.run();self.assertEqual(result.status,'COMPLETED',result.to_dict())
        self.assertEqual(state.usage.test_executions,3)
    def test_identical_failed_checks_do_not_execute_again(self):
        controller,_,state,_=self.agent([CHECK,CHECK,CHECK,CHECK])
        result=controller.run();self.assertEqual(result.status,'FAILED',result.to_dict())
        self.assertEqual(state.usage.test_executions,1)
        self.assertGreater(state.usage.recovery_attempts,1)
    def test_budget_exhaustion_during_recovery(self):
        controller,_,state,_=self.agent([CHECK,edit(),CHECK],budgets=BudgetConfig(max_model_calls=1))
        self.assertEqual(controller.run().status,'BUDGET_EXHAUSTED')
        self.assertNotEqual(state.task_result,'VERIFIED')
