import json
import hashlib
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from datetime import datetime
from dataclasses import asdict

from .control import ControlServer, InstanceLease
from .models import messages_from, messages_json
from .scheduler import Scheduler, align_messages
from .provider import build_context, ProviderError
from .sending import Sender
from .scope import ScopePolicy, DiscoveryCatalog
from .campaigns import ClockGuard, CampaignManager
from .generation import GenerationRequest, GenerationResult
from .adapters.base import ScanResult


class Service:
    def __init__(self, config, store, driver, provider, sender=None, executor=None,
                 clock=time.time, mode='preview', monotonic=time.monotonic):
        self.config, self.store, self.driver, self.provider = config, store, driver, provider
        if config.app_id != store.app_id or getattr(driver,'app_id',store.app_id) != store.app_id:
            raise ValueError('app_state_mismatch')
        self.scope = ScopePolicy(store,config,datetime.now().astimezone().tzinfo,clock)
        self.clock_guard = ClockGuard(store,clock,monotonic)
        self.campaigns = CampaignManager(store,self.scope,self.clock_guard)
        self.catalog = DiscoveryCatalog(store,self.scope)
        self.discovery_id, self.last_rows = None, ()
        self._preview_seen = set()
        cal = getattr(driver,'calibration',None)
        self.preview_tokens = {}
        for key in config.allowed:
            identity = {'signature':cal.profile_signatures.get(key),
                        'probe':cal.identity_probes.get(key),
                        'group':key in cal.group_keys,
                        'labels':[label for label,value in cal.identities.items() if value==key]} if cal else None
            self.preview_tokens[key] = key if identity is None else key + ':' + hashlib.sha256(
                json.dumps(identity,sort_keys=True).encode()).hexdigest()
        if hasattr(driver,'preview_requirements'):
            self.preview_tokens = {token:token for token in driver.preview_requirements(config)}
        if mode=='send' and config.app_id!='wechat' and not getattr(getattr(driver,'capabilities',None),'verified',False):
            raise ValueError('adapter_unverified')
        if (mode == 'send' and not getattr(driver,'offline_demo',False) and
                not set(self.preview_tokens.values()) <= set(store.state('previewed_contacts',[]))):
            raise ValueError('首次启用或新增联系人前，请先 run 预览并查看 status，完成后重新 run --send')
        self.clock, self.mode = clock, mode
        self.generation = int(store.state('generation', 0)) + 1
        store.set_state('generation', self.generation)
        self.cancellation, self.stopping = threading.Event(), threading.Event()
        self.recovery_attempts = [a for a in store.uncertain_attempts()+store.manual_confirmation_attempts()
                                  if self.scope.allows(store.job(a['job_id'])['conversation'])]
        self.state = 'recovering' if self.recovery_attempts else 'monitoring'
        self.last_error = ''
        self.scheduler = Scheduler(store, config.allowed,scope=self.scope,campaigns=self.campaigns)
        self.sender = sender or Sender(store, driver, config.allowed, mode,
                                       generation=lambda: self.generation,scope=self.scope,
                                       campaigns=self.campaigns,clock=clock)
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
                  'app_id':self.config.app_id,
                  'adapter_verified':getattr(getattr(self.driver,'capabilities',None),'verified',False),
                  'timezone':str(self.scope.timezone),'daily_scope':self.scope.describe(),
                  'discovery_id':self.discovery_id,'campaigns':self.campaigns.list(),
                  'campaign_counts':self.campaigns.counts(),
                  'clock_trusted':self.clock_guard.trusted(),
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
        if request.get('expected_app_id',self.config.app_id)!=self.config.app_id:
            return {'ok':False,'error':'app_state_mismatch'}
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
        self.clock_guard.resume()
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
        if request.get('expected_app_id',self.config.app_id)!=self.config.app_id:
            raise ValueError('app_state_mismatch')
        fields = {'status':(), 'pause':(), 'resume':(), 'stop':(), 'cleanup':(),
                  'resolve':('job_id','outcome'), 'retry':('job_id',), 'rebaseline':('conversation',),
                  'discover':('full',), 'scope_get':(), 'scope_set':('date','selected'),
                  'scope_delete':('date',), 'campaign_start':('request_id','snapshot_id','selected','instruction'),
                  'campaign_list':('limit','before','active'), 'campaign_cancel':('campaign_id',), 'contact_unblock':('conversation',)}
        command = request.get('command')
        if not isinstance(command,str) or command not in fields:
            raise ValueError('未知控制命令')
        if set(request) - {'command','expected_app_id',*fields[command]}:
            raise ValueError('未知控制字段')
        command = request['command']
        if command == 'status':
            return self._publish_status()
        if command == 'discover':
            if not isinstance(request.get('full',False),bool):
                raise ValueError('invalid_full_flag')
            scan = self.driver.scan(threading.Event(),full=request.get('full',False))
            if not isinstance(scan,ScanResult):
                raise ValueError('adapter_discovery_not_supported')
            self.last_rows = scan.candidates
            self.discovery_id = self.catalog.save(scan,self.clock())
            return {'ok':True,'snapshot_id':self.discovery_id,'complete':scan.complete,
                    'coverage':scan.coverage,'candidates':[asdict(row) for row in scan.candidates]}
        if command=='scope_get':
            return {'ok':True,**self.scope.describe()}
        if command in ('scope_set','scope_delete'):
            if command=='scope_set':
                self.scope.set_daily(request.get('date'),request.get('selected'))
            else:
                self.scope.delete_daily(request.get('date'))
            self._apply_scope()
            return self._publish_status()
        if command=='campaign_start':
            rid=request.get('request_id')
            if not isinstance(rid,str) or not rid or len(rid)>128:
                raise ValueError('invalid_request_id')
            digest=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            cached=self.store.state('manual_control:'+rid)
            if cached:
                if cached['digest']!=digest:
                    raise ValueError('request_id_conflict')
                return cached['result']
            with self.store.transaction():
                targets = self.catalog.resolve(request.get('snapshot_id'),request.get('selected'),self.scope,self.clock())
                result = {'ok':True,**self.campaigns.start(rid,targets,request.get('instruction',''))}
                self.store.set_state('manual_control:'+rid,{'digest':digest,'result':result,'created':self.clock()})
            return result
        if command=='campaign_list':
            limit=request.get('limit',100)
            rows=self.campaigns.list(limit,request.get('before'),request.get('active',False))
            return {'ok':True,'campaigns':rows,'next_before':rows[-1]['id'] if len(rows)==limit else None,
                    'counts':self.campaigns.counts()}
        if command=='campaign_cancel':
            if type(request.get('campaign_id')) is not int:
                raise ValueError('invalid_campaign_id')
            self.campaigns.cancel(request['campaign_id'],'manual_cancel')
            return self._publish_status()
        if command=='contact_unblock':
            key = request.get('conversation')
            if not isinstance(key,str) or not key or len(key)>200:
                raise ValueError('invalid_conversation')
            self.campaigns.unblock_contact(key)
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
            if job['campaign_id'] is not None:
                attempts=self.store.attempts_for_job(job['id'])
                with self.store.transaction():
                    if outcome=='sent' and attempts:
                        self.store.finish_attempt(attempts[-1]['id'],'sent','manual_confirmation',
                                                  on_confirmed=self.campaigns.on_confirmed)
                        self.store.update_conversation(job['conversation'],blocked='manual_confirmation_readback')
                    else:
                        self.store.update_job(job['id'],status=outcome,reason='manual_resolution')
                        self.campaigns.end_for_conversation(job['conversation'],'manual_resolution')
                        self.store.update_conversation(job['conversation'],blocked='rebaseline_required')
                if outcome=='sent' and attempts:
                    self.recovery_attempts=[a for a in self.recovery_attempts if a['job_id']!=job['id']]
                    self.recovery_attempts.append(self.store.attempt(attempts[-1]['id']))
                return self._publish_status()
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
            if not self.scope.allows(key):
                raise ValueError('联系人不在允许名单')
            if any(j['conversation'] == key and j['status'] == 'uncertain' for j in self.store.all_jobs()):
                raise ValueError('请先核对并 resolve 不确定的发送')
            manual_cancel=threading.Event()
            try:
                snap = self.driver.read_chat(key, manual_cancel)
                if (snap.key != key or not snap.identity_verified or not snap.at_latest or
                        snap.draft or snap.muted is not False):
                    raise ValueError('会话仍无法重新校准')
                self.store.ensure_conversation(key)
                conv = self.store.conversation(key)
                if old and old['campaign_id'] is not None:
                    campaign=self.campaigns.get(old['campaign_id'])
                    if (not campaign or campaign['status']!='active' or campaign['next_sequence']!=old['sequence'] or
                            self.store.attempts_for_job(old['id']) or self.store.unresolved_reply(key)):
                        raise ValueError('proactive_retry_not_safe')
                    with self.store.transaction():
                        self.store.update_conversation(key,blocked='',version=conv['version']+1)
                        self.store.update_job(old['id'],status='pending',draft=None,needs_refresh=1,
                                              version=conv['version']+1,generation=self.generation,reason='manual_retry')
                        self._refresh_proactive(self.store.job(old['id']),snap)
                    return self._publish_status()
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
                    if command=='rebaseline':
                        self.campaigns.end_for_conversation(key,'manual_rebaseline')
                        self.recovery_attempts=[a for a in self.recovery_attempts if self.store.job(a['job_id'])['conversation']!=key]
                        self.store.db.execute("UPDATE send_attempts SET reason='manual_rebaseline' WHERE app_id=? AND reason='manual_confirmation' AND job_id IN (SELECT id FROM reply_jobs WHERE app_id=? AND conversation=?)",
                                              (self.store.app_id,self.store.app_id,key))
                        owned=self.store.state('owned_draft')
                        if owned and owned['conversation']==key:
                            self.store.set_state('owned_draft',None)
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
                self.driver.return_to_list(manual_cancel)
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
                result = future.result()
                if isinstance(result,GenerationResult):
                    if result.disposition=='stop_contact':
                        self.campaigns.stop_contact(job['conversation'],result.reason)
                        continue
                    if result.disposition=='needs_attention':
                        self.scheduler.block(job['conversation'],'generation_needs_attention')
                        continue
                    result = result.reply
                self.store.set_draft(job_id, version, generation, result)
            except Exception as e:
                self.store.update_job(job_id, status='failed', reason=getattr(e,'code','generation_failed'))

    def _apply_scope(self):
        for campaign in self.campaigns.active():
            if campaign['status']=='active' and not self.scope.allows(campaign['conversation']):
                self.campaigns.cancel(campaign['id'],'out_of_scope')
        for job in self.store.active_jobs():
            if not self.scope.allows(job['conversation']):
                self.store.update_job(job['id'],status='skipped',draft=None,reason='out_of_scope')

    def _refresh_proactive(self, job, snap):
        key = job['conversation']
        if snap.key!=key or not snap.identity_verified or not snap.at_latest or snap.draft:
            self.scheduler.block(key,'identity_tail_or_manual_draft')
            self.campaigns.end_for_conversation(key,'conversation_requires_attention')
            return
        if snap.muted is not False:
            self.campaigns.end_for_conversation(key,'muted_or_unknown')
            return
        previous = messages_from(self.store.conversation(key)['snapshot'])
        if not previous and snap.initial_unread is None:
            self.scheduler.block(key,'proactive_initial_boundary_unknown')
            self.campaigns.end_for_conversation(key,'proactive_initial_boundary_unknown')
            return
        if previous:
            aligned = align_messages(previous,snap.messages)
            if aligned.ambiguous:
                self.scheduler.block(key,'proactive_context_ambiguous')
                return
            if aligned.appended:
                self.scheduler.observe_chat(snap)
                return
        elif (snap.initial_unread or any(c.key==key and c.unread for c in self.last_rows)):
            self.campaigns.observe_incoming(key)
            self.scheduler.observe_chat(snap)
            return
        with self.store.transaction():
            self.store.update_conversation(key,snapshot=messages_json(snap.messages))
            self.store.update_job(job['id'],messages=messages_json(snap.messages),needs_refresh=0,
                                  policy_version=self.scope.version())

    def tick(self):
        self._drain_commands()
        self._collect()
        if self.cancellation.is_set() or self.state not in ('monitoring','recovering'):
            return self._publish_status()
        if self.config.app_id=='wechat' and not self.config.allowed:
            self.last_error = 'configure_allowed_contacts_and_calibrate_first'
            return self._publish_status()
        try:
            if hasattr(self.sender,'cleanup_owned_draft'):
                self.sender.cleanup_owned_draft(self.cancellation)
            if self.state == 'recovering':
                attempt = self.recovery_attempts.pop(0)
                # User resolution may have superseded the startup recovery queue.
                current = self.store.job(attempt['job_id'])
                if current and (current['status']=='uncertain' or (current['status']=='sent' and attempt['reason']=='manual_confirmation')):
                    result = self.sender.reconcile(attempt,self.cancellation)
                    if result.status == 'sent' and result.snapshot:
                        self.scheduler.observe_chat(result.snapshot,own_send=True)
                    if result.status != 'sent' or result.reason == 'return_to_list_needs_attention':
                        self.last_error = result.reason or 'manual_confirmation_required'
                        self.state = 'needs_attention'
                        self.cancellation.set()
                if self.state == 'recovering' and not self.recovery_attempts:
                    self.state = 'monitoring'
                return self._publish_status()
            now = self.clock()
            self._apply_scope()
            if now >= self.next_scan:
                observed = self.driver.scan(self.cancellation)
                rows = observed.candidates if isinstance(observed,ScanResult) else observed
                self.last_rows = rows
                for row in rows:
                    if row.muted is not False:
                        self.campaigns.end_for_conversation(row.key,'muted_or_unknown')
                self.scheduler.enqueue_scan(rows)
                if isinstance(observed,ScanResult):
                    self._preview_seen.update(self.driver.preview_evidence(observed) & set(self.preview_tokens))
                else:
                    self._preview_seen.update(c.key for c in rows if c.identity_verified and c.key in self.preview_tokens)
                self.next_scan = self.clock() + self.config.poll_seconds
            self.campaigns.materialize_due(self.clock_guard.now())
            refresh = next((j for j in self.store.ready_jobs(now) if j['needs_refresh']), None)
            if refresh:
                snap = self.driver.read_chat(refresh['conversation'], self.cancellation)
                if refresh['kind']=='reply':
                    self.scheduler.observe_chat(snap)
                else:
                    self._refresh_proactive(refresh,snap)
                self.driver.return_to_list(self.cancellation)
            for job in self.store.ready_jobs(self.clock()):
                if len(self.futures) >= self.config.ai_workers:
                    break
                if job['status'] != 'pending' or job['needs_refresh'] or job['id'] in self.futures:
                    continue
                now = self.clock()
                if job['kind']=='reply' and now - job['updated'] < self.config.quiet_seconds and now - job['created'] < self.config.max_wait_seconds:
                    continue
                self.store.update_job(job['id'], status='generating', generation=self.generation)
                if hasattr(self.provider,'generate_task'):
                    campaign = self.campaigns.get(job['campaign_id']) if job['campaign_id'] else None
                    prior = tuple(j['draft'] for j in self.store.all_jobs() if j['campaign_id']==job['campaign_id']
                                  and j['status']=='sent') if campaign else ()
                    task = GenerationRequest(job['kind'],tuple(messages_from(job['messages'])),
                                             tuple(messages_from(job['new_messages'])),job['campaign_id'],job['sequence'],
                                             prior,campaign['instruction'] if campaign else '')
                    future = self.pool.submit(self.provider.generate_task,task,self.persona,self.config,cancel=self.cancellation)
                else:
                    if job['kind']!='reply':
                        self.store.update_job(job['id'],status='failed',reason='proactive_provider_not_supported')
                        continue
                    future = self.pool.submit(self.provider.generate,messages_from(job['messages']),self.persona,self.config,
                                              new_messages=messages_from(job['new_messages']),cancel=self.cancellation)
                self.futures[job['id']] = (future, job['version'], self.generation)
            head = self.store.head_job(self.clock())
            if head and head['status'] == 'draft_ready' and self.mode == 'send':
                self.sender.mode = self.mode
                result = self.sender.send(head, self.cancellation)
                if result.status == 'sent' and result.snapshot:
                    self.scheduler.observe_chat(result.snapshot,own_send=True)
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
