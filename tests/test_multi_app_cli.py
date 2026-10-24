import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_autoreply.cli import main


class MultiAppCliTests(unittest.TestCase):
    def config(self, directory, app_id='jimu'):
        p=Path(directory)/'config.json'
        p.write_text(json.dumps({'app_id':app_id,'state_dir':str(Path(directory)/'state')}))
        return p

    def test_multi_app_demo_runs_without_device_or_credentials(self):
        process=subprocess.run(['python3','-m','wechat_autoreply','demo','--scenario','multi-app'],
                               text=True,capture_output=True,timeout=10)
        self.assertEqual(process.returncode,0,process.stderr)
        result=json.loads(process.stdout)
        self.assertTrue(result['ok'])
        self.assertEqual(result['unanswered_sequences'],[1,2,3])
        self.assertTrue(result['reply_cancels_followups'])

    def test_scope_empty_and_clear_send_distinct_commands(self):
        with tempfile.TemporaryDirectory() as d:
            config=self.config(d)
            requests=[]
            def send(root,request):
                requests.append(request)
                return {'ok':True}
            with patch('wechat_autoreply.cli.send_command',side_effect=send), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--config',str(config),'scope','set','--date','2026-10-24']),0)
                self.assertEqual(main(['--config',str(config),'scope','clear','--date','2026-10-24']),0)
            self.assertEqual(requests,[{'command':'scope_set','date':'2026-10-24','selected':[],'expected_app_id':'jimu'},
                                       {'command':'scope_delete','date':'2026-10-24','expected_app_id':'jimu'}])

    def test_new_app_real_run_refuses_before_phone_construction(self):
        with tempfile.TemporaryDirectory() as d:
            config=self.config(d)
            output=io.StringIO()
            with patch('wechat_autoreply.cli.PhoneBridge',side_effect=AssertionError('must not touch phone')), contextlib.redirect_stdout(output):
                code=main(['--config',str(config),'run','--send'])
            self.assertEqual(code,1)
            self.assertEqual(json.loads(output.getvalue())['error'],'adapter_not_available')

    def test_soul_real_run_refuses_before_phone_construction(self):
        with tempfile.TemporaryDirectory() as d:
            config=self.config(d, 'soul')
            output=io.StringIO()
            with patch('wechat_autoreply.cli.PhoneBridge',side_effect=AssertionError('must not touch phone')), contextlib.redirect_stdout(output):
                code=main(['--config',str(config),'run','--send'])
            self.assertEqual(code,1)
            self.assertEqual(json.loads(output.getvalue())['error'],'adapter_not_available')
