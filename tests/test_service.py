import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from wechat_autoreply.config import Config
from wechat_autoreply.models import ChatSnapshot, ConversationCandidate, Message, SendResult
from wechat_autoreply.service import Service
from wechat_autoreply.store import Store


def row(key, pos, muted=False):
    return ConversationCandidate(key, key, pos, True, muted, key, True, key, 1)


class Driver:
    def __init__(self):
        self.rows = [row('a', 200), row('b', 273), row('muted', 346, True)]
        self.reads, self.scans = [], 0
    def scan(self, cancel=None):
        self.scans += 1
        return self.rows
    def read_chat(self, key, cancel=None):
        self.reads.append(key)
        return ChatSnapshot(key, [Message('incoming', '消息' + key)], False,
                            at_latest=True, identity_verified=True)
    def return_to_list(self, cancel=None):
        pass


class Executor:
    def __init__(self):
        self.calls = []
    def submit(self, fn, *args, **kwargs):
        f = Future()
        self.calls.append((f, fn, args, kwargs))
        return f
    def shutdown(self, **kwargs):
        pass


class Recorder:
    def __init__(self, store):
        self.store, self.sent = store, []
    def send(self, job, cancellation):
        self.sent.append(job['conversation'])
        self.store.update_job(job['id'], status='sent')
        return SendResult('sent')


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.driver, self.pool = Driver(), Executor()
        self.recorder = Recorder(self.store)
        self.now = time.time()
        self.store.set_state('previewed_contacts', ['a','b','muted'])
        self.service = Service(Config(allowed=['a','b','muted'], quiet_seconds=0), self.store,
                               self.driver, SimpleNamespace(generate=lambda *a, **k: '回复'), sender=self.recorder, executor=self.pool,
                               clock=lambda: self.now, mode='send')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def tick(self):
        self.now += 2
        self.service.tick()

    def test_parallel_generation_cannot_change_bottom_first_send_order(self):
        self.tick()
        self.tick()
        self.assertEqual(self.driver.reads, ['b','a'])
        self.assertEqual(len(self.pool.calls), 2)
        self.pool.calls[1][0].set_result('后面的先完成')
        self.tick()
        self.assertEqual(self.recorder.sent, [])
        self.pool.calls[0][0].set_result('前面的完成')
        self.tick()
        self.tick()
        self.assertEqual(self.recorder.sent, ['b','a'])

    def test_pause_discards_late_generation_and_restart_defaults_to_preview(self):
        self.tick()
        self.service.pause()
        self.pool.calls[0][0].set_result('过期回复')
        self.tick()
        self.assertEqual(self.recorder.sent, [])
        self.assertNotIn('过期回复', [j['draft'] for j in self.store.all_jobs()])
        restarted = Service(Config(allowed=['a']), self.store, self.driver, object(), executor=Executor())
        self.assertEqual(restarted.mode, 'preview')

    def test_fresh_state_cannot_send_before_completed_preview(self):
        self.store.set_state('previewed_contacts',[])
        with self.assertRaisesRegex(ValueError,'预览'):
            Service(Config(allowed=['a']),self.store,self.driver,object(),executor=Executor(),mode='send')

    def test_completed_preview_allows_same_contacts_but_new_contact_needs_preview(self):
        self.store.set_state('previewed_contacts',[])
        self.service.mode = 'preview'
        self.tick()
        self.tick()
        self.assertEqual(self.store.state('previewed_contacts'),[])
        for future, _, _, _ in self.pool.calls:
            future.set_result('preview reply')
        self.tick()
        self.assertEqual(set(self.store.state('previewed_contacts')),{'a','b','muted'})
        Service(Config(allowed=['a']),self.store,self.driver,object(),executor=Executor(),mode='send')
        with self.assertRaisesRegex(ValueError,'预览'):
            Service(Config(allowed=['a','new']),self.store,self.driver,object(),executor=Executor(),mode='send')

    def test_muted_rows_do_not_read_or_generate(self):
        self.driver.rows = [row('muted',200,True)]
        self.tick()
        self.assertEqual(self.driver.reads, [])
        self.assertEqual(self.pool.calls, [])

    def test_resume_revalidates_already_read_input_without_losing_job(self):
        self.tick()
        self.service.pause()
        self.service.resume()
        self.tick()
        jobs = self.store.active_jobs()
        self.assertTrue(any(j['conversation']=='b' for j in jobs))
        self.assertFalse(self.store.conversation('b')['blocked'])

    def test_generation_failure_does_not_block_other_conversation(self):
        self.tick()
        self.tick()
        self.pool.calls[0][0].set_exception(RuntimeError('failed'))
        self.pool.calls[1][0].set_result('回复')
        self.tick()
        self.assertEqual(self.recorder.sent, ['a'])

    def test_post_send_incoming_is_new_tail_job(self):
        self.tick()
        self.tick()
        self.pool.calls[0][0].set_result('reply b')
        def send(job, cancel):
            self.store.update_job(job['id'],status='sent')
            return SendResult('sent',snapshot=ChatSnapshot('b',[
                Message('incoming','消息b'),Message('outgoing','reply b'),
                Message('incoming','new input')],False,at_latest=True,identity_verified=True))
        self.service.sender.send = send
        self.tick()
        self.assertEqual([j['conversation'] for j in self.store.active_jobs()],['a','b'])
        self.assertEqual(self.store.active_jobs()[-1]['new_messages'][0]['text'],'new input')

    def test_uncertain_cannot_be_retried_and_resolution_does_not_send(self):
        job = self.store.create_job('a',1,[],[])
        self.store.update_job(job['id'],status='uncertain')
        with self.assertRaises(ValueError):
            self.service.handle_command({'command':'retry','job_id':job['id']})
        self.service.handle_command({'command':'resolve','job_id':job['id'],'outcome':'handled'})
        self.assertEqual(self.recorder.sent, [])
        self.assertEqual(self.store.job(job['id'])['status'],'handled')

    def test_rebaseline_retires_every_old_active_version(self):
        self.store.ensure_conversation('a')
        for status in ('pending','generating','draft_ready'):
            job = self.store.create_job('a',0,[],[])
            self.store.update_job(job['id'],status=status,draft='old reply')
        self.service.handle_command({'command':'rebaseline','conversation':'a'})
        self.assertEqual(self.store.active_jobs(),[])
        self.assertTrue(all(j['status']=='stale' for j in self.store.all_jobs()))

    def test_retry_empty_ambiguous_input_refuses_without_changing_baseline(self):
        job = self.store.create_job('a',0,[],[])
        self.store.update_job(job['id'],status='ambiguous')
        with self.assertRaisesRegex(ValueError,'输入边界'):
            self.service.handle_command({'command':'retry','job_id':job['id']})
        self.assertEqual(self.store.active_jobs(),[])
        self.assertEqual(self.store.conversation('a')['version'],0)

    def test_control_response_explains_unverifiable_retry_boundary(self):
        job = self.store.create_job('a',0,[],[])
        self.store.update_job(job['id'],status='ambiguous')
        future = Future()
        self.service.commands.put(({'command':'retry','job_id':job['id']},future))
        self.service._drain_commands()
        self.assertFalse(future.result()['ok'])
        self.assertIn('输入边界',future.result()['error'])

    def test_retry_reconstructs_input_and_retires_old_active_version(self):
        from wechat_autoreply.models import messages_json
        before = [Message('incoming','old')]
        current = before + [Message('incoming','new')]
        self.store.ensure_conversation('a')
        self.store.update_conversation('a',snapshot=messages_json(before))
        failed = self.store.create_job('a',0,[],[])
        self.store.update_job(failed['id'],status='ambiguous')
        active = self.store.create_job('a',0,[],[])
        self.driver.read_chat = lambda *args:ChatSnapshot('a',current,False,at_latest=True,identity_verified=True)
        self.service.handle_command({'command':'retry','job_id':failed['id']})
        self.assertEqual(self.store.job(active['id'])['status'],'stale')
        self.assertEqual(self.store.head_job()['new_messages'],messages_json([current[-1]]))

    def test_startup_reconciliation_only_reads_and_never_submits(self):
        from wechat_autoreply.models import messages_json
        from wechat_autoreply.sending import Sender
        from test_sending import FakeDriver, FakeSession, snapshot
        for verified in (True, False):
            with self.subTest(verified=verified):
                job = self.store.create_job('a',1,[],[])
                before = [Message('incoming','你好')]
                self.store.update_conversation('a',snapshot=messages_json(before),version=1,blocked='')
                attempt = self.store.mark_sending(job['id'],'收到',messages_json(before))
                self.store.finish_attempt(attempt,'uncertain')
                after = before + ([Message('outgoing','收到')] if verified else [])
                session = FakeSession([snapshot(after)])
                driver = FakeDriver(session)
                sender = Sender(self.store,driver,{'a'})
                service = Service(Config(allowed=['a']),self.store,driver,object(),sender=sender,executor=Executor())
                service.tick()
                self.assertEqual(session.sends,0)
                self.assertEqual(session.inputs,[])
                self.assertEqual(session.returns,1)
                self.assertEqual(self.store.job(job['id'])['status'],'sent' if verified else 'uncertain')
                self.assertEqual(service.state,'monitoring' if verified else 'needs_attention')
                if not verified:
                    self.store.update_job(job['id'],status='abandoned')

    def test_quiet_period_defers_generation_but_not_beyond_max_wait(self):
        self.service.config.quiet_seconds = 2
        self.service.config.max_wait_seconds = 8
        self.service.clock = time.time
        self.service.tick()
        self.assertEqual(self.pool.calls, [])
        job = self.store.head_job()
        self.store.db.execute('UPDATE reply_jobs SET created=?,updated=? WHERE id=?',
                              (time.time()-9,time.time(),job['id']))
        self.store.db.commit()
        self.service.tick()
        self.assertEqual(len(self.pool.calls),1)


if __name__ == '__main__':
    unittest.main()
