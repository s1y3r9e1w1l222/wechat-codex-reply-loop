import tempfile
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from wechat_autoreply.config import Config
from wechat_autoreply.models import ChatSnapshot, ConversationCandidate, Message
from wechat_autoreply.adapters.base import ScanResult
from wechat_autoreply.provider import FakeProvider
from wechat_autoreply.generation import GenerationResult
from wechat_autoreply.service import Service
from wechat_autoreply.store import Store
from chat_support import FakeClock, SyntheticAdapter


class ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        result = Future()
        try:
            result.set_result(fn(*args, **kwargs))
        except Exception as error:
            result.set_exception(error)
        return result
    def shutdown(self, **kwargs):
        pass


class DistinctProvider(FakeProvider):
    def generate_task(self, request, persona='', limits=None, cancel=None):
        super().generate_task(request,persona,limits,cancel)
        return GenerationResult('reply','synthetic message '+str(request.sequence or 'reply'))


class MultiAppServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.store = Store(Path(self.tmp.name)/'state.sqlite3','jimu',self.clock.wall)
        self.addCleanup(self.store.close)
        row = ConversationCandidate('a','A',0,False,False,identity_verified=False)
        self.scan = ScanResult((row,),True,{'reached_end':True},'s','jimu-synthetic')
        self.driver = SyntheticAdapter('jimu',[self.scan],{'a':ChatSnapshot('a',[],False,identity_verified=True,at_latest=True,initial_unread=())})

    def service(self, mode='send'):
        return Service(Config(app_id='jimu',quiet_seconds=0),self.store,self.driver,DistinctProvider(),
                       executor=ImmediateExecutor(),clock=self.clock.wall,monotonic=self.clock.monotonic,mode=mode)

    def cycle(self, service):
        for _ in range(4):
            service.tick()

    def start(self, service):
        discovery = service.handle_command({'command':'discover','full':True})
        return service.handle_command({'command':'campaign_start','request_id':'manual',
                                       'snapshot_id':discovery['snapshot_id']})

    def test_non_wechat_default_all_supports_manual_three_message_round(self):
        service = self.service()
        self.start(service)
        for number in (1,2,3):
            self.cycle(service)
            self.assertEqual(self.driver.actions.count(('submit','a')),number)
            self.clock.advance(7200)
        self.cycle(service)
        self.assertEqual(self.driver.actions.count(('submit','a')),3)
        self.assertEqual(service.status()['campaigns'][0]['status'],'completed')

    def test_new_reply_cancels_followup_and_enters_reply_queue(self):
        service = self.service()
        self.start(service)
        self.cycle(service)
        snap = self.driver.chats['a']
        self.driver.chats['a'] = replace(snap,messages=snap.messages+[Message('incoming','hi')])
        row = replace(self.scan.candidates[0],unread=True,unread_count=1,preview='hi')
        self.driver.scans.append(replace(self.scan,candidates=(row,)))
        self.clock.advance(2)
        self.cycle(service)
        self.assertEqual(service.status()['campaigns'][0]['status'],'ended')
        self.assertEqual(self.driver.actions.count(('submit','a')),2)
        self.clock.advance(7200)
        self.cycle(service)
        self.assertEqual(self.driver.actions.count(('submit','a')),2)

    def test_daily_empty_scope_cancels_existing_round(self):
        service = self.service()
        self.start(service)
        service.handle_command({'command':'scope_set','date':service.scope.today(),'selected':[]})
        self.cycle(service)
        self.assertNotIn(('submit','a'),self.driver.actions)
        self.assertEqual(service.status()['campaigns'][0]['status'],'ended')

    def test_preview_never_advances_real_round_count(self):
        service = self.service('preview')
        self.start(service)
        self.cycle(service)
        self.assertNotIn(('submit','a'),self.driver.actions)
        self.assertEqual(service.status()['campaigns'][0]['confirmed_count'],0)

    def test_unknown_input_boundary_is_not_replied_as_whole_history(self):
        row = replace(self.scan.candidates[0],unread=True,preview='hi')
        self.driver.scans = [replace(self.scan,candidates=(row,))]
        self.driver.chats['a'] = ChatSnapshot('a',[Message('incoming','old')],False,identity_verified=True,at_latest=True)
        service = self.service()
        self.cycle(service)
        self.assertEqual(self.store.conversation('a')['blocked'],'unread_boundary_unknown')
        self.assertNotIn(('submit','a'),self.driver.actions)

    def test_late_proactive_generation_cannot_send_after_new_reply(self):
        class Deferred(ImmediateExecutor):
            def __init__(self):
                self.futures=[]
            def submit(self,*args,**kwargs):
                f=Future()
                self.futures.append(f)
                return f
        service=self.service()
        pool=Deferred()
        service.pool=pool
        self.start(service)
        service.tick()
        self.driver.chats['a']=replace(self.driver.chats['a'],messages=[Message('incoming','new')])
        self.driver.scans.append(replace(self.scan,candidates=(replace(self.scan.candidates[0],unread=True,unread_count=1,preview='new'),)))
        self.clock.advance(2)
        service.tick()
        pool.futures[0].set_result(GenerationResult('reply','stale opener'))
        service.tick()
        self.assertNotIn(('type','stale opener'),self.driver.actions)
        self.assertEqual(service.status()['campaigns'][0]['status'],'ended')

    def test_stop_contact_is_persistent_until_explicit_unblock(self):
        class StopProvider(FakeProvider):
            def generate_task(self,*args,**kwargs):
                return GenerationResult('stop_contact',reason='explicit request')
        service=self.service()
        service.provider=StopProvider()
        self.start(service)
        self.cycle(service)
        self.assertFalse(service.scope.allows('a'))
        self.assertNotIn(('submit','a'),self.driver.actions)
        service.handle_command({'command':'contact_unblock','conversation':'a'})
        self.assertTrue(service.scope.allows('a'))

    def test_command_fields_are_validated_per_command(self):
        service=self.service()
        with self.assertRaises(ValueError):
            service.handle_command({'command':'status','selected':['a']})
        self.assertFalse(service.request({'command':'status','expected_app_id':'wechat'})['ok'])

    def test_manual_request_replay_survives_discovery_expiration(self):
        service=self.service()
        discovery=service.handle_command({'command':'discover','full':True})
        request={'command':'campaign_start','request_id':'repeat','snapshot_id':discovery['snapshot_id']}
        result=service.handle_command(request)
        self.clock.advance(301)
        self.assertEqual(service.handle_command(request),result)
        self.assertEqual(len(service.campaigns.list()),1)

    def test_unsupported_adapter_remains_send_disabled(self):
        self.driver.capabilities=replace(self.driver.capabilities,verified=False)
        with self.assertRaisesRegex(ValueError,'adapter_unverified'):
            self.service()

    def test_first_proactive_read_with_unknown_boundary_never_sends(self):
        self.driver.chats['a']=replace(self.driver.chats['a'],messages=[Message('incoming','fresh after scan')],initial_unread=None)
        service=self.service()
        self.start(service)
        self.cycle(service)
        self.assertNotIn(('submit','a'),self.driver.actions)
        self.assertEqual(self.store.conversation('a')['blocked'],'proactive_initial_boundary_unknown')

    def assert_restart_recovers_round(self, baseline):
        if baseline:
            self.driver.chats['a']=replace(self.driver.chats['a'],messages=[Message('incoming','old read context')])
        service=self.service()
        self.start(service)
        submit=self.driver.submit
        def interrupted():
            submit()
            raise RuntimeError('crash_after_submit')
        self.driver.submit=interrupted
        self.cycle(service)
        self.assertEqual(self.store.all_jobs()[0]['status'],'uncertain')
        self.driver.submit=submit
        restarted=self.service()
        self.assertEqual(restarted.state,'recovering')
        restarted.tick()
        campaign=restarted.campaigns.list()[0]
        self.assertEqual(campaign['status'],'active')
        self.assertEqual(campaign['next_sequence'],2)
        self.assertEqual(self.store.conversation('a')['blocked'],'')

    def test_restart_reconcile_own_send_with_existing_baseline(self):
        self.assert_restart_recovers_round(True)

    def test_restart_reconcile_own_send_with_empty_baseline(self):
        self.assert_restart_recovers_round(False)

    def test_manual_sent_resolution_advances_round_and_readback(self):
        service=self.service()
        self.start(service)
        submit=self.driver.submit
        def interrupted():
            submit()
            raise RuntimeError('crash_after_submit')
        self.driver.submit=interrupted
        self.cycle(service)
        self.driver.submit=submit
        job=self.store.all_jobs()[0]
        service.handle_command({'command':'resolve','job_id':job['id'],'outcome':'sent'})
        self.assertEqual(service.campaigns.list()[0]['next_sequence'],2)
        service.resume()
        service.tick()
        self.assertEqual(self.store.conversation('a')['blocked'],'')
        self.assertEqual(service.campaigns.list()[0]['status'],'active')
        self.clock.advance(7200)
        self.cycle(service)
        self.assertEqual(self.driver.actions.count(('submit','a')),2)

    def test_proactive_retry_preserves_kind_round_and_sequence(self):
        class FailProvider(FakeProvider):
            def generate_task(self,*args,**kwargs):
                raise RuntimeError('generation_failed')
        service=self.service()
        service.provider=FailProvider()
        self.start(service)
        self.cycle(service)
        job=self.store.all_jobs()[0]
        self.assertEqual(job['status'],'failed')
        service.provider=DistinctProvider()
        service.handle_command({'command':'retry','job_id':job['id']})
        self.cycle(service)
        self.assertEqual(self.driver.actions.count(('submit','a')),1)
        self.assertEqual(len(self.store.all_jobs()),1)
        self.assertEqual(self.store.job(job['id'])['kind'],'proactive_initial')
        self.assertEqual(self.store.job(job['id'])['sequence'],1)

    def test_rebaseline_ends_proactive_round(self):
        service=self.service('preview')
        self.start(service)
        self.cycle(service)
        service.handle_command({'command':'rebaseline','conversation':'a'})
        self.assertEqual(service.campaigns.list()[0]['status'],'ended')

    def test_abandoned_resolution_ends_proactive_round(self):
        service=self.service('preview')
        self.start(service)
        self.cycle(service)
        job=self.store.all_jobs()[0]
        self.store.update_job(job['id'],status='failed')
        service.handle_command({'command':'resolve','job_id':job['id'],'outcome':'abandoned'})
        self.assertEqual(service.campaigns.list()[0]['status'],'ended')

    def test_third_manual_confirmation_is_read_back_after_restart(self):
        service=self.service()
        self.start(service)
        self.cycle(service)
        self.clock.advance(7200)
        self.cycle(service)
        submit=self.driver.submit
        def interrupted():
            submit()
            raise RuntimeError('crash_after_submit')
        self.driver.submit=interrupted
        self.clock.advance(7200)
        self.cycle(service)
        self.driver.submit=submit
        job=self.store.all_jobs()[-1]
        service.handle_command({'command':'resolve','job_id':job['id'],'outcome':'sent'})
        restarted=self.service()
        self.assertEqual(restarted.state,'recovering')
        restarted.tick()
        self.assertEqual(self.store.conversation('a')['blocked'],'')

    def pause_after_type(self):
        service=self.service()
        self.start(service)
        input_text=self.driver.input_text
        def paused(text):
            input_text(text)
            service.pause()
        self.driver.input_text=paused
        self.cycle(service)
        self.driver.input_text=input_text
        return service

    def test_pause_after_type_cleans_owned_draft_on_resume(self):
        service=self.pause_after_type()
        self.assertEqual(service.state,'paused')
        self.assertTrue(self.store.state('owned_draft'))
        service.resume()
        self.cycle(service)
        self.assertEqual(self.driver.actions.count(('submit','a')),1)
        self.assertIsNone(self.store.state('owned_draft'))
        self.assertEqual(self.store.conversation('a')['blocked'],'')

    def test_changed_owned_draft_is_never_erased(self):
        service=self.pause_after_type()
        self.driver.chats['a']=replace(self.driver.chats['a'],draft='human changed text')
        service.resume()
        service.tick()
        self.assertEqual(self.driver.chats['a'].draft,'human changed text')
        self.assertEqual(service.state,'needs_attention')
        self.assertNotIn(('submit','a'),self.driver.actions)

    def test_unsubmitted_owned_draft_survives_restart(self):
        self.pause_after_type()
        restarted=self.service('preview')
        restarted.tick()
        self.assertEqual(self.driver.chats['a'].draft,'')
        self.assertIsNone(self.store.state('owned_draft'))

    def test_status_and_campaign_list_are_bounded_without_full_instructions(self):
        service=self.service()
        service.campaigns.start('many',['contact'+str(i) for i in range(125)],'topic '*200)
        status=service.handle_command({'command':'status'})
        self.assertEqual(len(status['campaigns']),100)
        self.assertTrue(all('instruction' not in c for c in status['campaigns']))
        page=service.handle_command({'command':'campaign_list','limit':100})
        next_page=service.handle_command({'command':'campaign_list','limit':100,'before':page['next_before']})
        self.assertEqual(len(next_page['campaigns']),25)
        self.assertEqual(status['campaign_counts']['active'],125)

    def test_manual_rebaseline_retires_confirmation_and_allows_expired_cleanup(self):
        service=self.service()
        self.start(service)
        submit=self.driver.submit
        def interrupted():
            submit()
            raise RuntimeError('crash_after_submit')
        self.driver.submit=interrupted
        self.cycle(service)
        self.driver.submit=submit
        job=self.store.all_jobs()[0]
        campaign_id=job['campaign_id']
        service.handle_command({'command':'resolve','job_id':job['id'],'outcome':'sent'})
        service.handle_command({'command':'rebaseline','conversation':'a'})
        self.clock.advance(10*86400)
        self.store.cleanup(7)
        self.assertIsNone(service.campaigns.get(campaign_id))
