import tempfile
import unittest
from pathlib import Path

from wechat_autoreply.models import ChatSnapshot, ConversationCandidate, Message
from wechat_autoreply.scheduler import Scheduler, align_messages
from wechat_autoreply.store import Store


def candidate(key, y, preview='hello', muted=False, unread=True):
    return ConversationCandidate(key, key, y, unread, muted, key, True, preview, 1)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'state/db.sqlite3')
        self.scheduler = Scheduler(self.store, ['a', 'b', 'c'])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_bottom_first_and_repeat_scan_does_not_reorder(self):
        rows = [candidate('a', 200), candidate('b', 273), candidate('c', 346)]
        self.scheduler.enqueue_scan(rows)
        self.scheduler.enqueue_scan(list(reversed(rows)))
        self.assertEqual([j['conversation'] for j in self.store.active_jobs()], ['c', 'b', 'a'])

    def test_changed_input_moves_to_tail_but_read_badge_does_not_delete(self):
        self.scheduler.enqueue_scan([candidate('a', 200), candidate('b', 273)])
        self.scheduler.enqueue_scan([candidate('b', 200, 'new'), candidate('a', 273, unread=False)])
        self.assertEqual([j['conversation'] for j in self.store.active_jobs()], ['a', 'b'])

    def test_identical_unread_reappearing_after_read_creates_new_job(self):
        self.scheduler.enqueue_scan([candidate('a', 200, '好')])
        old = self.store.head_job()
        self.store.update_job(old['id'], status='sent')
        self.scheduler.enqueue_scan([candidate('a', 200, '好', unread=False)])
        self.scheduler.enqueue_scan([candidate('a', 200, '好')])
        self.assertEqual(len(self.store.active_jobs()), 1)
        self.assertGreater(self.store.head_job()['id'], old['id'])

    def test_sent_checkpoint_rearms_same_unread_before_next_observation(self):
        self.scheduler.enqueue_scan([candidate('a',200,'好')])
        old = self.store.head_job()
        attempt = self.store.mark_sending(old['id'],'reply',[])
        self.store.finish_attempt(attempt,'sent')
        # Simulate interruption before Scheduler sees the post-send snapshot.
        self.scheduler.enqueue_scan([candidate('a',200,'好')])
        self.assertEqual(len(self.store.active_jobs()),1)
        self.assertGreater(self.store.head_job()['id'],old['id'])

    def test_input_before_outgoing_is_visible_ambiguity(self):
        self.scheduler.enqueue_scan([candidate('a',200)])
        before = [Message('incoming','old')]
        self.scheduler.observe_chat(ChatSnapshot('a',before,False,at_latest=True,identity_verified=True))
        self.store.update_job(self.store.head_job()['id'],status='sent')
        self.scheduler.observe_chat(ChatSnapshot('a',before + [Message('incoming','new'),
            Message('outgoing','old reply')],False,at_latest=True,identity_verified=True))
        self.assertEqual(self.store.active_jobs(), [])
        self.assertEqual(self.store.all_jobs()[-1]['status'],'ambiguous')
        self.assertEqual(self.store.all_jobs()[-1]['new_messages'][0]['text'],'new')

    def test_muted_and_unknown_never_enter_queue(self):
        self.scheduler.enqueue_scan([candidate('a', 200, muted=True), candidate('b', 273, muted=None)])
        self.assertEqual(self.store.active_jobs(), [])

    def test_muted_or_unknown_scan_removes_existing_draft_from_queue(self):
        for mute in (True, None):
            with self.subTest(mute=mute):
                self.scheduler.enqueue_scan([candidate('a', 200, preview=str(mute))])
                job = self.store.head_job()
                self.store.update_job(job['id'], status='draft_ready', draft='old reply')
                self.scheduler.enqueue_scan([candidate('a', 200, muted=mute, unread=False)])
                self.assertEqual(self.store.active_jobs(), [])
                self.assertEqual(self.store.job(job['id'])['status'], 'skipped')

    def test_initial_read_keeps_queue_order_and_stores_separate_contexts(self):
        self.scheduler.enqueue_scan([candidate('a', 200), candidate('b', 273)])
        self.scheduler.observe_chat(ChatSnapshot('b', [Message('incoming', '给乙')], False, at_latest=True, identity_verified=True))
        self.scheduler.observe_chat(ChatSnapshot('a', [Message('incoming', '给甲')], False, at_latest=True, identity_verified=True))
        jobs = self.store.active_jobs()
        self.assertEqual([j['conversation'] for j in jobs], ['b', 'a'])
        self.assertEqual(jobs[0]['messages'][0]['text'], '给乙')
        self.assertEqual(jobs[1]['messages'][0]['text'], '给甲')

    def test_unread_boundary_unknown_is_ambiguous(self):
        c = candidate('a', 200)
        c = ConversationCandidate(c.key, c.label, c.position, True, False, c.signature, True, c.preview, None)
        self.scheduler.enqueue_scan([c])
        self.scheduler.observe_chat(ChatSnapshot('a', [Message('incoming', 'hello')], False, at_latest=True, identity_verified=True))
        self.assertEqual(self.store.active_jobs(), [])
        self.assertEqual(self.store.all_jobs()[0]['status'], 'ambiguous')

    def test_ordered_overlap_preserves_identical_messages(self):
        m = Message('incoming', '好')
        aligned = align_messages([m], [m, m])
        self.assertFalse(aligned.ambiguous)
        self.assertEqual(aligned.appended, (m,))
        self.assertTrue(align_messages([m, m], [m, m, m]).ambiguous)
        self.assertTrue(align_messages([m], [Message('incoming', '另一个')]).ambiguous)


if __name__ == '__main__':
    unittest.main()
