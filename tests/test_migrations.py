import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from wechat_autoreply.store import Store


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.sqlite3'

    def test_app_keys_jobs_and_service_state_are_isolated(self):
        with_store = Store(self.path, app_id='wechat')
        with_store.ensure_conversation('same')
        job = with_store.create_job('same', 0, [], [])
        with_store.set_state('previewed_contacts', ['wechat-only'])
        with_store.close()
        other = Store(self.path, app_id='jimu')
        try:
            self.assertIsNone(other.conversation('same'))
            self.assertIsNone(other.job(job['id']))
            self.assertEqual(other.state('previewed_contacts', []), [])
            with self.assertRaises(ValueError):
                other.update_job(job['id'], status='sent')
            other.ensure_conversation('same', 'Other')
        finally:
            other.close()
        first = Store(self.path)
        try:
            self.assertEqual(first.conversation('same')['label'], 'same')
        finally:
            first.close()

    def test_due_queue_prioritizes_reply_without_future_head_blocking(self):
        store = Store(self.path)
        try:
            store.create_job('future', 0, [], [], kind='proactive_initial', not_before=7200)
            proactive = store.create_job('due', 0, [], [], kind='proactive_initial', not_before=0)
            reply = store.create_job('reply', 0, [], [])
            self.assertEqual(store.head_job(now=7199)['id'], reply['id'])
            store.update_job(reply['id'], status='handled')
            self.assertEqual(store.head_job(now=7199)['id'], proactive['id'])
        finally:
            store.close()

    def test_unknown_newer_schema_is_refused(self):
        db = sqlite3.connect(self.path)
        db.execute('PRAGMA user_version=999')
        db.close()
        with self.assertRaisesRegex(ValueError, 'schema'):
            Store(self.path)

    def test_v1_rounds_receive_timestamps_without_losing_state(self):
        from wechat_autoreply.migrations import migrate
        db=sqlite3.connect(self.path)
        db.executescript("""
        CREATE TABLE campaigns(id INTEGER PRIMARY KEY,app_id TEXT,conversation TEXT,status TEXT,next_sequence INTEGER,confirmed_count INTEGER,last_confirmed_at REAL,not_before REAL,instruction TEXT,reason TEXT);
        CREATE TABLE manual_requests(app_id TEXT,id TEXT,digest TEXT,result TEXT,PRIMARY KEY(app_id,id));
        CREATE TABLE reply_jobs(campaign_id INTEGER,app_id TEXT,created REAL,updated REAL);
        INSERT INTO campaigns VALUES(1,'jimu','a','completed',4,3,600,1000,'topic','');
        INSERT INTO reply_jobs VALUES(1,'jimu',500,600);
        PRAGMA user_version=1;
        """)
        try:
            migrate(db,self.path)
            row=db.execute('SELECT created,updated,status FROM campaigns').fetchone()
            self.assertEqual(row,(500,600,'completed'))
        finally:
            db.close()

    def test_migration_rolls_back_ddl_and_can_retry(self):
        from unittest.mock import patch
        from wechat_autoreply.migrations import migrate
        # Take a legacy schema, without depending on any Git history.
        self.test_legacy_rows_preserved_and_private_backup_created_once()
        backup = next(self.path.parent.glob('*.pre-v1.bak'))
        source = sqlite3.connect(backup)
        target = sqlite3.connect(self.path)
        source.backup(target)
        source.close()
        target.close()
        class Interrupted(sqlite3.Connection):
            fail = True
            def execute(self, sql, *args):
                if self.fail and sql.startswith('CREATE TABLE service_state'):
                    self.fail = False
                    raise RuntimeError('injected_migration_interruption')
                return super().execute(sql, *args)
        db = sqlite3.connect(self.path, factory=Interrupted)
        try:
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                migrate(db, self.path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT key FROM conversations').fetchone()[0], 'a')
            migrate(db, self.path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
        finally:
            db.close()

    def test_cleanup_preserves_round_evidence_and_other_app_history(self):
        store = Store(self.path, clock=lambda: 0)
        with store.transaction():
            store.db.execute("INSERT INTO campaigns(id,app_id,conversation,not_before) VALUES (1,'wechat','a',0)")
        job = store.create_job('a', 1, [], [], kind='proactive_initial', campaign_id=1, sequence=1)
        attempt = store.mark_sending(job['id'], 'one', [])
        store.finish_attempt(attempt, 'sent')
        store.clock = lambda: 1000000
        store.cleanup(1)
        self.assertIsNotNone(store.job(job['id']))
        self.assertIsNotNone(store.attempt(attempt))
        store.close()

    def test_legacy_rows_preserved_and_private_backup_created_once(self):
        db = sqlite3.connect(self.path)
        db.executescript("\n        CREATE TABLE IF NOT EXISTS conversations (\n            key TEXT PRIMARY KEY, label TEXT, preview TEXT DEFAULT '', unread_count INTEGER,\n            version INTEGER DEFAULT 0, snapshot TEXT DEFAULT '[]', blocked TEXT DEFAULT '');\n        CREATE TABLE IF NOT EXISTS reply_jobs (\n            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation TEXT NOT NULL,\n            version INTEGER NOT NULL, generation INTEGER NOT NULL DEFAULT 0,\n            status TEXT NOT NULL DEFAULT 'pending', messages TEXT NOT NULL DEFAULT '[]',\n            new_messages TEXT NOT NULL DEFAULT '[]', draft TEXT, reason TEXT DEFAULT '',\n            needs_refresh INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL);\n        CREATE TABLE IF NOT EXISTS message_observations (\n            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation TEXT NOT NULL,\n            message TEXT NOT NULL, observed REAL NOT NULL);\n        CREATE TABLE IF NOT EXISTS send_attempts (\n            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,\n            status TEXT NOT NULL, draft TEXT NOT NULL, before_snapshot TEXT NOT NULL,\n            created REAL NOT NULL, reason TEXT DEFAULT '');\n        CREATE TABLE IF NOT EXISTS service_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n        ")
        db.execute("INSERT INTO conversations(key,label) VALUES ('a','A')")
        db.execute("INSERT INTO reply_jobs(id,conversation,version,status,created,updated) VALUES (1,'a',1,'sending',0,0)")
        db.execute("INSERT INTO service_state VALUES ('previewed_contacts','[\"a\"]')")
        db.commit()
        db.close()
        store = Store(self.path)
        try:
            self.assertEqual(store.job(1)['status'], 'uncertain')
            self.assertEqual(store.state('previewed_contacts'), ['a'])
            backups = list(self.path.parent.glob('*.pre-v1.bak'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].stat().st_mode & 0o077, 0)
        finally:
            store.close()
