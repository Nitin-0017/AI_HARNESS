import tempfile
import unittest
from pathlib import Path
from ai_harness.config import BudgetConfig, load_config
from ai_harness.state import RunState
from ai_harness.errors import ConfigurationError
from tool_support import ToolTestCase
import test_agent_loop as agent_support
from test_agent_loop import READ, CHECK, FINISH, edit


class CacheTests(ToolTestCase):
    def test_read_reuses_actual_evidence_and_invalidates_external_change(self):
        state=RunState(BudgetConfig()); tools=self.new_tools(state=state)
        first=tools.call('read_file',{'path':'calculator.py'})
        second=tools.call('read_file',{'path':'calculator.py'})
        self.assertEqual(first['content'],second['content']); self.assertTrue(second['cache_hit'])
        self.assertEqual(state.usage.read_calls,1); self.assertEqual(state.usage.cache_hits,1)
        self.write('calculator.py','changed = True\n')
        third=tools.call('read_file',{'path':'calculator.py'})
        self.assertEqual(third['content'],'changed = True\n'); self.assertEqual(state.usage.read_calls,2)
    def test_repeated_search_cached_but_new_files_invalidate(self):
        tools=self.new_tools(state=RunState(BudgetConfig()))
        tools.call('search_code',{'query':'average'})
        self.assertTrue(tools.call('search_code',{'query':'average'})['cache_hit'])
        self.write('other.py','average = 7\n')
        self.assertFalse(tools.call('search_code',{'query':'average'}).get('cache_hit',False))
    def test_patch_invalidates_cache(self):
        self.tools.call('read_file',{'path':'calculator.py'})
        self.tools.call('apply_patch',{'edits':[{'path':'calculator.py','old':'return sum(numbers) / len(numbers)','new':'return 0'}]})
        self.assertIn('return 0',self.tools.call('read_file',{'path':'calculator.py'})['content'])
    def test_cache_size_bounded(self):
        for n in range(25):
            self.write(f'f{n}.py',f'v={n}\n'); self.tools.call('read_file',{'path':f'f{n}.py'})
        self.assertLessEqual(len(self.tools._cache),16)
        self.assertLessEqual(self.tools._cache_bytes,self.tools.limits.max_output_bytes*4)


class CheckReuseTests(ToolTestCase):
    agent = agent_support.AgentLoopTests.agent
    def test_repeat_pass_does_not_spawn_identical_check(self):
        controller,model,state,_=self.agent([READ,edit(),CHECK,CHECK,FINISH])
        result=controller.run()
        self.assertEqual(result.status,'COMPLETED',result.to_dict())
        self.assertEqual(state.usage.test_executions,1)
        self.assertEqual(state.usage.avoided_test_runs,1)
        self.assertGreater(state.usage.model_seconds,0)
        self.assertGreater(state.usage.tool_seconds,0)
        self.assertGreater(state.usage.test_seconds,0)


class AliasTests(unittest.TestCase):
    def test_budget_aliases_are_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg=load_config(root,env={'HARNESS_MAX_RUNTIME_SECONDS':'25','HARNESS_MAX_TEST_RUNS':'4'})
            self.assertEqual(cfg.budgets.max_seconds,25); self.assertEqual(cfg.budgets.max_test_executions,4)
            self.assertEqual(cfg.budgets.max_runtime_seconds,25)
    def test_conflicting_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ConfigurationError):
            load_config(Path(tmp),env={'HARNESS_MAX_SECONDS':'10','HARNESS_MAX_RUNTIME_SECONDS':'25'})
