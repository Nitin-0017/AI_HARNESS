"""Ten clean repositories exercised through the actual agent and sandbox tools."""
import tempfile
import unittest
from pathlib import Path
from roadmap_fixtures import cases, execute
from ai_harness.reporting import build_report


class TaskMatrixTests(unittest.TestCase):
    def test_all_ten_clean_tasks(self):
        tasks=cases()
        self.assertGreaterEqual(len(tasks),10)
        for name,case in tasks.items():
            with self.subTest(task=name), tempfile.TemporaryDirectory() as tmp:
                result,state,model,events=execute(Path(tmp)/'target',case)
                self.assertEqual(result.status,case.expected,result.to_dict())
                report=build_report(state,result.to_dict(),model.metadata(),{c.name:c for c in case.checks})
                self.assertEqual(len(result.steps),len(model.requests))
                self.assertTrue(events)
                if case.expected=='COMPLETED':
                    self.assertTrue(report['final_state_verified'],report)
                    self.assertTrue(any(r['assessed_passed'] for r in report['checks_run']))
                else:self.assertFalse(report['final_state_verified'])
                if name in {'syntax_recovery','wrong_then_repair'}:
                    self.assertTrue(report['checks_failed']); self.assertTrue(report['checks_passed'])
                if name=='large_irrelevant':
                    self.assertNotIn('UNRELATED_LARGE_MARKER',''.join(m.content for r in model.requests for m in r.messages))
