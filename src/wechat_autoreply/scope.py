"""Per-App daily scope and honest, bounded candidate discovery."""
import json
import uuid
from dataclasses import asdict
from datetime import datetime, date as Date


def validate_keys(selected):
    if (not isinstance(selected, (list, tuple)) or len(selected) > 10000 or
            any(not isinstance(k, str) or not k or len(k) > 200 or '\x00' in k for k in selected) or
            len(set(selected)) != len(selected)):
        raise ValueError('invalid_contact_selection')


class ScopePolicy:
    def __init__(self, store, config, timezone, clock):
        self.store, self.config, self.timezone, self.clock = store, config, timezone, clock

    def today(self):
        return datetime.fromtimestamp(self.clock(), self.timezone).date().isoformat()

    def _date(self, value):
        if not isinstance(value, str) or Date.fromisoformat(value).isoformat() != value:
            raise ValueError('invalid_date')
        return value

    def _editable(self):
        if self.config.app_id == 'wechat':
            raise ValueError('wechat_uses_static_allowlist')

    def set_daily(self, date, selected):
        self._editable()
        self._date(date)
        validate_keys(selected)
        with self.store.transaction():
            self.store.db.execute('INSERT OR REPLACE INTO scope_policies VALUES (?,?,?)',
                                  (self.store.app_id,date,json.dumps(list(selected))))
            self.store.set_state('policy_revision', self.store.state('policy_revision',0)+1)
        return self.describe()

    def delete_daily(self, date):
        self._editable()
        self._date(date)
        with self.store.transaction():
            self.store.db.execute('DELETE FROM scope_policies WHERE app_id=? AND date=?', (self.store.app_id,date))
            self.store.set_state('policy_revision', self.store.state('policy_revision',0)+1)

    def stopped(self, key):
        row = self.store.db.execute('SELECT stopped FROM contact_controls WHERE app_id=? AND conversation=?',
                                    (self.store.app_id,key)).fetchone()
        return bool(row and row[0])

    def allows(self, key, *, date=None):
        if self.stopped(key):
            return False
        if self.config.app_id == 'wechat':
            return key in self.config.allowed
        row = self.store.db.execute('SELECT selected FROM scope_policies WHERE app_id=? AND date=?',
                                   (self.store.app_id,date or self.today())).fetchone()
        return row is None or key in json.loads(row[0])

    def version(self):
        # An integer changes at either calendar rollover or a policy edit.
        revision=self.store.state('policy_revision',0)
        if self.config.app_id=='wechat':
            selected=sorted(self.config.allowed)
            if self.store.state('static_scope_contacts')!=selected:
                with self.store.transaction():
                    revision+=1
                    self.store.set_state('policy_revision',revision)
                    self.store.set_state('static_scope_contacts',selected)
        return Date.fromisoformat(self.today()).toordinal()*1000000000 + revision

    def describe(self):
        if self.config.app_id == 'wechat':
            return {'mode':'static', 'selected':list(self.config.allowed), 'version':self.version()}
        row = self.store.db.execute('SELECT selected FROM scope_policies WHERE app_id=? AND date=?',
                                   (self.store.app_id,self.today())).fetchone()
        return {'date':self.today(), 'mode':'all_existing' if row is None else 'selected',
                'selected':None if row is None else json.loads(row[0]), 'version':self.version()}


class DiscoveryCatalog:
    def __init__(self, store, policy=None):
        self.store, self.policy = store, policy

    def save(self, scan, now):
        sid = uuid.uuid4().hex
        rows = [asdict(c) for c in scan.candidates]
        if len({row['key'] for row in rows}) != len(rows):
            raise ValueError('duplicate_conversation_key')
        with self.store.transaction():
            payload={'candidates':rows,'policy_version':self.policy.version() if self.policy else None}
            self.store.db.execute('INSERT INTO discovery_snapshots VALUES (?,?,?,?,?)',
                                  (self.store.app_id,sid,now,int(scan.complete),json.dumps(payload,ensure_ascii=False)))
        return sid

    def get(self, snapshot_id):
        row = self.store.db.execute('SELECT * FROM discovery_snapshots WHERE app_id=? AND id=?',
                                   (self.store.app_id,snapshot_id)).fetchone()
        if row is None:
            raise ValueError('unknown_discovery')
        payload=json.loads(row['rows'])
        if isinstance(payload,list):
            return dict(row,rows=payload,policy_version=None)
        return dict(row,rows=payload['candidates'],policy_version=payload['policy_version'])

    def resolve(self, snapshot_id, selected, policy, now):
        snap = self.get(snapshot_id)
        if now < snap['created'] or now-snap['created'] > 300:
            raise ValueError('discovery_expired')
        if selected is None and not snap['complete']:
            raise ValueError('discovery_incomplete')
        if selected is None and snap['policy_version']!=policy.version():
            raise ValueError('discovery_policy_changed')
        if selected is not None:
            validate_keys(selected)
            if set(selected) - {r['key'] for r in snap['rows']}:
                raise ValueError('contact_not_in_discovery')
        rows = sorted(snap['rows'], key=lambda r: r['position'], reverse=True)
        return tuple(r['key'] for r in rows if (selected is None or r['key'] in selected)
                     and r['muted'] is False and policy.allows(r['key']))
