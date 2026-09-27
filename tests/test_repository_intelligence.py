from tool_support import ToolTestCase
import unittest
from ai_harness.repository_intelligence import RepositoryIndex
import test_agent_loop as agent_support
from test_agent_loop import READ, FINISH, edit

class IndexTests(unittest.TestCase):
    def test_symbols_imports_tests_dependencies(self):
        idx=RepositoryIndex()
        idx.structure(['app.py','constants.py','tests/test_app.py','pyproject.toml','requirements.txt'])
        idx.read('app.py','from constants import SCALE\nclass Convert:\n    def run(self): return SCALE\n')
        idx.read('tests/test_app.py','from app import Convert\ndef test_run(): assert Convert().run()\n')
        s=idx.summary()
        self.assertTrue(any(r['name']=='Convert' and r['line']==2 for r in s['symbols']))
        self.assertIn({'from':'app.py','to':'constants.py','kind':'observed_import'},s['references'])
        self.assertTrue(any(r['source']=='app.py' for r in s['test_source_relationships']))
        self.assertIn('requirements.txt',s['dependency_files'])
    def test_syntax_error_does_not_crash_index(self):
        idx=RepositoryIndex();idx.read('broken.py','def x(:')
        self.assertIsNotNone(idx.observed['broken.py']['parse_error'])
    def test_index_bounded(self):
        idx=RepositoryIndex(5);idx.structure([f'f{i}.py' for i in range(1000)])
        for i in range(30): idx.read(f'f{i}.py','def x(): pass')
        self.assertLessEqual(len(idx.files),5);self.assertLessEqual(len(idx.observed),5)
    def test_source_is_never_imported_or_executed(self):
        idx=RepositoryIndex();idx.read('bad.py','raise RuntimeError("must not execute")\ndef symbol(): pass\n')
        self.assertEqual(idx.summary()['symbols'][0]['name'],'symbol')

class IndexIntegration(ToolTestCase):
    agent = agent_support.AgentLoopTests.agent
    def test_existing_context_contains_lightweight_index(self):
        controller,model,state,_=self.agent([READ,edit(),FINISH])
        self.assertEqual(controller.run().status,'COMPLETED')
        self.assertTrue(state.repository_intelligence['test_source_relationships'])
        self.assertIn('symbols',str(state.context))
