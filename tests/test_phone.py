import tempfile
import unittest
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from wechat_autoreply.phone import PhoneBridge, PhoneError


class FakeRuntime:
    def __init__(self, root):
        self.state_dir = Path(root)
        self.base_url = 'http://127.0.0.1:18100'
        self.client = SimpleNamespace(session_id=None)
        self.phone = SimpleNamespace(snapshots={})
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        if name == 'pua_setup':
            return {'configured': True, 'service': {'ready': True}}
        if name == 'pua_ready':
            return {'ready': True, 'observation': {'app': 'com.tencent.xin'}}
        return {'ok': True}


class PhoneTests(unittest.TestCase):
    def test_critical_transaction_excludes_other_process_and_checks_cancel(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = FakeRuntime(d)
            runtime._call = runtime.call
            bridge = PhoneBridge(runtime=runtime)
            def body(device):
                script = ('import fcntl,sys; f=open(sys.argv[1],"a"); '
                          'fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)')
                p = subprocess.run([sys.executable, '-c', script, str(Path(d) / 'operation.lock')], capture_output=True)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(b'BlockingIOError', p.stderr)
                return 'owned'
            self.assertEqual(bridge.transaction(body), 'owned')
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(PhoneError):
                bridge.transaction(lambda dev: dev.call('pua_observe', {}), cancelled)
            self.assertEqual(runtime.calls, [])

    def test_import_and_construct_do_not_operate_phone(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = FakeRuntime(d)
            bridge = PhoneBridge(runtime=runtime)
            self.assertEqual(runtime.calls, [])
            bridge.ready()
            self.assertEqual([n for n, a in runtime.calls], ['pua_setup', 'pua_ready'])

    def test_unknown_plugin_version_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'server').mkdir()
            (root / 'server' / 'iphone_use.py').write_text('VERSION="9.9.9"')
            with self.assertRaises(PhoneError):
                PhoneBridge(plugin_path=root)

    def test_truncated_observation_requires_complete_same_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = FakeRuntime(d)
            runtime.phone.snapshots['x'] = {'nodes': [dict(type='Button', label='a', rect={'x':0,'y':0,'width':1,'height':1})]}
            bridge = PhoneBridge(runtime=runtime)
            raw = {'observation_id': 'x', 'total_nodes': 2, 'truncated': True}
            self.assertTrue(bridge.complete_observation(raw)['truncated'])


if __name__ == '__main__':
    unittest.main()
