from tool_support import ToolTestCase
import json
import tempfile
from pathlib import Path
from ai_harness.reporting import build_report, terminal_report, write_report
from ai_harness.telemetry import Redactor
import test_agent_loop as agent_support
from test_agent_loop import READ, CHECK, FINISH, edit
from ai_harness.config import BudgetConfig
from tool_support import UNIT

class ReportTests(ToolTestCase):
    agent = agent_support.AgentLoopTests.agent
    def test_report_keeps_failed_attempt_after_successful_repair(self):
        controller,model,state,_=self.agent([READ,edit(new='return 0'),CHECK,edit(old='return 0'),CHECK,FINISH])
        result=controller.run(); report=build_report(state,result.to_dict(),model.metadata(),{'unit':UNIT})
        self.assertEqual(report['status'],'VERIFIED',report)
        self.assertEqual(len(report['checks_failed']),1);self.assertEqual(len(report['checks_passed']),1)
        self.assertIsNone(report['token_usage']['total_tokens'])
        self.assertIn('FAILED',terminal_report(report))
        self.assertIn('calculator.py',report['files_changed'])
    def test_unverified_model_claim_cannot_make_report_verified(self):
        controller,model,state,_=self.agent([FINISH],budgets=BudgetConfig(max_retries=0))
        result=controller.run(); report=build_report(state,{**result.to_dict(),'task_result':'VERIFIED'},model.metadata(),{'unit':UNIT})
        self.assertFalse(report['final_state_verified']);self.assertNotEqual(report['status'],'VERIFIED')
    def test_report_files_are_redacted_and_do_not_overwrite(self):
        controller,model,state,_=self.agent([READ,edit(),FINISH])
        result=controller.run();report=build_report(state,result.to_dict(),model.metadata(),{'unit':UNIT})
        report['task']=self.secret
        with tempfile.TemporaryDirectory() as tmp:
            paths=write_report(Path(tmp),report,Redactor(self.secret))
            self.assertNotIn(self.secret,Path(paths['report.json']).read_text())
            with self.assertRaises(FileExistsError): write_report(Path(tmp),report,Redactor(self.secret))
