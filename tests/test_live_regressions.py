"""Synthetic replay of the layouts and workflow observed during device testing."""
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from wechat_autoreply.config import Config
from wechat_autoreply.adapters.wechat import WechatAdapter
from wechat_autoreply.models import Message
from wechat_autoreply.navigation import WechatDriver, WechatSession
from wechat_autoreply.phone import PhoneError
from wechat_autoreply.provider import FakeProvider
from wechat_autoreply.service import Service
from wechat_autoreply.store import Store
from wechat_autoreply.wechat import Calibration, parse_chat


def node(kind, label='', rect=(0, 200, 440, 58), **kw):
    return dict(type=kind, label=label, rect=list(rect), **kw)


def view(nodes):
    return dict(app='com.tencent.xin', viewport={'width': 440, 'height': 956},
                nodes=nodes, truncated=False)


class ChatDevice:
    def __init__(self, store=None, *, group=False):
        self.page, self.keyboard, self.draft = 'list', False, ''
        self.calls, self.opened, self.submits = [], [], 0
        self.store = store
        self.group = group
        self.label = '测试群' if group else '测试甲'
        self.messages = [('成员甲' if group else '测试甲', '你好')]
        self.unread = True

    def observe(self):
        if self.page == 'list':
            return view([node('NavigationBar', '微信'),
                         node('SearchField', '搜索', (0, 105, 440, 40)),
                         node('Cell', self.label + ',' + ('1条未读,' if self.unread else '') + '你好,20:30'),
                         node('Cell', '已读乙,旧消息,20:00', (0, 273, 440, 73)),
                         node('Cell', '免打扰群,未读,[3条]消息,19:00,消息免打扰', (0, 346, 440, 73))])
        nodes = [node('NavigationBar', self.label + (',(4)' if self.group else ''), (0, 56, 440, 45)),
                 node('Button', '返回', (16, 56, 12, 45)),
                 node('Button', '语音', (8, 607 if self.keyboard else 874, 32, 36)),
                 node('TextView', '', (54, 612 if self.keyboard else 879, 295, 30), value=self.draft),
                 node('Other', '垂直滚动条, 2页', (407, 100, 30, 499), value='100%')]
        nodes += [node('Other', who + ',' + body, (0, 180 + i * 58, 440, 58))
                  for i, (who, body) in enumerate(self.messages)]
        if self.keyboard:
            nodes.append(node('Button', 'send', (330, 830, 110, 56), name='Send', enabled=bool(self.draft)))
        return view(nodes)

    def call(self, name, args):
        self.calls.append((name, args))
        if name == 'pua_tap':
            if args.get('selector'):
                if args['selector'].get('name') != 'Send' and args['selector'].get('label') not in ('发送', 'send'):
                    raise AssertionError('runtime must not open avatar or profile')
                self.submits += 1
                self.messages.append(('我', self.draft))
                self.draft = ''
            elif self.page == 'list':
                if self.store:
                    job = self.store.head_job()
                    assert job and job['conversation'] == 'a', 'unread must be durable before opening'
                    assert self.store.conversation('a')['unread_count'] == 1
                self.opened.append('a')
                self.page, self.unread = 'chat', False
            elif args.get('x', 100) < 50:
                self.page, self.keyboard = 'list', False
            else:
                self.keyboard = True
        elif name == 'pua_type_text':
            self.draft = args['text']
            return {'exact_readback': True}
        else:
            raise AssertionError('unexpected device action: ' + name)
        return {}

    def focused_value(self):
        return self.draft


def driver_for(device):
    cal = Calibration(list_bottom=868, muted_marker_verified=True, list_region_complete=True,
                      verified=True, identities={getattr(device, 'label', '测试甲'): 'a', '已读乙': 'b', '免打扰群': 'g'},
                      group_keys=['a'] if getattr(device, 'group', False) else [],
                      profile_signatures={'a': 'old-profile-signature'},
                      identity_probes={'a': {'steps': [{'label': '更多'}, {'label': '测试甲'}],
                                             'back_steps': [{'label': '返回'}, {'label': '返回'}]}})
    bridge = SimpleNamespace(transaction=lambda callback, cancel=None, **kw: callback(device))
    return WechatDriver(bridge, cal, max_pages=1)


class ImmediatePool:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class LiveRegressionTests(unittest.TestCase):
    def test_pending_unread_is_saved_before_open_and_read_or_muted_rows_never_open(self):
        self.complete_loop(group=False)

    def test_allowed_group_runs_through_real_adapter_scheduler_and_sender(self):
        self.complete_loop(group=True)

    def test_group_name_starting_with_wechat_is_not_the_list_page(self):
        self.complete_loop(group=True, label='微信群')

    def test_outgoing_group_text_with_media_words_is_confirmed_as_text(self):
        self.complete_loop(group=True, reply='收到,图片,稍后看')

    def complete_loop(self, *, group, label=None, reply=None):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            try:
                device = ChatDevice(store, group=group)
                if label:
                    device.label = label
                driver = WechatAdapter(driver_for(device))
                config = Config(allowed=['a', 'b', 'g'], quiet_seconds=0)
                class Provider(FakeProvider):
                    def generate(self, *args, **kwargs):
                        return reply if reply is not None else super().generate(*args, **kwargs)
                provider = Provider()
                preview = Service(config, store, driver, provider, executor=ImmediatePool())
                preview.tick()
                preview.tick()
                service = Service(config, store, driver, provider, executor=ImmediatePool(), mode='send')
                for _ in range(3):
                    service.tick()
                self.assertEqual(service.state, 'monitoring', service.last_error)
                self.assertEqual([job['status'] for job in store.all_jobs()], ['sent'])
                self.assertEqual(set(device.opened), {'a'})
                self.assertEqual(device.submits, 1)
                self.assertEqual(device.page, 'list')
                self.assertEqual(device.draft, '')
            finally:
                store.close()

    def test_repeated_chat_reads_stay_in_chat_without_profile_navigation(self):
        device = ChatDevice()
        driver = driver_for(device)
        def read_three_times(session):
            for _ in range(3):
                snap = session.read_chat('a')
                self.assertTrue(snap.identity_verified)
                self.assertIs(snap.muted, False)
        driver.transaction(read_three_times)
        self.assertEqual(device.opened, ['a'])
        self.assertFalse(any(args.get('selector') for _, args in device.calls))

    def test_group_title_and_speakers_preserve_context_and_message_kinds(self):
        cal = Calibration(identities={'测试群': 'group'}, verified=True)
        cal.group_keys = ['group']
        current = view([node('NavigationBar', '测试群,(4)'),
                        node('Other', '成员甲,你好'),
                        node('Other', '我,收到', (0, 258, 440, 58)),
                        node('Other', '成员乙,图片,', (0, 316, 440, 150)),
                        node('Other', '垂直滚动条, 2页', (407, 100, 30, 600), value='100%')])
        snap = parse_chat(current, cal, 'group', muted=False)
        self.assertTrue(snap.identity_verified)
        self.assertEqual([(m.direction, m.text, m.kind) for m in snap.messages],
                         [('incoming', '成员甲：你好', 'text'), ('outgoing', '收到', 'text'),
                          ('incoming', '成员乙：图片,', 'image')])
        cal.group_keys = []
        self.assertFalse(parse_chat(current, cal, 'group').identity_verified)

    def test_native_english_send_is_selected_semantically_once(self):
        device = ChatDevice()
        device.page, device.keyboard, device.draft = 'chat', True, '收到'
        WechatSession(driver_for(device), device).submit()
        self.assertEqual(device.submits, 1)
        self.assertEqual(device.messages[-1], ('我', '收到'))
        self.assertEqual(device.calls[-1][1]['selector'], {'type': 'Button', 'name': 'Send', 'label': 'send'})

    def test_third_party_keyboard_can_type_without_native_send_control(self):
        device = ChatDevice()
        device.observe = lambda: view([node('Button', '发送', (345, 239, 95, 53))])
        WechatSession(driver_for(device), device).input_text('完整回复')
        self.assertEqual(device.draft, '完整回复')

    def test_third_party_relative_send_button_uses_element_not_coordinates(self):
        device = ChatDevice()
        device.draft = '收到'
        device.observe = lambda: view([node('Button', '发送', (345, 239, 95, 53))])
        WechatSession(driver_for(device), device).submit()
        self.assertEqual(device.submits, 1)
        self.assertEqual(device.messages[-1], ('我', '收到'))
        self.assertEqual(device.calls[-1][1]['selector'], {'type': 'Button', 'label': '发送'})

    def test_name_only_send_uses_only_observed_attributes(self):
        device = ChatDevice()
        device.draft = '收到'
        device.observe = lambda: view([{'type': 'Button', 'name': 'Send', 'rect': [330, 830, 110, 56]}])
        WechatSession(driver_for(device), device).submit()
        self.assertEqual(device.calls[-1][1]['selector'], {'type': 'Button', 'name': 'Send'})
        self.assertEqual(device.submits, 1)

    def test_comma_in_group_nickname_cannot_hide_nontext_kind(self):
        from wechat_autoreply.scheduler import Scheduler
        from wechat_autoreply.models import ConversationCandidate
        cal = Calibration(verified=True, identities={'测试群': 'g'}, group_keys=['g'])
        for marker in ('图片', '语音', '视频', '文件', '链接'):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as tmp:
                snap = parse_chat(view([node('NavigationBar', '测试群,(4)'),
                                       node('Other', '成员,甲,' + marker + ','),
                                       node('Other', '垂直滚动条, 2页', (407, 100, 30, 600), value='100%')]),
                                  cal, 'g', muted=False)
                store = Store(Path(tmp) / 'state.sqlite3')
                try:
                    scheduler = Scheduler(store, ['g'])
                    scheduler.enqueue_scan([ConversationCandidate('g', '测试群', 200, True, False,
                                                                   identity_verified=True, unread_count=1)])
                    scheduler.observe_chat(snap)
                    self.assertEqual(store.active_jobs(), [])
                    self.assertEqual(store.conversation('g')['blocked'], 'nontext_input')
                finally:
                    store.close()

    def test_fresh_recovery_driver_scans_for_cross_page_duplicates_before_opening(self):
        class DuplicatePages(ChatDevice):
            offset = 0
            def observe(self):
                if self.page != 'list':
                    return super().observe()
                labels = ['测试甲', '已读乙'] if self.offset == 0 else ['已读乙', '测试甲']
                nodes = [node('NavigationBar', '微信')]
                if not self.offset:
                    nodes.append(node('SearchField', '搜索', (0, 105, 440, 40)))
                nodes += [node('Cell', name + ',旧消息,20:00', (0, 200 + i * 73, 440, 73))
                          for i, name in enumerate(labels)]
                return view(nodes)
            def call(self, name, args):
                if name == 'pua_swipe':
                    self.calls.append((name, args))
                    self.offset = 0 if args['direction'] == 'down' else 1
                    return {}
                return super().call(name, args)
        for start_page in ('list', 'chat'):
            with self.subTest(start_page=start_page):
                device = DuplicatePages()
                device.page, device.draft = start_page, '恢复前草稿'
                driver = driver_for(device)
                driver.max_pages = 2
                with self.assertRaisesRegex(PhoneError, 'duplicate_contact_identity'):
                    driver.transaction(lambda session: session.read_chat('a'))
                self.assertEqual(device.opened, [])
                self.assertEqual(device.draft, '恢复前草稿')

    def test_return_from_known_chat_through_recent_search_page(self):
        pages = iter([view([node('NavigationBar', '测试甲'), node('Button', '返回', (16, 56, 12, 45))]),
                      view([node('TextView', '', (72, 80, 312, 22)), node('StaticText', '最常使用'),
                            node('Button', '返回', (16, 70, 30, 40))]),
                      view([node('NavigationBar', '微信')])])
        device = SimpleNamespace(observe=lambda: next(pages), call=lambda *args: {})
        WechatSession(driver_for(device), device).return_to_list()

    def test_next_queued_chat_above_current_list_position_is_reachable(self):
        class ScrolledDevice(ChatDevice):
            offset = 1
            def observe(self):
                if self.page == 'list' and self.offset:
                    return view([node('NavigationBar', '微信'),
                                 node('Cell', '免打扰群,未读,[3条]消息,19:00,消息免打扰')])
                return super().observe()
            def call(self, name, args):
                if name == 'pua_swipe':
                    self.calls.append((name, args))
                    self.offset = 0 if args['direction'] == 'down' else 1
                    return {}
                return super().call(name, args)
        device = ScrolledDevice()
        driver = driver_for(device)
        driver.max_pages = 2
        driver.scan()
        device.offset = 1
        self.assertTrue(driver.read_chat('a').identity_verified)
        self.assertEqual(device.opened, ['a'])


if __name__ == '__main__':
    unittest.main()
