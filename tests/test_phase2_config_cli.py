from dataclasses import replace
import io
import json
from pathlib import Path
import secrets
import subprocess
import sys
from unittest import mock

from ai_harness.cli import main
from ai_harness.config import load_config
from ai_harness.errors import ConfigurationError
from ai_harness.tool_types import CheckSpec, ToolLimits
from fixture_repositories import create_fixture
from support import FoundationTestCase


class ToolConfigurationTests(FoundationTestCase):
    def test_typed_tool_limits_from_toml(self):
        self.config_file('[tools]\nmax_file_bytes=512\n[budgets]\ncommand_timeout_seconds=4.0\n')
        cfg=load_config(self.project,env={})
        self.assertEqual(cfg.tools.max_file_bytes,512)
        self.assertEqual(cfg.tools.command_timeout_seconds,4)

    def test_check_registry_from_toml(self):
        self.config_file('[[checks]]\nname="unit"\nargv=["{python}","-m","unittest"]\ntimeout_seconds=3\n')
        cfg=load_config(self.project,env={})
        self.assertEqual(cfg.checks[0].name,'unit')
        self.assertIsInstance(cfg.checks[0].argv,tuple)

    def test_unknown_tool_field_rejected(self):
        self.config_file('[tools]\nshell=true\n')
        with self.assertRaises(ConfigurationError): load_config(self.project,env={})

    def test_unknown_check_field_rejected(self):
        self.config_file('[[checks]]\nname="x"\nargv=["{python}"]\nshell=true\n')
        with self.assertRaises(ConfigurationError): load_config(self.project,env={})

    def test_duplicate_check_names_rejected(self):
        self.config_file('[[checks]]\nname="x"\nargv=["{python}"]\n[[checks]]\nname="x"\nargv=["{python}"]\n')
        with self.assertRaises(ConfigurationError): load_config(self.project,env={})

    def test_target_cannot_supply_trusted_harness_config(self):
        cfg=self.target/'settings.toml'; cfg.write_text('[workspace]\npath="."\n')
        with self.assertRaises(ConfigurationError): load_config(self.project,config_path=cfg,env={})

    def test_invalid_limits_rejected(self):
        for value in [0,-1,True,float('nan'),'lots']:
            with self.subTest(value=value),self.assertRaises(ConfigurationError):
                ToolLimits(max_file_bytes=value)

    def test_command_not_string_shell(self):
        with self.assertRaises(ConfigurationError): CheckSpec('bad','python -c whatever')

    def test_check_executable_must_be_trusted_absolute_path(self):
        for executable in ['python3','./test.sh','/tmp/arbitrary','/usr/bin/../tmp/bad']:
            with self.assertRaises(ConfigurationError): CheckSpec('bad',(executable,))

    def test_config_credentials_still_forbidden(self):
        self.config_file('[tools]\nAI_API_KEY="not-a-credential"\n')
        with self.assertRaises(ConfigurationError): load_config(self.project,env={})


class ToolCliTests(FoundationTestCase):
    def args(self,*extra):
        return ['--workspace',str(self.target),'--task','Inspect the repository',
                '--non-interactive','--output-dir',str(self.project/'logs'),*extra]

    def test_cli_executes_real_read_and_writes_evidence(self):
        code,out,err=self.cli(self.args('--tool','read_file','--tool-args','{"path":"main.py"}'))
        self.assertEqual(code,0,err)
        state=json.loads(out)
        self.assertIn('def add',state['tool_result']['content'])
        self.assertEqual(state['usage']['tool_calls'],1)
        self.assertEqual(state['usage']['model_calls'],0)
        self.assertIsNone(state['task_result'])
        self.assertTrue(Path(state['artifacts']['tool_result_file']).is_file())

    def test_cli_patch_updates_target(self):
        data={'edits':[{'path':'main.py','old':'a + b','new':'a - b'}]}
        code,out,err=self.cli(self.args('--tool','apply_patch','--tool-args',json.dumps(data)))
        self.assertEqual(code,0,err)
        self.assertIn('a - b',(self.target/'main.py').read_text())
        self.assertTrue(json.loads(out)['tool_result']['applied'])

    def test_cli_invalid_path_fails_without_traceback(self):
        code,out,err=self.cli(self.args('--tool','read_file','--tool-args','{"path":"../private"}'))
        self.assertEqual(code,2)
        self.assertNotIn('Traceback',err)
        statefile=next((self.project/'logs').glob('*/run_state.json'))
        self.assertEqual(json.loads(statefile.read_text())['usage']['failures'],1)

    def test_cli_bad_json_is_controlled(self):
        code,out,err=self.cli(self.args('--tool','list_files','--tool-args','{broken'))
        self.assertEqual(code,2)
        self.assertNotIn('Traceback',err)

    def test_cli_requires_workspace_for_tool(self):
        code,out,err=self.cli(['--tool','list_files','--output-dir',str(self.project/'logs')])
        self.assertEqual(code,2)
        self.assertIn('workspace',err)

    def test_cli_no_implicit_execution(self):
        with mock.patch('subprocess.Popen',side_effect=AssertionError('must not execute')):
            code,out,err=self.cli(self.args())
        self.assertEqual(code,0,err)

    def test_cli_run_checks_returns_nonzero_on_real_failure(self):
        self.config_file('[[checks]]\nname="failure"\nargv=["{python}","-c","raise SystemExit(5)"]\n')
        code,out,err=self.cli(self.args('--tool','run_checks'))
        self.assertEqual(code,1,err)
        result=json.loads(out)
        self.assertEqual(result['tool_result']['results'][0]['exit_code'],5)
        self.assertEqual(result['usage']['test_executions'],1)
        self.assertEqual(result['verification_status'],'NOT_ASSESSED')

    def test_cli_api_key_still_required(self):
        code,out,err=self.cli(self.args('--tool','list_files'),env={})
        self.assertEqual(code,2)
        self.assertIn('AI_API_KEY',err)
