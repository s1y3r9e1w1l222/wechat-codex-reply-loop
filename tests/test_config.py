import tempfile
import unittest
from pathlib import Path

from wechat_autoreply.config import load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_preview_and_empty_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.json'
            p.write_text('{}')
            c = load_config(p)
            self.assertEqual(c.mode, 'preview')
            self.assertEqual(c.allowed, [])
            self.assertEqual(c.poll_seconds, 2)

    def test_unknown_keys_and_out_of_bounds_values_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.json'
            for content in ['{"oops":1}', '{"poll_seconds":0}', '{"ai_workers":4}', '{"mode":"send"}', '{"poll_seconds":true}']:
                p.write_text(content)
                with self.assertRaises(ValueError, msg=content):
                    load_config(p)


if __name__ == '__main__':
    unittest.main()
