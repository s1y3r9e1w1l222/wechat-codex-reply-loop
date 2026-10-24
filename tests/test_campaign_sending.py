import tempfile
import threading
import unittest
from pathlib import Path
from datetime import timezone
from wechat_autoreply.config import Config
from wechat_autoreply.store import Store
from wechat_autoreply.scope import ScopePolicy
from wechat_autoreply.campaigns import CampaignManager, ClockGuard
from wechat_autoreply.sending import Sender
from wechat_autoreply.models import ChatSnapshot, Message, messages_json
from chat_support import SyntheticAdapter, FakeClock


class CampaignSendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.store = Store(Path(self.tmp.name)/'state.sqlite3','jimu',self.clock.wall)
        self.addCleanup(self.store.close)
        self.policy = ScopePolicy(self.store,Config(app_id='jimu'),timezone.utc,self.clock.wall)
        self.guard = ClockGuard(self.store,self.clock.wall,self.clock.monotonic)
        self.manager = CampaignManager(self.store,self.policy,self.guard)
        self.manager.start('r',['a'])
        self.job_id = self.manager.materialize_due(self.guard.now())[0]
        self.snapshot = ChatSnapshot('a',[],False,identity_verified=True,at_latest=True)
        self.driver = SyntheticAdapter('jimu',chats={'a':self.snapshot})
        self.store.update_job(self.job_id,status='draft_ready',needs_refresh=0,draft='hello')

    def sender(self):
        return Sender(self.store,self.driver,[],mode='send',scope=self.policy,
                      campaigns=self.manager,clock=self.clock.wall)

    def test_confirm_and_next_due_commit_atomically(self):
        result = self.sender().send(self.store.job(self.job_id),threading.Event())
        self.assertEqual(result.status,'sent')
        campaign = self.manager.get(self.store.job(self.job_id)['campaign_id'])
        self.assertEqual(campaign['confirmed_count'],1)
        self.assertEqual(campaign['not_before'],8200)
        self.assertEqual(self.driver.actions.count(('submit','a')),1)

    def test_policy_revoked_after_input_never_submits(self):
        original = self.driver.input_text
        def revoke(text):
            original(text)
            self.policy.set_daily(self.policy.today(),[])
        self.driver.input_text = revoke
        result = self.sender().send(self.store.job(self.job_id),threading.Event())
        self.assertEqual(result.status,'skipped')
        self.assertNotIn(('submit','a'),self.driver.actions)
        self.assertEqual(self.driver.chats['a'].draft,'')

    def test_duplicate_checkpoint_and_fourth_sequence_are_rejected(self):
        attempt = self.store.mark_sending(self.job_id,'hello',[])
        with self.assertRaises(ValueError):
            self.store.mark_sending(self.job_id,'hello',[])
        self.store.finish_attempt(attempt,'uncertain')
        self.clock.advance(72000)
        self.assertEqual(self.manager.materialize_due(self.guard.now()),[])
        bad = self.store.create_job('a',0,[],[],kind='proactive_followup',
                    campaign_id=self.store.job(self.job_id)['campaign_id'],sequence=4)
        with self.assertRaisesRegex(ValueError,'round'):
            self.store.mark_sending(bad['id'],'four',[])

    def test_failure_between_sent_and_next_due_rolls_back_to_uncertain(self):
        def fail(*args):
            raise RuntimeError('injected_after_sent')
        self.manager.on_confirmed = fail
        result = self.sender().send(self.store.job(self.job_id),threading.Event())
        self.assertEqual(result.status,'uncertain')
        self.assertEqual(self.manager.get(self.store.job(self.job_id)['campaign_id'])['confirmed_count'],0)
        self.assertEqual(self.driver.actions.count(('submit','a')),1)

    def test_wrong_app_and_cancelled_round_do_not_submit(self):
        self.driver.app_id='qingteng'
        self.assertEqual(self.sender().send(self.store.job(self.job_id),threading.Event()).status,'skipped')
        self.assertEqual(self.driver.actions,[])

    def test_failed_reply_appearing_after_generation_blocks_submit(self):
        job=self.store.create_job('a',0,[],[{'direction':'incoming','text':'hi','kind':'text','anchor':''}])
        self.store.update_job(job['id'],status='failed')
        result=self.sender().send(self.store.job(self.job_id),threading.Event())
        self.assertEqual(result.status,'skipped')
        self.assertNotIn(('submit','a'),self.driver.actions)
        self.driver.app_id='jimu'
        self.manager.cancel(self.store.job(self.job_id)['campaign_id'],'manual')
        self.assertEqual(self.sender().send(self.store.job(self.job_id),threading.Event()).status,'skipped')
        self.assertEqual(self.driver.actions,[])
