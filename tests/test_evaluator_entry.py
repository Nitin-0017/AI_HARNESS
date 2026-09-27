"""The standard launcher uses the configured real adapter, never a mock factory."""
import io
import json
from pathlib import Path
from support import FoundationTestCase
from roadmap_fixtures import cases, build
from model_support import ModelServer, json_response

class EvaluatorEntryTests(FoundationTestCase):
    def prepare(self):
        case=cases()['wrong_then_repair']
        target=build(self.base/'evaluation-target',case)
        server=ModelServer([{'body':json_response(r.requested_action,r.arguments,r.message)} for r in case.script])
        self.addCleanup(server.close)
        env={**self.env,'AI_PROVIDER':'deepseek','AI_MODEL_ID':'local-protocol-fixture',
             'AI_API_ENDPOINT':server.endpoint,'AI_REQUEST_FORMAT':'chat_completions','AI_RESPONSE_FORMAT':'chat_json',
             'AI_MAX_RETRIES':'0','HARNESS_MAX_TOTAL_TOKENS':'2000000'}
        return case,target,server,env
    def test_default_launch_runs_configured_agent_and_reports_actual_repair(self):
        case,target,server,env=self.prepare()
        code,out,err=self.cli(['--workspace',str(target),'--task',case.task],env=env)
        self.assertEqual(code,0,out+err)
        self.assertIn('VERIFIED',out);self.assertIn('FAILED',out)
        self.assertEqual(len(server.requests),len(case.script))
        reports=list(self.project.rglob('report.json'))
        self.assertEqual(len(reports),1)
        report=json.loads(reports[0].read_text())
        self.assertTrue(report['checks_failed']);self.assertTrue(report['broader_checks_passed'])
    def test_explicit_startup_preserves_no_execution(self):
        case,target,server,env=self.prepare()
        code,out,err=self.cli(['--startup','--workspace',str(target),'--task',case.task,'--json'],env=env)
        self.assertEqual(code,0,out+err);self.assertEqual(server.requests,[])
        self.assertEqual(json.loads(out)['usage']['model_calls'],0)
    def test_task_can_be_supplied_after_interactive_launch(self):
        case,target,server,env=self.prepare()
        class Terminal(io.StringIO):
            def isatty(self):return True
        code,out,err=self.cli([],env=env,stdin=Terminal(str(target)+'\n'+case.task+'\n'))
        self.assertEqual(code,0,out+err)
        self.assertIn('Target workspace path',err);self.assertIn('Task text',err)
        self.assertIn('VERIFIED',out)
    def test_startup_display_uses_public_endpoint_presence_without_exposing_url(self):
        case,target,server,env=self.prepare()
        code,out,err=self.cli(['--startup','--workspace',str(target),'--task',case.task],env=env)
        self.assertEqual(code,0,out+err)
        self.assertNotIn('not fully configured',out)
        self.assertNotIn(server.endpoint,out)
        self.assertEqual(server.requests,[])
