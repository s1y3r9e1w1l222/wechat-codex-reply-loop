import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from wechat_autoreply.config import Config
from wechat_autoreply.store import Store
from wechat_autoreply.scope import ScopePolicy, DiscoveryCatalog
from wechat_autoreply.adapters.base import ScanResult
from wechat_autoreply.models import ConversationCandidate
from chat_support import FakeClock


class ScopeTests(unittest.TestCase):
    def test_soul_daily_scope_is_isolated_from_jimu(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'state.sqlite3'
            jimu = Store(path, app_id='jimu', clock=self.clock.wall)
            try:
                ScopePolicy(jimu, Config(app_id='jimu'), timezone.utc, self.clock.wall).set_daily('2024-01-01', ['jimu-only'])
            finally:
                jimu.close()
            soul = Store(path, app_id='soul', clock=self.clock.wall)
            try:
                policy = ScopePolicy(soul, Config(app_id='soul'), timezone.utc, self.clock.wall)
                self.assertTrue(policy.allows('existing-chat'))
                policy.set_daily('2024-01-01', ['soul-only'])
                self.assertFalse(policy.allows('jimu-only'))
                self.assertTrue(policy.allows('soul-only'))
                policy.set_daily('2024-01-01', [])
                self.assertFalse(policy.allows('soul-only'))
                policy.delete_daily('2024-01-01')
                self.assertTrue(policy.allows('existing-chat'))
            finally:
                soul.close()
            jimu = Store(path, app_id='jimu', clock=self.clock.wall)
            try:
                policy = ScopePolicy(jimu, Config(app_id='jimu'), timezone.utc, self.clock.wall)
                self.assertTrue(policy.allows('jimu-only'))
                self.assertFalse(policy.allows('soul-only'))
            finally:
                jimu.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock(1704067200)
        self.store = Store(Path(self.tmp.name)/'state.sqlite3', app_id='jimu', clock=self.clock.wall)
        self.addCleanup(self.store.close)
        self.config = Config()
        self.config.app_id = 'jimu'
        self.policy = ScopePolicy(self.store, self.config, timezone.utc, self.clock.wall)

    def test_unset_empty_selected_and_delete_have_different_effects(self):
        self.assertTrue(self.policy.allows('any'))
        self.policy.set_daily('2024-01-01', ['a'])
        self.assertTrue(self.policy.allows('a'))
        self.assertFalse(self.policy.allows('b'))
        self.policy.set_daily('2024-01-01', [])
        self.assertFalse(self.policy.allows('a'))
        self.policy.delete_daily('2024-01-01')
        self.assertTrue(self.policy.allows('b'))

    def test_new_day_defaults_to_all_and_changes_version(self):
        self.policy.set_daily('2024-01-01', [])
        old = self.policy.version()
        self.clock.advance(86400)
        self.assertTrue(self.policy.allows('a'))
        self.assertNotEqual(old, self.policy.version())

    def test_wechat_static_scope_cannot_be_replaced_by_daily_all(self):
        self.config.app_id = 'wechat'
        self.config.allowed = ['a']
        policy = ScopePolicy(self.store, self.config, timezone.utc, self.clock.wall)
        self.assertFalse(policy.allows('b'))
        self.assertTrue(policy.allows('a'))
        with self.assertRaisesRegex(ValueError, 'wechat_uses_static'):
            policy.set_daily('2024-01-01', ['b'])

    def test_partial_snapshot_cannot_start_all_but_subset_can(self):
        catalog = DiscoveryCatalog(self.store,self.policy)
        row = ConversationCandidate('a', 'A', 0, False, False, identity_verified=False)
        sid = catalog.save(ScanResult((row,), False, {}, 's', 'token'), self.clock.wall())
        with self.assertRaisesRegex(ValueError, 'discovery_incomplete'):
            catalog.resolve(sid, None, self.policy, self.clock.wall())
        self.assertEqual(catalog.resolve(sid, ['a'], self.policy, self.clock.wall()), ('a',))

    def test_stale_snapshot_and_latest_policy_are_checked(self):
        catalog = DiscoveryCatalog(self.store,self.policy)
        rows = (ConversationCandidate('a', 'A', 0, False, False),
                ConversationCandidate('m', 'M', 1, False, True))
        sid = catalog.save(ScanResult(rows, True, {}, 's', 't'), self.clock.wall())
        self.assertEqual(catalog.resolve(sid, None, self.policy, self.clock.wall()), ('a',))
        self.policy.set_daily('2024-01-01', [])
        with self.assertRaisesRegex(ValueError,'discovery_policy_changed'):
            catalog.resolve(sid, None, self.policy, self.clock.wall())
        self.assertEqual(catalog.resolve(sid, ['a'], self.policy, self.clock.wall()), ())
        self.clock.advance(301)
        with self.assertRaisesRegex(ValueError, 'discovery_expired'):
            catalog.resolve(sid, ['a'], self.policy, self.clock.wall())

    def test_policy_expansion_invalidates_implicit_all_snapshot(self):
        catalog=DiscoveryCatalog(self.store,self.policy)
        self.policy.set_daily('2024-01-01',['a'])
        rows=tuple(ConversationCandidate(k,k,float(i),False,False) for i,k in enumerate(('a','b')))
        sid=catalog.save(ScanResult(rows,True,{},'s','t'),self.clock.wall())
        self.assertEqual(catalog.resolve(sid,None,self.policy,self.clock.wall()),('a',))
        self.policy.delete_daily('2024-01-01')
        with self.assertRaisesRegex(ValueError,'discovery_policy_changed'):
            catalog.resolve(sid,None,self.policy,self.clock.wall())
        self.assertEqual(catalog.resolve(sid,['a'],self.policy,self.clock.wall()),('a',))

    def test_wechat_static_scope_change_invalidates_full_discovery(self):
        self.config.app_id='wechat'
        self.config.allowed=['a']
        catalog=DiscoveryCatalog(self.store,self.policy)
        rows=tuple(ConversationCandidate(k,k,float(i),False,False) for i,k in enumerate(('a','b')))
        sid=catalog.save(ScanResult(rows,True,{},'s','t'),self.clock.wall())
        self.config.allowed=['a','b']
        with self.assertRaisesRegex(ValueError,'discovery_policy_changed'):
            catalog.resolve(sid,None,self.policy,self.clock.wall())
