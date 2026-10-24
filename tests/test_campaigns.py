import tempfile
import unittest
from pathlib import Path
from datetime import timezone
from wechat_autoreply.store import Store
from wechat_autoreply.config import Config
from wechat_autoreply.scope import ScopePolicy
from wechat_autoreply.campaigns import CampaignManager, ClockGuard
from chat_support import FakeClock


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.store = Store(Path(self.tmp.name)/'state.sqlite3', 'jimu', self.clock.wall)
        self.addCleanup(self.store.close)
        config = Config(app_id='jimu')
        self.policy = ScopePolicy(self.store, config, timezone.utc, self.clock.wall)
        self.guard = ClockGuard(self.store, self.clock.wall, self.clock.monotonic)
        self.manager = CampaignManager(self.store, self.policy, self.guard)

    def confirm(self, job_id):
        attempt = self.store.mark_sending(job_id, 'text', [])
        self.store.finish_attempt(attempt, 'sent')
        self.manager.on_confirmed(job_id, self.clock.wall())

    def test_manual_round_has_three_slots_with_two_hour_spacing(self):
        result = self.manager.start('r', ['a'])
        self.assertIn('a', result['created'])
        sequences = []
        for number in (1, 2, 3):
            jobs = self.manager.materialize_due(self.guard.now())
            self.assertEqual(len(jobs), 1)
            sequences.append(self.store.job(jobs[0])['sequence'])
            self.confirm(jobs[0])
            if number < 3:
                self.clock.advance(7199)
                self.assertEqual(self.manager.materialize_due(self.guard.now()), [])
                self.clock.advance(1)
        self.clock.advance(86400)
        self.assertEqual(sequences, [1, 2, 3])
        self.assertEqual(self.manager.materialize_due(self.guard.now()), [])

    def test_request_idempotence_conflict_and_active_round_do_not_reset(self):
        first = self.manager.start('same', ['a'], 'hello')
        self.assertEqual(self.manager.start('same', ['a'], 'hello'), first)
        with self.assertRaisesRegex(ValueError, 'request_id_conflict'):
            self.manager.start('same', ['a'], 'different')
        self.assertEqual(self.manager.start('new', ['a'])['existing']['a'], first['created']['a'])

    def test_reply_manual_outgoing_scope_change_and_stop_cancel(self):
        for key, action in [('a',self.manager.observe_incoming), ('b',self.manager.observe_manual_outgoing)]:
            self.manager.start(key, [key])
            jobs = self.manager.materialize_due(self.guard.now())
            action(key)
            self.assertEqual(self.store.job(jobs[0])['status'], 'stale')
        self.manager.start('scope', ['c'])
        self.policy.set_daily(self.policy.today(), [])
        self.assertEqual(self.manager.materialize_due(self.guard.now()), [])
        self.policy.delete_daily(self.policy.today())
        self.assertEqual(self.manager.materialize_due(self.guard.now()), [])
        self.manager.stop_contact('d', 'declined')
        self.assertIn('d', self.manager.start('stop', ['d'])['skipped'])

    def test_restart_and_clock_jump_delay_followups_without_burst(self):
        self.manager.start('r', ['a'])
        self.confirm(self.manager.materialize_due(self.guard.now())[0])
        self.clock.jump_wall(100000)
        self.assertEqual(self.manager.materialize_due(self.guard.now()), [])
        self.assertFalse(self.guard.trusted())
        self.guard.resume()
        self.assertEqual(self.manager.materialize_due(self.guard.now()), [])
        self.clock.advance(7200)
        guard = ClockGuard(self.store, self.clock.wall, self.clock.monotonic)
        manager = CampaignManager(self.store, self.policy, guard)
        self.assertEqual(manager.materialize_due(guard.now()), [])
        self.clock.advance(7200)
        self.assertEqual(len(manager.materialize_due(guard.now())), 1)

    def test_uncertain_submission_never_materializes_another_slot(self):
        self.manager.start('r', ['a'])
        job = self.manager.materialize_due(self.guard.now())[0]
        attempt = self.store.mark_sending(job, 'text', [])
        self.store.finish_attempt(attempt, 'uncertain')
        self.clock.advance(72000)
        self.assertEqual(self.manager.materialize_due(self.guard.now()), [])

    def test_pending_or_failed_reply_prevents_round_and_later_materialization(self):
        job=self.store.create_job('a',0,[],[{'direction':'incoming','text':'hi','kind':'text','anchor':''}])
        self.store.update_job(job['id'],status='failed')
        self.assertEqual(self.manager.start('a',['a'])['skipped']['a'],'unresolved_reply')
        result=self.manager.start('b',['b'])
        self.store.create_job('b',0,[],[{'direction':'incoming','text':'hi','kind':'text','anchor':''}])
        self.assertEqual(self.manager.materialize_due(self.guard.now()),[])
        self.assertEqual(self.manager.get(result['created']['b'])['status'],'ended')

    def test_cleanup_removes_completed_history_but_preserves_active_and_idempotence(self):
        from wechat_autoreply.scope import DiscoveryCatalog
        from wechat_autoreply.adapters.base import ScanResult
        from wechat_autoreply.models import ConversationCandidate
        completed=self.manager.start('completed',['a'])['created']['a']
        for _ in range(3):
            job=self.manager.materialize_due(self.guard.now())[0]
            self.confirm(job)
            self.clock.advance(7200)
        active=self.manager.start('active',['b'])['created']['b']
        catalog=DiscoveryCatalog(self.store,self.policy)
        sid=catalog.save(ScanResult((ConversationCandidate('a','A',0,False,False),),True,{},'s','t'),self.clock.wall())
        self.clock.advance(10*86400)
        self.store.cleanup(7)
        self.assertIsNone(self.manager.get(completed))
        self.assertIsNotNone(self.manager.get(active))
        self.assertIsNone(self.store.job(job))
        with self.assertRaisesRegex(ValueError,'unknown_discovery'):
            catalog.get(sid)
        replay=self.manager.start('completed',['a'])
        self.assertTrue(replay['expired'])
