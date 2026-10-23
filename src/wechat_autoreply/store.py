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
    def __init__(self, path):
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
        with self.transaction():
            self.db.execute("UPDATE reply_jobs SET status='uncertain',reason='restart_after_sending' WHERE status='sending'")
            self.db.execute("UPDATE send_attempts SET status='uncertain' WHERE status='sending'")
            self.db.execute("UPDATE conversations SET blocked='uncertain' WHERE key IN (SELECT conversation FROM reply_jobs WHERE status='uncertain')")

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
        row = self.db.execute('SELECT value FROM service_state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO service_state VALUES (?,?)', (key, json.dumps(value)))

    def ensure_conversation(self, key, label=''):
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO conversations(key,label) VALUES (?,?)', (key, label or key))

    def conversation(self, key):
        row = self.db.execute('SELECT * FROM conversations WHERE key=?', (key,)).fetchone()
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
            self.db.execute('UPDATE conversations SET ' + ','.join(k + '=?' for k in values) + ' WHERE key=?',
                            tuple(values.values()) + (key,))

    @staticmethod
    def _job(row):
        if row is None:
            return None
        result = dict(row)
        for key in ('messages', 'new_messages'):
            result[key] = json.loads(result[key])
        return result

    def job(self, job_id):
        return self._job(self.db.execute('SELECT * FROM reply_jobs WHERE id=?', (job_id,)).fetchone())

    def all_jobs(self):
        return [self._job(r) for r in self.db.execute('SELECT * FROM reply_jobs ORDER BY id')]

    def active_jobs(self):
        return [self._job(r) for r in self.db.execute("SELECT * FROM reply_jobs WHERE status IN ('pending','generating','draft_ready') ORDER BY id")]

    def head_job(self):
        jobs = self.active_jobs()
        return jobs[0] if jobs else None

    def create_job(self, key, version, messages, new_messages, generation=0):
        self.ensure_conversation(key)
        now = time.time()
        with self.transaction():
            cur = self.db.execute('''INSERT INTO reply_jobs
              (conversation,version,generation,messages,new_messages,needs_refresh,created,updated)
              VALUES (?,?,?,?,?,?,?,?)''',
              (key, version, generation, json.dumps(messages, ensure_ascii=False),
               json.dumps(new_messages, ensure_ascii=False), int(not bool(new_messages)), now, now))
        return self.job(cur.lastrowid)

    def update_job(self, job_id, **values):
        allowed = {'version', 'generation', 'status', 'messages', 'new_messages',
                   'draft', 'reason', 'needs_refresh', 'updated'}
        if not values or set(values) - allowed:
            raise ValueError('invalid job fields')
        for key in ('messages', 'new_messages'):
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        values.setdefault('updated', time.time())
        with self.transaction():
            self.db.execute('UPDATE reply_jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                            tuple(values.values()) + (job_id,))

    def set_draft(self, job_id, version, generation, text):
        with self.transaction():
            cur = self.db.execute("""UPDATE reply_jobs SET draft=?,status='draft_ready',updated=?
                WHERE id=? AND version=? AND generation=? AND status='generating'""",
                (text, time.time(), job_id, version, generation))
        return cur.rowcount == 1

    def mark_sending(self, job_id, draft, before):
        with self.transaction():
            row = self.db.execute('SELECT status FROM reply_jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row[0] not in ACTIVE:
                raise ValueError('任务不在可发送状态')
            self.db.execute("UPDATE reply_jobs SET status='sending' WHERE id=?", (job_id,))
            cur = self.db.execute('INSERT INTO send_attempts(job_id,status,draft,before_snapshot,created) VALUES (?,?,?,?,?)',
                                  (job_id, 'sending', draft, json.dumps(before, ensure_ascii=False), time.time()))
        return cur.lastrowid

    def finish_attempt(self, attempt_id, status, reason=''):
        if status not in ('sent', 'uncertain'):
            raise ValueError('invalid send result')
        attempt = self.attempt(attempt_id)
        with self.transaction():
            self.db.execute('UPDATE send_attempts SET status=?,reason=? WHERE id=?', (status, reason, attempt_id))
            self.db.execute('UPDATE reply_jobs SET status=?,reason=?,updated=? WHERE id=?',
                            (status, reason, time.time(), attempt['job_id']))
            if status == 'uncertain':
                self.db.execute("UPDATE conversations SET blocked='uncertain' WHERE key=(SELECT conversation FROM reply_jobs WHERE id=?)", (attempt['job_id'],))
            else:
                # Rearm the list signal in the same durable sent checkpoint.
                # A crash before Scheduler accounts for after-send input must
                # not suppress an identical subsequent unread preview/count.
                self.db.execute("UPDATE conversations SET preview='' WHERE key=(SELECT conversation FROM reply_jobs WHERE id=?)", (attempt['job_id'],))

    def attempt(self, attempt_id):
        row = self.db.execute('SELECT * FROM send_attempts WHERE id=?', (attempt_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['before_snapshot'] = json.loads(result['before_snapshot'])
        return result

    def uncertain_attempts(self):
        rows = self.db.execute("""SELECT a.id FROM send_attempts a JOIN reply_jobs j ON j.id=a.job_id
            WHERE a.status='uncertain' AND j.status='uncertain' ORDER BY a.id""")
        return [self.attempt(row['id']) for row in rows]

    def append_messages(self, key, messages):
        with self.transaction():
            self.db.executemany('INSERT INTO message_observations(conversation,message,observed) VALUES (?,?,?)',
                                [(key, json.dumps(m, ensure_ascii=False), time.time()) for m in messages])

    def cleanup(self, days=7):
        cutoff = time.time() - days * 86400
        with self.transaction():
            self.db.execute('DELETE FROM message_observations WHERE observed<?', (cutoff,))
            self.db.execute("DELETE FROM send_attempts WHERE created<? AND status='sent'", (cutoff,))
            self.db.execute("DELETE FROM reply_jobs WHERE updated<? AND status IN ('sent','stale','abandoned','handled')", (cutoff,))
        self.db.execute('VACUUM')
