import json
import hashlib
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

from .control import ControlServer, InstanceLease
from .models import messages_from, messages_json
from .scheduler import Scheduler, align_messages
from .provider import build_context, ProviderError
from .sending import Sender


class Service:
    def __init__(self, config, store, driver, provider, sender=None, executor=None,
                 clock=time.time, mode='preview'):
        self.config, self.store, self.driver, self.provider = config, store, driver, provider
        self._preview_seen = set()
        cal = getattr(driver,'calibration',None)
        self.preview_tokens = {}
        for key in config.allowed:
            identity = {'signature':cal.profile_signatures.get(key),
                        'probe':cal.identity_probes.get(key),
                        'labels':[label for label,value in cal.identities.items() if value==key]} if cal else None
            self.preview_tokens[key] = key if identity is None else key + ':' + hashlib.sha256(
                json.dumps(identity,sort_keys=True).encode()).hexdigest()
        if (mode == 'send' and not getattr(driver,'offline_demo',False) and
                not set(self.preview_tokens.values()) <= set(store.state('previewed_contacts',[]))):
            raise ValueError('首次启用或新增联系人前，请先 run 预览并查看 status，完成后重新 run --send')
        self.clock, self.mode = clock, mode
        self.generation = int(store.state('generation', 0)) + 1
        store.set_state('generation', self.generation)
        self.cancellation, self.stopping = threading.Event(), threading.Event()
        self.recovery_attempts = [a for a in store.uncertain_attempts()
                                  if store.job(a['job_id'])['conversation'] in config.allowed]
        self.state = 'recovering' if self.recovery_attempts else 'monitoring'
        self.last_error = ''
        self.scheduler = Scheduler(store, config.allowed)
        self.sender = sender or Sender(store, driver, config.allowed, mode,
                                       generation=lambda: self.generation)
        self.pool = executor or ThreadPoolExecutor(max_workers=config.ai_workers, thread_name_prefix='wechat-codex')
        self.futures, self.commands = {}, queue.Queue()
        self.next_scan = 0
        self._status_lock, self._cached = threading.Lock(), {}
        if config.persona_path and Path(config.persona_path).stat().st_size > 16384:
            raise ValueError('回复口吻文件过大（最多 4000 字、16 KiB）')
        self.persona = Path(config.persona_path).read_text(encoding='utf-8') if config.persona_path else ''
        if len(self.persona) > 4000:
            raise ValueError('回复口吻文件过大（最多 4000 字）')
        with store.transaction():
            for job in store.active_jobs():
                store.update_job(job['id'], status='pending', generation=self.generation,
                                 draft=None, needs_refresh=1)
        self._publish_status()

    def _publish_status(self):
        if (self.mode == 'preview' and self.state == 'monitoring' and self._preview_seen and
                all(j['status']=='draft_ready' and not j['needs_refresh'] for j in self.store.active_jobs())):
            tokens = set(self.store.state('previewed_contacts',[]))
            tokens.update(self.preview_tokens[key] for key in self._preview_seen)
            self.store.set_state('previewed_contacts',sorted(tokens))
        result = {'ok':True, 'state':self.state, 'mode':self.mode,
                  'generation':self.generation, 'error':self.last_error,
                  'coverage':getattr(self.driver, 'coverage', {}),
                  'allowed_count':len(self.config.allowed),
                  'initial_preview_complete':set(self.preview_tokens.values()) <= set(self.store.state('previewed_contacts',[])),
                  'jobs':[dict(id=j['id'], conversation=j['conversation'], status=j['status'],
                               reason=j['reason'], draft=j['draft']) for j in self.store.all_jobs()[-100:]]}
        with self._status_lock:
            self._cached = result
        return result

    def status(self):
        with self._status_lock:
            return json.loads(json.dumps(self._cached))

    def request(self, request):
        command = request.get('command')
        if command == 'status':
            return self.status()
        if command in ('pause', 'stop'):
            self.cancellation.set()
        future = Future()
        self.commands.put((request, future))
        try:
            return future.result(timeout=2)
        except FutureTimeout:
            return {'ok':True, 'accepted':True, 'command':command, 'state':'queued'}

    def pause(self):
        self.cancellation.set()
        self.generation += 1
        self.store.set_state('generation', self.generation)
        with self.store.transaction():
            for job in self.store.active_jobs():
                self.store.update_job(job['id'], status='pending', draft=None,
                                      generation=self.generation, needs_refresh=1)
        self.state = 'paused'
        return self._publish_status()

    def resume(self):
        self.cancellation = threading.Event()
        self.state, self.last_error = ('recovering' if self.recovery_attempts else 'monitoring'), ''
        self.next_scan = 0
        return self._publish_status()

    def stop(self):
        self.pause()
        self.state = 'stopping'
        self.stopping.set()
        return self._publish_status()

    def handle_command(self, request):
        allowed_keys = {'command', 'job_id', 'outcome', 'conversation'}
        if set(request) - allowed_keys:
            raise ValueError('未知控制字段')
        command = request['command']
        if command == 'status':
            return self._publish_status()
        if command in ('pause','resume','stop'):
            return getattr(self, command)()
        if command == 'cleanup':
            self.store.cleanup(self.config.retain_days)
            return self._publish_status()
        if command == 'resolve':
            job = self.store.job(request.get('job_id'))
            outcome = request.get('outcome')
            if not job or job['status'] not in ('uncertain','ambiguous','failed','skipped') or outcome not in ('sent','handled','abandoned'):
                raise ValueError('任务或人工处理结果不正确')
            with self.store.transaction():
                self.store.update_job(job['id'], status=outcome, reason='manual_resolution')
                self.store.update_conversation(job['conversation'], blocked='rebaseline_required')
                if outcome == 'sent':
                    self.store.db.execute("UPDATE send_attempts SET status='sent',reason='manual_confirmation' WHERE job_id=?", (job['id'],))
            return self._publish_status()
        if command in ('retry','rebaseline'):
            old = self.store.job(request.get('job_id')) if command == 'retry' else None
            if command == 'retry' and (not old or old['status'] not in ('failed','ambiguous')):
                raise ValueError('不确定或已发送任务禁止重发')
            key = old['conversation'] if old else request.get('conversation')
            if key not in self.scheduler.allowed:
                raise ValueError('联系人不在允许名单')
            if any(j['conversation'] == key and j['status'] == 'uncertain' for j in self.store.all_jobs()):
                raise ValueError('请先核对并 resolve 不确定的发送')
            try:
                snap = self.driver.read_chat(key, self.cancellation)
                if (snap.key != key or not snap.identity_verified or not snap.at_latest or
                        snap.draft or snap.muted is not False):
                    raise ValueError('会话仍无法重新校准')
                self.store.ensure_conversation(key)
                conv = self.store.conversation(key)
                retry_input = []
                if command == 'retry':
                    previous = messages_from(old['messages'] if old['new_messages'] else conv['snapshot'])
                    aligned = align_messages(previous, snap.messages)
                    if not previous or aligned.ambiguous:
                        raise ValueError('输入边界无法重新确认；请人工处理后 rebaseline')
                    retry_input = messages_from(old['new_messages']) + list(aligned.appended)
                    try:
                        build_context(snap.messages, retry_input, self.config)
                    except ProviderError as error:
                        raise ValueError('输入边界无法重新确认；请人工处理后 rebaseline') from error
                with self.store.transaction():
                    for job in self.store.all_jobs():
                        if job['conversation'] != key:
                            continue
                        if job['status'] in ('pending','generating','draft_ready'):
                            self.store.update_job(job['id'],status='stale',draft=None,reason=command)
                        elif command == 'rebaseline' and job['status'] in ('ambiguous','failed','skipped'):
                            self.store.update_job(job['id'],status='handled',draft=None,reason='manual_rebaseline')
                    self.store.update_conversation(key, blocked='', preview='', version=conv['version']+1,
                                                   snapshot=messages_json(snap.messages))
                    if command == 'retry':
                        self.store.create_job(key, conv['version']+1, messages_json(snap.messages),
                                              messages_json(retry_input), self.generation)
            finally:
                self.driver.return_to_list(self.cancellation)
            return self._publish_status()
        raise ValueError('未知控制命令')

    def _drain_commands(self):
        while True:
            try:
                request, future = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                future.set_result(self.handle_command(request))
            except ValueError as error:
                future.set_result({'ok':False, 'error':str(error)})
            except Exception:
                future.set_result({'ok':False, 'error':'control_action_requires_attention'})

    def _collect(self):
        for job_id, (future, version, generation) in list(self.futures.items()):
            if not future.done():
                continue
            del self.futures[job_id]
            job = self.store.job(job_id)
            if generation != self.generation or self.cancellation.is_set() or not job or job['status'] != 'generating':
                continue
            try:
                self.store.set_draft(job_id, version, generation, future.result())
            except Exception as e:
                self.store.update_job(job_id, status='failed', reason=getattr(e,'code','generation_failed'))

    def tick(self):
        self._drain_commands()
        self._collect()
        if self.cancellation.is_set() or self.state not in ('monitoring','recovering'):
            return self._publish_status()
        if not self.config.allowed:
            self.last_error = 'configure_allowed_contacts_and_calibrate_first'
            return self._publish_status()
        try:
            if self.state == 'recovering':
                attempt = self.recovery_attempts.pop(0)
                # User resolution may have superseded the startup recovery queue.
                current = self.store.job(attempt['job_id'])
                if current and current['status'] == 'uncertain':
                    result = self.sender.reconcile(attempt,self.cancellation)
                    if result.status == 'sent' and result.snapshot:
                        self.scheduler.observe_chat(result.snapshot)
                    if result.status != 'sent' or result.reason == 'return_to_list_needs_attention':
                        self.last_error = result.reason or 'manual_confirmation_required'
                        self.state = 'needs_attention'
                        self.cancellation.set()
                if self.state == 'recovering' and not self.recovery_attempts:
                    self.state = 'monitoring'
                return self._publish_status()
            now = self.clock()
            if now >= self.next_scan:
                rows = self.driver.scan(self.cancellation)
                self.scheduler.enqueue_scan(rows)
                self._preview_seen.update(c.key for c in rows if c.identity_verified and c.key in self.preview_tokens)
                self.next_scan = self.clock() + self.config.poll_seconds
            refresh = next((j for j in self.store.active_jobs() if j['needs_refresh']), None)
            if refresh:
                snap = self.driver.read_chat(refresh['conversation'], self.cancellation)
                self.scheduler.observe_chat(snap)
                self.driver.return_to_list(self.cancellation)
            for job in self.store.active_jobs():
                if len(self.futures) >= self.config.ai_workers:
                    break
                if job['status'] != 'pending' or job['needs_refresh'] or job['id'] in self.futures:
                    continue
                now = self.clock()
                if now - job['updated'] < self.config.quiet_seconds and now - job['created'] < self.config.max_wait_seconds:
                    continue
                self.store.update_job(job['id'], status='generating', generation=self.generation)
                future = self.pool.submit(self.provider.generate, messages_from(job['messages']),
                                          self.persona, self.config,
                                          new_messages=messages_from(job['new_messages']), cancel=self.cancellation)
                self.futures[job['id']] = (future, job['version'], self.generation)
            head = self.store.head_job()
            if head and head['status'] == 'draft_ready' and self.mode == 'send':
                self.sender.mode = self.mode
                result = self.sender.send(head, self.cancellation)
                if result.status == 'sent' and result.snapshot:
                    self.scheduler.observe_chat(result.snapshot)
                if result.status == 'stale' and result.snapshot:
                    self.scheduler.observe_chat(result.snapshot)
                if result.status in ('uncertain','failed') or result.reason == 'return_to_list_needs_attention':
                    self.last_error = result.reason
                    self.cancellation.set()
                    self.state = 'needs_attention'
        except Exception as e:
            self.last_error = getattr(e,'code','phone_or_observation_requires_attention')
            self.cancellation.set()
            self.state = 'needs_attention'
        return self._publish_status()

    def run(self, once=False):
        runtime = getattr(getattr(self.driver, 'bridge', None), 'runtime', None)
        lease = InstanceLease(runtime.state_dir if runtime else self.store.path.parent)
        server = ControlServer(self.store.path.parent, self.request)
        try:
            server.start()
            while not self.stopping.is_set():
                self.tick()
                if once:
                    break
                self.stopping.wait(.1)
        finally:
            self.cancellation.set()
            self.state = 'stopped'
            self._publish_status()
            server.close()
            self.pool.shutdown(wait=False, cancel_futures=True)
            lease.close()
