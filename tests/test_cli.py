import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_autoreply.control import ControlServer, send_command
from wechat_autoreply.cli import main


class ControlTests(unittest.TestCase):
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
