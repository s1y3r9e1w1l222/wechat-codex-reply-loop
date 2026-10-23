import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wechat_autoreply.navigation import WechatSession, WechatDriver
from wechat_autoreply.models import ConversationCandidate
from wechat_autoreply.wechat import Calibration
from wechat_autoreply.phone import PhoneError


class Device:
    def __init__(self, results):
        self.results, self.calls = iter(results), []
    def call(self, name, args):
        self.calls.append((name, args))
        return next(self.results)


class NavigationTests(unittest.TestCase):
    def scan_driver(self, reordered=False):
        class Pages:
            page = 0
            changed = False
            def call(self, name, args):
                if args['direction'] == 'up':
                    self.page = 1
                else:
                    self.page = 0
                    self.changed = reordered
            def observe(self):
                keys = (['c','a','b'] if self.changed else ['a','c','b']) if self.page == 0 else ['b','d','e']
                return {'nodes':[{'type':'NavigationBar','label':'微信'}] +
                        ([{'type':'SearchField','rect':[0,100,400,40]}] if self.page == 0 else []),
                        'rows':[ConversationCandidate(k,k,float(i),True,False,k,True,k,1)
                                for i,k in enumerate(keys)]}
        device = Pages()
        bridge = SimpleNamespace(transaction=lambda cb,cancel=None:cb(device))
        return WechatDriver(bridge,Calibration(list_bottom=868),max_pages=2)

    def test_stable_two_page_scan_preserves_order(self):
        driver = self.scan_driver()
        with patch('wechat_autoreply.navigation.parse_list',side_effect=lambda view,cal:view['rows']):
            rows = driver.scan()
        self.assertEqual([r.key for r in rows],['a','c','b','d','e'])
        self.assertEqual(driver.coverage.get('stable_passes'),2)

    def test_page_reordering_with_valid_overlap_is_discarded(self):
        driver = self.scan_driver(reordered=True)
        with patch('wechat_autoreply.navigation.parse_list',side_effect=lambda view,cal:view['rows']):
            with self.assertRaisesRegex(PhoneError,'list_changed_between_passes'):
                driver.scan()

    def test_known_keyboard_send_button_disambiguates_two_send_controls(self):
        device = Device([{}])
        device.observe = lambda: {'nodes': [
            {'type':'Button','label':'发送','rect':[388,562,40,36]},
            {'type':'Button','label':'发送','name':'Send','rect':[330,830,110,56]}]}
        session = WechatSession(SimpleNamespace(), device)
        session.submit()
        self.assertEqual(device.calls[0][1], {'x':385.0,'y':858.0,'observe':'tree'})

    def test_input_continuation_does_not_replay_text(self):
        device = Device([{'input_complete': False, 'continue_token': 'token'},
                         {'input_complete': True, 'exact_readback': True}])
        session = WechatSession(SimpleNamespace(), device)
        session.input_text('完整回复')
        self.assertEqual(device.calls[0][1]['text'], '完整回复')
        self.assertFalse(device.calls[0][1]['submit'])
        self.assertEqual(device.calls[1][1], {'continue_token': 'token'})

    def test_unverified_input_is_not_accepted(self):
        session = WechatSession(SimpleNamespace(), Device([{'exact_readback': False}]))
        with self.assertRaises(PhoneError):
            session.input_text('完整回复')


if __name__ == '__main__':
    unittest.main()
