import unittest
from types import SimpleNamespace

from wechat_autoreply.adapters.base import AdapterCapabilities, ScanResult
from wechat_autoreply.adapters.wechat import WechatAdapter
from wechat_autoreply.adapters.registry import create_adapter
from wechat_autoreply.config import Config
from wechat_autoreply.models import ConversationCandidate, ChatSnapshot
from wechat_autoreply.wechat import Calibration


class AdapterTests(unittest.TestCase):
    def test_changing_contact_to_group_invalidates_old_preview_evidence(self):
        cal = Calibration(verified=True, identities={'测试群': 'g'})
        adapter = WechatAdapter(SimpleNamespace(calibration=cal, bridge=None))
        before = adapter.preview_requirements(Config(allowed=['g']))
        cal.group_keys = ['g']
        self.assertNotEqual(adapter.preview_requirements(Config(allowed=['g'])), before)
    def test_wechat_wrapper_preserves_calibration_and_rows(self):
        row = ConversationCandidate('a', 'A', 0, True, False)
        driver = SimpleNamespace(scan=lambda cancel: [row], coverage={'reached_end': False},
                                 calibration=SimpleNamespace(verified=False), bridge=None)
        result = WechatAdapter(driver).scan()
        self.assertEqual(result.candidates, (row,))
        self.assertFalse(result.complete)
        self.assertEqual(result.coverage['reached_end'], False)

    def test_unverified_adapter_cannot_supply_preview_ticket(self):
        driver = SimpleNamespace(calibration=SimpleNamespace(verified=False), bridge=None)
        adapter = WechatAdapter(driver)
        self.assertEqual(adapter.preview_evidence(ScanResult((), False, {}, 's', 't')), set())

    def test_unread_dot_does_not_invent_boundary(self):
        self.assertIsNone(ChatSnapshot('a', [], False).initial_unread)

    def test_unknown_real_adapter_is_not_silently_enabled(self):
        config = Config()
        config.app_id = 'jimu'
        with self.assertRaisesRegex(ValueError, 'adapter_not_available'):
            create_adapter(config, None)
