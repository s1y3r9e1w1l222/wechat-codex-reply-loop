import json
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_autoreply.control import ControlServer, send_command
from wechat_autoreply.cli import load_calibration, main


class ControlTests(unittest.TestCase):
    def test_send_entry_accepts_allowed_group_without_avatar_or_mute_probes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cal = root / 'calibration.json'
            cal.write_text(json.dumps({'verified': True, 'muted_marker_verified': True,
                                      'list_region_complete': True, 'list_bottom': 868,
                                      'identities': {'测试群': 'group'}, 'group_keys': ['group']}))
            config = root / 'config.json'
            config.write_text(json.dumps({'state_dir': d, 'calibration_path': str(cal), 'allowed': ['group']}))
            output = io.StringIO()
            with patch('wechat_autoreply.cli.PhoneBridge', side_effect=RuntimeError('phone_entry_reached')):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(['--config', str(config), 'run', '--send']), 1)
            self.assertEqual(json.loads(output.getvalue())['error'], 'phone_entry_reached')
    def test_long_private_state_directory_supports_real_control_commands(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / ('state-' + 'x' * 100)
            root.mkdir(mode=0o700)
            server = ControlServer(root, lambda request: {'ok': True, 'command': request['command']})
            server.start()
            try:
                self.assertEqual(send_command(root, {'command': 'status'}), {'ok': True, 'command': 'status'})
                self.assertEqual(server.path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(server.path.parent.stat().st_mode & 0o777, 0o700)
            finally:
                server.close()

    def test_failed_duplicate_server_cannot_remove_live_control_socket(self):
        with tempfile.TemporaryDirectory() as d:
            server = ControlServer(d, lambda request: {'ok': True})
            server.start()
            other = ControlServer(d, lambda request: {'ok': False})
            try:
                with self.assertRaisesRegex(RuntimeError, '已在运行'):
                    other.start()
                other.close()
                self.assertTrue(send_command(d, {'command': 'status'})['ok'])
            finally:
                server.close()

    def test_explicit_group_calibration_roundtrips_and_rejects_unknown_group_key(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'calibration.json'
            data = {'identities': {'测试群': 'group'}, 'group_keys': ['group']}
            path.write_text(json.dumps(data))
            self.assertEqual(load_calibration(path).group_keys, ['group'])
            data['group_keys'] = ['missing']
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_calibration(path)
    def test_calibrate_does_not_overwrite_existing_contact_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = root / 'config.json'
            cal = root / 'calibration.json'
            cal.write_text('{"verified":true}')
            config.write_text(json.dumps({'state_dir':d, 'calibration_path':str(cal)}))
            with patch('wechat_autoreply.cli.PhoneBridge') as bridge:
                self.assertEqual(main(['--config',str(config),'calibrate']),1)
                bridge.assert_not_called()
            self.assertEqual(cal.read_text(),'{"verified":true}')

    def test_private_socket_and_request_contract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            server = ControlServer(root, lambda request: {'ok':True, 'command':request['command']})
            server.start()
            try:
                self.assertEqual(os.stat(root / 'control.sock').st_mode & 0o777,0o600)
                self.assertEqual(send_command(root, {'command':'status'})['command'],'status')
                with self.assertRaises(ValueError):
                    send_command(root, {'command':'status','padding':'x'*5000})
            finally:
                server.close()


if __name__ == '__main__':
    unittest.main()
