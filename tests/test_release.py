import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_release.py'


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('release_check', SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_private_paths_and_binary_artifacts_are_rejected(self):
        for path in ('config.json', '.state/result.json', 'calibration.json',
                     'test.sqlite3', 'screen.jpg', 'release.zip', '.env', 'AuthKey.p8'):
            with self.subTest(path=path):
                self.assertTrue(self.module.scan(path, b'{}'))
        self.assertEqual(self.module.scan('config.example.json', b'{"allowed":[]}'), [])

    def test_reports_never_echo_sensitive_content(self):
        examples = [('/' + 'Users/private-person/work').encode(),
                    ('sk-' + 'x' * 40).encode(),
                    ('ghp_' + 'a' * 36).encode(),
                    ('-----BEGIN ' + 'PRIVATE KEY-----').encode()]
        for value in examples:
            issues = self.module.scan('example.txt', value)
            self.assertTrue(issues)
            self.assertNotIn(value.decode(), repr(issues))

    def test_public_synthetic_source_passes_and_symlink_is_rejected(self):
        self.assertEqual(self.module.scan('tests/example.py', '测试甲：你好'.encode()), [])
        self.assertTrue(self.module.scan('linked.txt', b'/tmp/private', mode='120000'))


if __name__ == '__main__':
    unittest.main()
