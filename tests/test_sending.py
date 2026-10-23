import tempfile
import threading
import unittest
from pathlib import Path

from wechat_autoreply.models import ChatSnapshot, Message, messages_json
from wechat_autoreply.sending import Sender
from wechat_autoreply.store import Store


def snapshot(messages=None, muted=False, draft='', identity=True):
    return ChatSnapshot('a', messages or [Message('incoming', '你好')], muted,
                        draft, True, identity, '测试甲')


class FakeSession:
    def __init__(self, views, fail_send=False):
        self.views = iter(views)
        self.inputs, self.sends, self.returns = [], 0, 0
        self.fail_send = fail_send

    def read_chat(self, key):
        return next(self.views)

    def input_text(self, value):
        self.inputs.append(value)

    def submit(self):
        self.sends += 1
        if self.fail_send:
            raise TimeoutError('accepted then lost response')

    def return_to_list(self):
        self.returns += 1

    def clear_own_draft(self, value):
        pass


class FakeDriver:
    def __init__(self, session):
        self.session, self.transactions = session, 0

    def transaction(self, callback, cancel=None):
        self.transactions += 1
        return callback(self.session)


class SendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.context = [Message('incoming', '你好')]
        self.store.ensure_conversation('a')
        self.store.update_conversation('a', version=1, snapshot=messages_json(self.context))
        self.job = self.store.create_job('a', 1, messages_json(self.context), messages_json(self.context), generation=1)
        self.store.update_job(self.job['id'], status='generating', needs_refresh=0)
        self.store.set_draft(self.job['id'], 1, 1, '收到')
        self.job = self.store.job(self.job['id'])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def sender(self, views, **kwargs):
        session = FakeSession(views, kwargs.pop('fail_send', False))
        driver = FakeDriver(session)
        return Sender(self.store, driver, {'a'}, mode='send', generation=lambda: 1, **kwargs), session

    def test_one_send_with_checkpoint_and_verified_outgoing_message(self):
        sender, s = self.sender([snapshot(), snapshot(draft='收到'), snapshot(self.context + [Message('outgoing', '收到')])])
        result = sender.send(self.job, threading.Event())
        self.assertEqual(result.status, 'sent')
        self.assertEqual(s.sends, 1)
        self.assertEqual(s.returns, 1)
        self.assertEqual(self.store.job(self.job['id'])['status'], 'sent')

    def test_sender_leaves_new_input_for_scheduler_to_account_for(self):
        after = snapshot(self.context + [Message('outgoing','收到'),Message('incoming','新消息')])
        sender, session = self.sender([snapshot(),snapshot(draft='收到'),after])
        result = sender.send(self.job,threading.Event())
        self.assertEqual(result.status,'sent')
        self.assertEqual(result.snapshot,after)
        self.assertEqual(self.store.conversation('a')['snapshot'],messages_json(self.context))

    def test_muted_unknown_manual_draft_or_identity_never_send(self):
        for view in [snapshot(muted=True), snapshot(muted=None), snapshot(draft='手写草稿'), snapshot(identity=False)]:
            self.store.update_job(self.job['id'], status='draft_ready')
            self.store.update_conversation('a', blocked='')
            sender, s = self.sender([view])
            self.assertNotEqual(sender.send(self.job, threading.Event()).status, 'sent')
            self.assertEqual(sender.driver.transactions, 1)
            self.assertEqual(s.sends, 0)
            self.assertEqual(s.inputs, [])
            self.assertEqual(s.returns, 1)

    def test_timeout_after_send_is_uncertain_and_not_replayed(self):
        sender, s = self.sender([snapshot(), snapshot(draft='收到')], fail_send=True)
        result = sender.send(self.job, threading.Event())
        self.assertEqual(result.status, 'uncertain')
        self.assertEqual(s.sends, 1)
        self.assertEqual(sender.send(self.job, threading.Event()).status, 'skipped')
        self.assertEqual(s.sends, 1)

    def test_pause_and_preview_have_no_phone_actions(self):
        sender, s = self.sender([])
        pause = threading.Event()
        pause.set()
        self.assertEqual(sender.send(self.job, pause).status, 'skipped')
        sender.mode = 'preview'
        self.assertEqual(sender.send(self.job, threading.Event()).status, 'preview')
        self.assertEqual(sender.driver.transactions, 0)

    def test_changed_input_invalidates_draft_before_send(self):
        changed = snapshot(self.context + [Message('incoming', '再问一下')])
        sender, s = self.sender([changed])
        result = sender.send(self.job, threading.Event())
        self.assertEqual(result.status, 'stale')
        self.assertEqual(s.sends, 0)

    def test_old_matching_bubble_does_not_prove_new_send(self):
        previous = [Message('incoming', '你好'), Message('outgoing', '收到')]
        self.assertFalse(Sender._sent_evidence(previous, snapshot(previous), '收到'))

    def test_explicit_failure_bubble_stays_uncertain(self):
        from wechat_autoreply.wechat import parse_chat, Calibration
        from test_wechat import node, obs
        view = obs([node('NavigationBar','测试甲'),node('Other','测试甲,你好',rect=(0,200,440,50)),
                    node('Other','我,收到',rect=(0,250,440,50)),node('Button','发送失败'),
                    node('Other','垂直滚动条, 1页',value='100%')])
        after = parse_chat(view,Calibration(identities={'测试甲':'a'}),'a',muted=False)
        sender, session = self.sender([snapshot(),snapshot(draft='收到'),after])
        self.assertEqual(sender.send(self.job,threading.Event()).status,'uncertain')
        self.assertEqual(session.sends,1)

    def test_draft_mismatch_does_not_submit(self):
        sender, s = self.sender([snapshot(), snapshot(draft='不一样的内容')])
        self.assertEqual(sender.send(self.job, threading.Event()).status, 'skipped')
        self.assertEqual(s.sends, 0)


if __name__ == '__main__':
    unittest.main()
