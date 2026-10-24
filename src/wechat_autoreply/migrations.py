"""Transactional local-only upgrades of legacy checkpoints."""
import os
import sqlite3
import uuid
import time


def _migrate_v1(db, path):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > 1:
        raise ValueError('unsupported_schema_version')
    if version == 1:
        return
    has_data = any(db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone()
                   for table in ('conversations', 'reply_jobs', 'service_state'))
    if has_data:
        backup = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pre-v1.bak')
        fd = os.open(str(backup), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(str(backup))
        try:
            db.backup(target)
        finally:
            target.close()
    # Explicit BEGIN includes DDL in the rollback boundary on Python 3.9.
    with db:
        db.execute('BEGIN')
        db.execute('ALTER TABLE conversations RENAME TO legacy_conversations')
        db.execute("CREATE TABLE conversations (app_id TEXT NOT NULL, key TEXT NOT NULL, label TEXT, preview TEXT DEFAULT '', unread_count INTEGER, version INTEGER DEFAULT 0, snapshot TEXT DEFAULT '[]', blocked TEXT DEFAULT '', PRIMARY KEY(app_id,key))")
        db.execute("INSERT INTO conversations SELECT 'wechat',* FROM legacy_conversations")
        db.execute('DROP TABLE legacy_conversations')
        db.execute('ALTER TABLE service_state RENAME TO legacy_service_state')
        db.execute('CREATE TABLE service_state (app_id TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(app_id,key))')
        db.execute("INSERT INTO service_state SELECT 'wechat',* FROM legacy_service_state")
        db.execute('DROP TABLE legacy_service_state')
        for table in ('reply_jobs', 'send_attempts', 'message_observations'):
            db.execute("ALTER TABLE " + table + " ADD COLUMN app_id TEXT NOT NULL DEFAULT 'wechat'")
        for column in ("kind TEXT NOT NULL DEFAULT 'reply'", 'campaign_id INTEGER', 'sequence INTEGER',
                       'not_before REAL NOT NULL DEFAULT 0', 'policy_version INTEGER NOT NULL DEFAULT 0'):
            db.execute('ALTER TABLE reply_jobs ADD COLUMN ' + column)
        db.execute('CREATE UNIQUE INDEX round_sequence ON reply_jobs(app_id,campaign_id,sequence) WHERE campaign_id IS NOT NULL')
        db.execute('CREATE TABLE scope_policies(app_id TEXT,date TEXT,selected TEXT NOT NULL,PRIMARY KEY(app_id,date))')
        db.execute('CREATE TABLE discovery_snapshots(app_id TEXT,id TEXT,created REAL,complete INTEGER,rows TEXT,PRIMARY KEY(app_id,id))')
        db.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY AUTOINCREMENT,app_id TEXT NOT NULL,conversation TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',next_sequence INTEGER DEFAULT 1,confirmed_count INTEGER DEFAULT 0,last_confirmed_at REAL,not_before REAL NOT NULL,instruction TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '')")
        db.execute("CREATE UNIQUE INDEX one_active_round ON campaigns(app_id,conversation) WHERE status='active'")
        db.execute('CREATE TABLE manual_requests(app_id TEXT,id TEXT,digest TEXT,result TEXT,PRIMARY KEY(app_id,id))')
        db.execute("CREATE TABLE contact_controls(app_id TEXT,conversation TEXT,stopped INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',PRIMARY KEY(app_id,conversation))")
        db.execute('PRAGMA user_version=1')


def migrate(db,path):
    version=db.execute('PRAGMA user_version').fetchone()[0]
    if version>2:
        raise ValueError('unsupported_schema_version')
    if version==2:
        return
    if version==0:
        _migrate_v1(db,path)
    if db.execute('SELECT 1 FROM campaigns LIMIT 1').fetchone() or db.execute('SELECT 1 FROM manual_requests LIMIT 1').fetchone():
        backup=path.with_name(path.name+'.'+uuid.uuid4().hex+'.pre-v2.bak')
        fd=os.open(str(backup),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd)
        target=sqlite3.connect(str(backup))
        try:
            db.backup(target)
        finally:
            target.close()
    with db:
        db.execute('BEGIN')
        db.execute('ALTER TABLE campaigns ADD COLUMN created REAL NOT NULL DEFAULT 0')
        db.execute('ALTER TABLE campaigns ADD COLUMN updated REAL NOT NULL DEFAULT 0')
        db.execute('ALTER TABLE manual_requests ADD COLUMN created REAL NOT NULL DEFAULT 0')
        db.execute('UPDATE campaigns SET created=COALESCE((SELECT MIN(j.created) FROM reply_jobs j WHERE j.app_id=campaigns.app_id AND j.campaign_id=campaigns.id),last_confirmed_at,not_before)')
        db.execute('UPDATE campaigns SET updated=MAX(created,COALESCE((SELECT MAX(j.updated) FROM reply_jobs j WHERE j.app_id=campaigns.app_id AND j.campaign_id=campaigns.id),last_confirmed_at,created))')
        db.execute('UPDATE manual_requests SET created=?',(time.time(),))
        db.execute('PRAGMA user_version=2')
