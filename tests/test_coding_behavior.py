import tempfile
import unittest
from pathlib import Path
from ai_harness.config import ModelConfig
from ai_harness.controller import Action, agent_tool_definitions
from ai_harness.model_protocols import decode_response
from ai_harness.model_types import ModelError, ModelMessage, ModelRequest
from roadmap_fixtures import cases, execute, act
from tool_support import ToolTestCase
import test_agent_loop as agent_support


class CodingTasks(unittest.TestCase):
    def check_case(self, name):
        with tempfile.TemporaryDirectory() as tmp:
            result,state,model,events=execute(Path(tmp)/'target',cases()[name])
            self.assertEqual(result.status,'COMPLETED',result.to_dict())
            self.assertTrue(result.verification['passed'])
            self.assertGreater(state.usage.test_executions,0)
            self.assertTrue(model.metadata()['is_mock'])
            self.assertIn('app.py',model.requests[0].messages[-1].content)
    def test_one_file(self): self.check_case('one_file')
    def test_empty_input(self): self.check_case('empty_input')
    def test_multi_file(self): self.check_case('multi_file')
    def test_existing_failure(self): self.check_case('existing_failure')
    def test_missing_test(self): self.check_case('missing_test')


class StructuredAction(unittest.TestCase):
    def test_action_reason_expected_outcome_normalized(self):
        import json
        cfg=ModelConfig(response_format='chat_json')
        req=ModelRequest((ModelMessage('user','Inspect'),),agent_tool_definitions())
        body={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'action':'read_file','arguments':{'path':'app.py'},'reason':'Inspect before editing',
            'expected_outcome':'Read current implementation'})}}]}
        response=decode_response(cfg,body,req)
        action=Action.from_response(response)
        self.assertEqual(action.type,'read_file'); self.assertEqual(action.reason,'Inspect before editing')
        self.assertEqual(action.expected_outcome,'Read current implementation')


class EditSafeguards(ToolTestCase):
    def policy(self):
        from ai_harness.coding_policy import CodingPolicy
        from ai_harness.context import ContextManager
        from ai_harness.config import BudgetConfig
        from ai_harness.state import RunState
        from ai_harness.inputs import validate_task
        state=RunState(BudgetConfig(), task=validate_task('Fix calculator','test',64000))
        return CodingPolicy(self.tools,ContextManager(state),state)
    def test_uninspected_patch_denied(self):
        from ai_harness.tool_types import ToolError
        with self.assertRaisesRegex(ToolError,'INSPECTION_REQUIRED'):
            self.policy().validate(Action('apply_patch',{'edits':[{'path':'calculator.py','old':'x','new':'y'}]}))
    def test_test_deletion_denied(self):
        from ai_harness.tool_types import ToolError
        with self.assertRaisesRegex(ToolError,'Deleting'):
            self.policy().validate(Action('apply_patch',{'edits':[{'path':'tests/test_calculator.py','operation':'delete','expected_sha256':'a'*64}]}))
    def test_existing_assertion_replacement_denied(self):
        from ai_harness.tool_types import ToolError
        p=self.policy(); result=self.tools.read_file('tests/test_calculator.py'); p.observe('read_file',{},result)
        with self.assertRaises(ToolError):
            p.validate(Action('apply_patch',{'edits':[{'path':result['path'],'old':'assertEqual','new':'assertNotEqual'}]}))
