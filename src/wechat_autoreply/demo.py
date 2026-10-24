"""Deterministic replay; never imports or connects to the phone runtime."""
import time
from concurrent.futures import Future
from tempfile import TemporaryDirectory
from pathlib import Path

from .config import Config
from .models import ChatSnapshot, ConversationCandidate, Message, SendResult
from .provider import FakeProvider
from .service import Service
from .store import Store


class DemoDriver:
    offline_demo = True
    def __init__(self):
        self.rows = [ConversationCandidate('a','测试甲',200,True,False,'a',True,'甲的新消息',1),
                     ConversationCandidate('b','测试乙',273,True,False,'b',True,'乙的新消息',1),
                     ConversationCandidate('muted','免打扰联系人',346,True,True,'muted',True,'跳过',1)]
        self.coverage = {'scope':'offline_replay', 'pages':1}
    def scan(self, cancel=None):
        return self.rows
    def read_chat(self, key, cancel=None):
        return ChatSnapshot(key,[Message('incoming','这是一条来自'+key+'的模拟消息')],False,
                            at_latest=True,identity_verified=True)
    def return_to_list(self, cancel=None):
        pass


class DemoSender:
    def __init__(self, store):
        self.store, self.sent = store, []
    def send(self, job, cancellation):
        self.store.update_job(job['id'],status='sent',reason='offline_demo_only')
        self.sent.append(job['conversation'])
        return SendResult('sent')


class DeferredExecutor:
    def __init__(self):
        self.futures = []
    def submit(self, *args, **kwargs):
        future = Future()
        self.futures.append(future)
        return future
    def shutdown(self, **kwargs):
        pass


def replay_demo():
    with TemporaryDirectory(prefix='wechat-replay-') as directory:
        store = Store(Path(directory) / 'demo.sqlite3')
        sender, executor = DemoSender(store), DeferredExecutor()
        now = [time.time()]
        service = Service(Config(allowed=['a','b','muted'],quiet_seconds=0),store,DemoDriver(),FakeProvider(),
                          sender=sender,executor=executor,clock=lambda:now[0],mode='send')
        def tick():
            now[0] += 2
            service.tick()
        tick()
        tick()
        executor.futures[1].set_result('甲的离线回复先生成')
        tick()
        held_order = not sender.sent
        executor.futures[0].set_result('乙的离线回复后生成')
        tick()
        tick()
        result = {'ok':sender.sent == ['b','a'] and held_order,
                  'mode':'离线回放，不操作手机', 'reply_order':['测试乙','测试甲'],
                  'muted_excluded':'muted' not in sender.sent,
                  'generation_finish_order_does_not_change_sends':held_order}
        store.close()
        return result


def replay_multi_app_demo():
    """Exercise the real service, policy and Sender with synthetic UI only."""
    from dataclasses import replace
    from .adapters.base import AdapterCapabilities, ScanResult
    from .generation import GenerationResult

    class Clock:
        value = 1704067200.0
        elapsed = 0.0
        def wall(self):
            return self.value
        def monotonic(self):
            return self.elapsed
        def advance(self, seconds):
            self.value += seconds
            self.elapsed += seconds

    class Executor:
        def submit(self, function, *args, **kwargs):
            future = Future()
            try:
                future.set_result(function(*args,**kwargs))
            except Exception as error:
                future.set_exception(error)
            return future
        def shutdown(self, **kwargs):
            pass

    class Provider(FakeProvider):
        def generate_task(self, request, persona='', limits=None, cancel=None):
            super().generate_task(request,persona,limits,cancel)
            return GenerationResult('reply','【离线演示】第 '+str(request.sequence or '回复')+' 条合成消息')

    class Driver:
        app_id = 'jimu'
        offline_demo = True
        bridge = None
        capabilities = AdapterCapabilities('jimu',True,'synthetic-only',True)
        coverage = {'scope':'synthetic_only','reached_end':True}
        def __init__(self):
            self.rows=[ConversationCandidate(k,'合成联系人'+k,float(i),False,False)
                       for i,k in enumerate(('a','b','outside'))]
            self.chats={k:ChatSnapshot(k,[],False,at_latest=True,identity_verified=True,initial_unread=()) for k in ('a','b','outside')}
            self.submits=[]
        def scan(self,cancel=None,*,full=False):
            return ScanResult(tuple(self.rows),True,self.coverage,'synthetic','synthetic-only')
        def read_chat(self,key,cancel=None):
            self.key=key
            return self.chats[key]
        def return_to_list(self,cancel=None):
            pass
        def transaction(self,callback,cancel=None):
            return callback(self)
        def input_text(self,text):
            self.chats[self.key]=replace(self.chats[self.key],draft=text)
        def clear_own_draft(self,expected):
            if self.chats[self.key].draft==expected:
                self.chats[self.key]=replace(self.chats[self.key],draft='')
        def submit(self):
            snap=self.chats[self.key]
            self.submits.append(self.key)
            self.chats[self.key]=replace(snap,draft='',messages=snap.messages+[Message('outgoing',snap.draft)])
        def preview_requirements(self,config):
            return {'synthetic-only'}
        def preview_evidence(self,scan):
            return {'synthetic-only'}

    clock=Clock()
    with TemporaryDirectory(prefix='chat-engine-replay-') as directory:
        store=Store(Path(directory)/'demo.sqlite3','jimu',clock.wall)
        try:
            driver=Driver()
            service=Service(Config(app_id='jimu',quiet_seconds=0),store,driver,Provider(),
                            executor=Executor(),clock=clock.wall,monotonic=clock.monotonic,mode='send')
            default_all=service.scope.allows('outside')
            service.handle_command({'command':'scope_set','date':service.scope.today(),'selected':['a','b']})
            discovery=service.handle_command({'command':'discover','full':True})
            service.handle_command({'command':'campaign_start','request_id':'demo','snapshot_id':discovery['snapshot_id']})
            def cycle():
                for _ in range(6):
                    service.tick()
            cycle()
            driver.chats['b']=replace(driver.chats['b'],messages=driver.chats['b'].messages+[Message('incoming','合成的新回复')])
            driver.rows=[replace(r,unread=True,unread_count=1,preview='合成的新回复') if r.key=='b' else r for r in driver.rows]
            clock.advance(2)
            cycle()
            driver.rows=[replace(r,unread=False) for r in driver.rows]
            clock.advance(7198)
            cycle()
            clock.advance(7200)
            cycle()
            clock.advance(7200)
            cycle()
            jobs=store.all_jobs()
            sequences=[j['sequence'] for j in jobs if j['conversation']=='a' and j['status']=='sent' and j['campaign_id']]
            cancelled=[j['sequence'] for j in jobs if j['conversation']=='b' and j['status']=='sent' and j['campaign_id']]==[1]
            return {'ok':default_all and sequences==[1,2,3] and cancelled and 'outside' not in driver.submits,
                    'mode':'合成界面与虚拟时钟；未连接手机或 Codex；不代表积目已适配',
                    'default_all_existing':default_all,'daily_subset_excludes_other_contacts':'outside' not in driver.submits,
                    'unanswered_sequences':sequences,'reply_cancels_followups':cancelled,
                    'state':service.status()['state']}
        finally:
            store.close()
