"""Durable manual outreach rounds; never a second phone execution loop."""
import hashlib
import json
import time

from .scope import validate_keys


class ClockGuard:
    def __init__(self, store, wall=time.time, monotonic=time.monotonic):
        self.store, self.wall, self.monotonic = store, wall, monotonic
        self._wall, self._mono = wall(), monotonic()
        self._trusted = True
        if store.state('clock_initialized', False):
            self._rebase()
        store.set_state('clock_initialized', True)

    def _rebase(self):
        deadline = self.wall() + 7200
        with self.store.transaction():
            self.store.db.execute("UPDATE campaigns SET not_before=MAX(not_before,?) WHERE app_id=? AND status='active' AND next_sequence>1",
                                  (deadline,self.store.app_id))
            self.store.db.execute("UPDATE reply_jobs SET not_before=MAX(not_before,?) WHERE app_id=? AND kind='proactive_followup' AND status IN ('pending','generating','draft_ready')",
                                  (deadline,self.store.app_id))

    def now(self):
        wall, mono = self.wall(), self.monotonic()
        if abs((wall-self._wall)-(mono-self._mono)) > 5:
            self._trusted = False
        self._wall, self._mono = wall, mono
        return wall

    def trusted(self):
        self.now()
        return self._trusted

    def resume(self):
        self.now()
        if not self._trusted:
            self._rebase()
        self._wall, self._mono, self._trusted = self.wall(), self.monotonic(), True


class CampaignManager:
    def __init__(self, store, policy, clock_guard):
        self.store, self.policy, self.clock = store, policy, clock_guard

    def list(self, limit=100, before=None, active_only=False):
        if type(limit) is not int or not 1<=limit<=100 or (before is not None and (type(before) is not int or before<1)) or type(active_only) is not bool:
            raise ValueError('invalid_campaign_page')
        sql='SELECT id,app_id,conversation,status,next_sequence,confirmed_count,last_confirmed_at,not_before,reason,created,updated FROM campaigns WHERE app_id=?'
        args=[self.store.app_id]
        if before is not None:
            sql+=' AND id<?'
            args.append(before)
        if active_only:
            sql+=" AND status='active'"
        return [dict(r) for r in self.store.db.execute(sql+' ORDER BY id DESC LIMIT ?',(*args,limit))]

    def active(self):
        return [dict(r) for r in self.store.db.execute("SELECT id,conversation,status,next_sequence,not_before FROM campaigns WHERE app_id=? AND status='active' ORDER BY id",(self.store.app_id,))]

    def counts(self):
        return {r[0]:r[1] for r in self.store.db.execute('SELECT status,COUNT(*) FROM campaigns WHERE app_id=? GROUP BY status',(self.store.app_id,))}

    def get(self, campaign_id):
        row = self.store.db.execute('SELECT * FROM campaigns WHERE app_id=? AND id=?',
                                    (self.store.app_id,campaign_id)).fetchone()
        return dict(row) if row else None

    def start(self, request_id, targets, instruction=''):
        if not isinstance(request_id, str) or not request_id or len(request_id)>128:
            raise ValueError('invalid_request_id')
        validate_keys(targets)
        if not isinstance(instruction, str) or len(instruction)>4000 or len(instruction.encode())>16384:
            raise ValueError('invalid_instruction')
        if not targets:
            raise ValueError('no_targets')
        digest = hashlib.sha256(json.dumps([sorted(targets),instruction],ensure_ascii=False).encode()).hexdigest()
        old = self.store.db.execute('SELECT digest,result FROM manual_requests WHERE app_id=? AND id=?',
                                    (self.store.app_id,request_id)).fetchone()
        if old:
            if old['digest'] != digest:
                raise ValueError('request_id_conflict')
            return json.loads(old['result'])
        result = {'created':{}, 'existing':{}, 'skipped':{}}
        with self.store.transaction():
            for key in targets:
                self.store.ensure_conversation(key)
                conv = self.store.conversation(key)
                if self.store.unresolved_reply(key):
                    result['skipped'][key]='unresolved_reply'
                    continue
                if not self.policy.allows(key) or conv['blocked']:
                    result['skipped'][key] = 'out_of_scope_or_blocked'
                    continue
                existing = self.store.db.execute("SELECT id FROM campaigns WHERE app_id=? AND conversation=? AND status='active'",(self.store.app_id,key)).fetchone()
                if existing:
                    result['existing'][key] = existing['id']
                    continue
                now=self.clock.now()
                cur = self.store.db.execute('INSERT INTO campaigns(app_id,conversation,not_before,instruction,created,updated) VALUES (?,?,?,?,?,?)',
                                            (self.store.app_id,key,now,instruction,now,now))
                result['created'][key] = cur.lastrowid
            self.store.db.execute('INSERT INTO manual_requests(app_id,id,digest,result,created) VALUES (?,?,?,?,?)',
                                  (self.store.app_id,request_id,digest,json.dumps(result),self.clock.now()))
        return result

    def cancel(self, campaign_id, reason):
        campaign = self.get(campaign_id)
        if campaign is None:
            raise ValueError('unknown_campaign')
        with self.store.transaction():
            self.store.db.execute("UPDATE campaigns SET status='ended',reason=?,updated=? WHERE app_id=? AND id=? AND status='active'",
                                  (reason,self.clock.now(),self.store.app_id,campaign_id))
            for job in self.store.active_jobs():
                if job['campaign_id'] == campaign_id:
                    self.store.update_job(job['id'], status='stale', draft=None, reason=reason)

    def end_for_conversation(self, key, reason):
        for campaign in self.active():
            if campaign['conversation']==key and campaign['status']=='active':
                self.cancel(campaign['id'], reason)

    def observe_incoming(self, key):
        self.end_for_conversation(key, 'incoming_reply')

    def observe_manual_outgoing(self, key):
        self.end_for_conversation(key, 'manual_outgoing')

    def stop_contact(self, key, reason):
        with self.store.transaction():
            self.store.db.execute('INSERT OR REPLACE INTO contact_controls VALUES (?,?,1,?)',
                                  (self.store.app_id,key,reason))
            self.end_for_conversation(key, 'stop_contact')
            for job in self.store.active_jobs():
                if job['conversation']==key:
                    self.store.update_job(job['id'], status='skipped',draft=None,reason='stop_contact')

    def unblock_contact(self, key):
        with self.store.transaction():
            self.store.db.execute('DELETE FROM contact_controls WHERE app_id=? AND conversation=?', (self.store.app_id,key))

    def materialize_due(self, now):
        jobs = []
        for campaign in self.active():
            if campaign['status'] != 'active':
                continue
            key = campaign['conversation']
            if self.store.unresolved_reply(key):
                self.cancel(campaign['id'],'unresolved_reply')
                continue
            if not self.policy.allows(key):
                self.cancel(campaign['id'], 'out_of_scope')
                continue
            if (not self.clock.trusted() or campaign['not_before'] > now or
                    self.store.conversation(key)['blocked'] or campaign['next_sequence'] > 3):
                continue
            exists = self.store.db.execute('SELECT 1 FROM reply_jobs WHERE app_id=? AND campaign_id=? AND sequence=?',
                                           (self.store.app_id,campaign['id'],campaign['next_sequence'])).fetchone()
            if exists:
                continue
            conv = self.store.conversation(key)
            job = self.store.create_job(key, conv['version'], [], [],
                                        kind='proactive_initial' if campaign['next_sequence']==1 else 'proactive_followup',
                                        campaign_id=campaign['id'],sequence=campaign['next_sequence'],
                                        not_before=campaign['not_before'],policy_version=self.policy.version())
            jobs.append(job['id'])
        return jobs

    def on_confirmed(self, job_id, confirmed_at):
        job = self.store.job(job_id)
        if not job or not job['campaign_id'] or job['status']!='sent':
            raise ValueError('round_confirmation_requires_sent_job')
        campaign = self.get(job['campaign_id'])
        if not campaign or campaign['status']!='active' or job['sequence']<campaign['next_sequence']:
            return
        if job['sequence']!=campaign['next_sequence'] or not 1<=job['sequence']<=3:
            raise ValueError('invalid_round_sequence')
        with self.store.transaction():
            self.store.db.execute("UPDATE campaigns SET confirmed_count=?,next_sequence=?,last_confirmed_at=?,not_before=?,status=?,updated=? WHERE app_id=? AND id=?",
                                  (job['sequence'],job['sequence']+1,confirmed_at,confirmed_at+7200,
                                   'completed' if job['sequence']==3 else 'active',confirmed_at,self.store.app_id,campaign['id']))
