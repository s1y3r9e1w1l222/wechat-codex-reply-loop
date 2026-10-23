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
