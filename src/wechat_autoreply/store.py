"""Private SQLite checkpoints. Only the service thread mutates the database."""
import json
import fcntl
from contextlib import contextmanager
import os
import sqlite3
import time
from pathlib import Path

ACTIVE = ('pending', 'generating', 'draft_ready')


def private_dir(path):
    path = Path(path).expanduser().resolve()
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    info = path.stat()
    if not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('状态目录必须是当前用户独享的目录（权限 700）')
    return path


class Store:
    def __init__(self, path, app_id='wechat', clock=time.time):
        if app_id not in ('wechat', 'qingteng', 'jimu', 'soul'):
            raise ValueError('unknown_app')
        self.app_id, self.clock = app_id, clock
        self.path = Path(path).expanduser()
        private_dir(self.path.parent)
        if self.path.is_symlink():
            raise ValueError('数据库不能是符号链接')
        self._lease = self.path.with_name(self.path.name + '.lock').open('a')
        os.chmod(self.path.with_name(self.path.name + '.lock'), 0o600)
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self._lease.close()
            raise RuntimeError('状态数据库已由另一个服务使用') from e
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] > 2:
            self.close()
            raise ValueError('unsupported_schema_version')
        self._depth = 0
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS conversations (
            key TEXT PRIMARY KEY, label TEXT, preview TEXT DEFAULT '', unread_count INTEGER,
            version INTEGER DEFAULT 0, snapshot TEXT DEFAULT '[]', blocked TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS reply_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation TEXT NOT NULL,
            version INTEGER NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending', messages TEXT NOT NULL DEFAULT '[]',
            new_messages TEXT NOT NULL DEFAULT '[]', draft TEXT, reason TEXT DEFAULT '',
            needs_refresh INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS message_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation TEXT NOT NULL,
            message TEXT NOT NULL, observed REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS send_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
            status TEXT NOT NULL, draft TEXT NOT NULL, before_snapshot TEXT NOT NULL,
            created REAL NOT NULL, reason TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS service_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        from .migrations import migrate
        try:
            migrate(self.db, self.path)
        except Exception:
            self.close()
            raise
        with self.transaction():
            self.db.execute("UPDATE reply_jobs SET status='uncertain',reason='restart_after_sending' WHERE status='sending' AND app_id=?", (self.app_id,))
            self.db.execute("UPDATE send_attempts SET status='uncertain' WHERE status='sending' AND app_id=?", (self.app_id,))
            self.db.execute("UPDATE conversations SET blocked='uncertain' WHERE app_id=? AND key IN (SELECT conversation FROM reply_jobs WHERE status='uncertain' AND app_id=?)", (self.app_id,self.app_id))

    @contextmanager
    def transaction(self):
        outer = self._depth == 0
        if outer:
            self.db.execute('BEGIN IMMEDIATE')
        self._depth += 1
        try:
            yield
        except BaseException:
            if outer:
                self.db.rollback()
            raise
        else:
            if outer:
                self.db.commit()
        finally:
            self._depth -= 1

    def close(self):
        self.db.close()
        if not self._lease.closed:
            fcntl.flock(self._lease, fcntl.LOCK_UN)
            self._lease.close()

    def state(self, key, default=None):
        row = self.db.execute('SELECT value FROM service_state WHERE app_id=? AND key=?', (self.app_id,key)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO service_state VALUES (?,?,?)', (self.app_id,key,json.dumps(value)))

    def ensure_conversation(self, key, label=''):
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO conversations(app_id,key,label) VALUES (?,?,?)', (self.app_id,key,label or key))

    def conversation(self, key):
        row = self.db.execute('SELECT * FROM conversations WHERE app_id=? AND key=?', (self.app_id,key)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['snapshot'] = json.loads(result['snapshot'])
        return result

    def update_conversation(self, key, **values):
        allowed = {'label', 'preview', 'unread_count', 'version', 'snapshot', 'blocked'}
        if not values or set(values) - allowed:
            raise ValueError('invalid conversation fields')
        if 'snapshot' in values:
            values['snapshot'] = json.dumps(values['snapshot'], ensure_ascii=False)
        with self.transaction():
            self.db.execute('UPDATE conversations SET ' + ','.join(k + '=?' for k in values) + ' WHERE app_id=? AND key=?',
                            tuple(values.values()) + (self.app_id,key))

    @staticmethod
    def _job(row):
        if row is None:
            return None
        result = dict(row)
        for key in ('messages', 'new_messages'):
            result[key] = json.loads(result[key])
        return result

    def job(self, job_id):
        return self._job(self.db.execute('SELECT * FROM reply_jobs WHERE id=? AND app_id=?', (job_id,self.app_id)).fetchone())

    def all_jobs(self):
        return [self._job(r) for r in self.db.execute('SELECT * FROM reply_jobs WHERE app_id=? ORDER BY id', (self.app_id,))]

    def active_jobs(self):
        return [self._job(r) for r in self.db.execute("SELECT * FROM reply_jobs WHERE app_id=? AND status IN ('pending','generating','draft_ready') ORDER BY id", (self.app_id,))]

    def ready_jobs(self, now):
        return sorted((j for j in self.active_jobs() if j['not_before'] <= now),
                      key=lambda j: (j['kind'] != 'reply',j['id']))

    def unresolved_reply(self, key):
        return self.db.execute("SELECT 1 FROM reply_jobs WHERE app_id=? AND conversation=? AND kind='reply' AND status NOT IN ('sent','handled','abandoned','stale','skipped') LIMIT 1",
                               (self.app_id,key)).fetchone() is not None

    def head_job(self, now=None):
        jobs = self.ready_jobs(self.clock() if now is None else now)
        return jobs[0] if jobs else None

    def create_job(self, key, version, messages, new_messages, generation=0, *, kind='reply',
                   campaign_id=None, sequence=None, not_before=0, policy_version=0):
        self.ensure_conversation(key)
        now = self.clock()
        with self.transaction():
            cur = self.db.execute('''INSERT INTO reply_jobs
              (conversation,version,generation,messages,new_messages,needs_refresh,created,updated,app_id,kind,campaign_id,sequence,not_before,policy_version)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (key, version, generation, json.dumps(messages, ensure_ascii=False),
               json.dumps(new_messages, ensure_ascii=False), int(not bool(new_messages)), now, now,
               self.app_id,kind,campaign_id,sequence,not_before,policy_version))
        return self.job(cur.lastrowid)

    def update_job(self, job_id, **values):
        if self.job(job_id) is None:
            raise ValueError('unknown_job')
        allowed = {'version', 'generation', 'status', 'messages', 'new_messages',
                   'draft', 'reason', 'needs_refresh', 'updated', 'not_before', 'policy_version'}
        if not values or set(values) - allowed:
            raise ValueError('invalid job fields')
        for key in ('messages', 'new_messages'):
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        values.setdefault('updated', self.clock())
        with self.transaction():
            self.db.execute('UPDATE reply_jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=? AND app_id=?',
                            tuple(values.values()) + (job_id,self.app_id))

    def set_draft(self, job_id, version, generation, text):
        with self.transaction():
            cur = self.db.execute("""UPDATE reply_jobs SET draft=?,status='draft_ready',updated=?
                WHERE id=? AND version=? AND generation=? AND status='generating' AND app_id=?""",
                (text, self.clock(), job_id, version, generation,self.app_id))
        return cur.rowcount == 1

    def mark_sending(self, job_id, draft, before):
        with self.transaction():
            row = self.job(job_id)
            if not row or row['status'] not in ACTIVE:
                raise ValueError('任务不在可发送状态')
            if row['campaign_id'] is not None:
                campaign = self.db.execute("SELECT * FROM campaigns WHERE app_id=? AND id=? AND status='active'",
                                           (self.app_id,row['campaign_id'])).fetchone()
                exists = self.db.execute('SELECT 1 FROM send_attempts WHERE app_id=? AND job_id=?',
                                         (self.app_id,job_id)).fetchone()
                if (not campaign or not 1 <= row['sequence'] <= 3 or
                        campaign['next_sequence'] != row['sequence'] or exists):
                    raise ValueError('invalid_round_checkpoint')
            self.db.execute("UPDATE reply_jobs SET status='sending' WHERE id=?", (job_id,))
            cur = self.db.execute('INSERT INTO send_attempts(job_id,status,draft,before_snapshot,created,app_id) VALUES (?,?,?,?,?,?)',
                                  (job_id, 'sending', draft, json.dumps(before, ensure_ascii=False), self.clock(),self.app_id))
        return cur.lastrowid

    def finish_attempt(self, attempt_id, status, reason='', on_confirmed=None):
        if status not in ('sent', 'uncertain'):
            raise ValueError('invalid send result')
        attempt = self.attempt(attempt_id)
        if attempt is None:
            raise ValueError('unknown_attempt')
        with self.transaction():
            self.db.execute('UPDATE send_attempts SET status=?,reason=? WHERE id=?', (status, reason, attempt_id))
            self.db.execute('UPDATE reply_jobs SET status=?,reason=?,updated=? WHERE id=?',
                (status, reason, self.clock(), attempt['job_id']))
            if status == 'uncertain':
                self.db.execute("UPDATE conversations SET blocked='uncertain' WHERE app_id=? AND key=(SELECT conversation FROM reply_jobs WHERE id=? AND app_id=?)", (self.app_id,attempt['job_id'],self.app_id))
            else:
                # Rearm the list signal in the same durable sent checkpoint.
                # A crash before Scheduler accounts for after-send input must
                # not suppress an identical subsequent unread preview/count.
                self.db.execute("UPDATE conversations SET preview='' WHERE app_id=? AND key=(SELECT conversation FROM reply_jobs WHERE id=? AND app_id=?)", (self.app_id,attempt['job_id'],self.app_id))
                if on_confirmed and self.job(attempt['job_id'])['campaign_id'] is not None:
                    on_confirmed(attempt['job_id'], self.clock())

    def attempt(self, attempt_id):
        row = self.db.execute('SELECT * FROM send_attempts WHERE id=? AND app_id=?', (attempt_id,self.app_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['before_snapshot'] = json.loads(result['before_snapshot'])
        return result

    def uncertain_attempts(self):
        rows = self.db.execute("""SELECT a.id FROM send_attempts a JOIN reply_jobs j ON j.id=a.job_id
            WHERE a.status='uncertain' AND j.status='uncertain' AND a.app_id=j.app_id AND a.app_id=? ORDER BY a.id""", (self.app_id,))
        return [self.attempt(row['id']) for row in rows]

    def attempts_for_job(self, job_id):
        return [self.attempt(r[0]) for r in self.db.execute('SELECT id FROM send_attempts WHERE app_id=? AND job_id=? ORDER BY id',
                                                          (self.app_id,job_id))]

    def manual_confirmation_attempts(self):
        rows=self.db.execute("SELECT a.id FROM send_attempts a JOIN reply_jobs j ON a.job_id=j.id AND a.app_id=j.app_id JOIN campaigns c ON c.id=j.campaign_id AND c.app_id=j.app_id WHERE a.app_id=? AND a.status='sent' AND a.reason='manual_confirmation' AND c.status IN ('active','completed') ORDER BY a.id",
                             (self.app_id,))
        return [self.attempt(r[0]) for r in rows]

    def append_messages(self, key, messages):
        with self.transaction():
            self.db.executemany('INSERT INTO message_observations(conversation,message,observed,app_id) VALUES (?,?,?,?)',
                                [(key, json.dumps(m, ensure_ascii=False), self.clock(),self.app_id) for m in messages])

    def cleanup(self, days=7):
        cutoff = self.clock() - days * 86400
        with self.transaction():
            owned=self.state('owned_draft') or {}
            doomed=list(self.db.execute("""SELECT c.id FROM campaigns c WHERE c.app_id=? AND c.status!='active' AND c.updated<?
              AND NOT EXISTS(SELECT 1 FROM reply_jobs j WHERE j.app_id=c.app_id AND j.campaign_id=c.id
                AND (j.status IN ('sending','uncertain') OR j.id=? OR EXISTS(
                  SELECT 1 FROM send_attempts a WHERE a.app_id=j.app_id AND a.job_id=j.id AND a.reason='manual_confirmation')))
              """,(self.app_id,cutoff,owned.get('job_id',-1))))
            for row in doomed:
                self.db.execute('DELETE FROM send_attempts WHERE app_id=? AND job_id IN (SELECT id FROM reply_jobs WHERE app_id=? AND campaign_id=?)',
                                (self.app_id,self.app_id,row[0]))
                self.db.execute('DELETE FROM reply_jobs WHERE app_id=? AND campaign_id=?',(self.app_id,row[0]))
                self.db.execute('DELETE FROM campaigns WHERE app_id=? AND id=?',(self.app_id,row[0]))
            self.db.execute('DELETE FROM discovery_snapshots WHERE app_id=? AND created<?',(self.app_id,cutoff))
            self.db.execute('UPDATE manual_requests SET result=? WHERE app_id=? AND created<?',
                            (json.dumps({'expired':True}),self.app_id,cutoff))
            for row in list(self.db.execute("SELECT key,value FROM service_state WHERE app_id=? AND key LIKE 'manual_control:%'",(self.app_id,))):
                value=json.loads(row['value'])
                if value.get('created',0)<cutoff and not value['result'].get('expired'):
                    value['result']={'ok':True,'expired':True,'reason':'request_expired'}
                    self.set_state(row['key'],value)
            self.db.execute('DELETE FROM message_observations WHERE observed<? AND app_id=?', (cutoff,self.app_id))
            self.db.execute("DELETE FROM send_attempts WHERE created<? AND status='sent' AND app_id=? AND job_id IN (SELECT id FROM reply_jobs WHERE campaign_id IS NULL)", (cutoff,self.app_id))
            self.db.execute("DELETE FROM reply_jobs WHERE updated<? AND status IN ('sent','stale','abandoned','handled') AND app_id=? AND campaign_id IS NULL", (cutoff,self.app_id))
        self.db.execute('VACUUM')
