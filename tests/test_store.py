import os
import tempfile
import unittest
from pathlib import Path

from wechat_autoreply.store import Store


class StoreTests(unittest.TestCase):
    def test_second_process_store_cannot_mutate_running_send_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'db.sqlite3'
            first = Store(path)
            job = first.create_job('a',1,[],[])
            first.mark_sending(job['id'],'回复',[])
            try:
                with self.assertRaises(RuntimeError):
                    Store(path)
                self.assertEqual(first.job(job['id'])['status'],'sending')
            finally:
                first.close()

    def test_nested_queue_update_rolls_back_as_one_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / 'db.sqlite3')
            with self.assertRaises(RuntimeError):
                with s.transaction():
                    s.create_job('a', 1, [], [])
                    raise RuntimeError('crash before queue update completes')
            self.assertEqual(s.all_jobs(), [])
            self.assertIsNone(s.conversation('a'))
            s.close()

    def test_private_permissions_and_sending_survives_restart_as_uncertain(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state/db.sqlite3'
            store = Store(path)
            job = store.create_job('a', 1, [], [], generation=1)
            attempt = store.mark_sending(job['id'], '回复', [])
            store.close()
            store = Store(path)
            self.assertEqual(store.job(job['id'])['status'], 'uncertain')
            self.assertEqual(store.attempt(attempt)['status'], 'uncertain')
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            store.close()

    def test_stale_generation_cannot_install_draft(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / 'db.sqlite3')
            job = s.create_job('a', 1, [], [], generation=1)
            self.assertFalse(s.set_draft(job['id'], 1, 2, '旧回复'))
            self.assertIsNone(s.job(job['id'])['draft'])
            s.close()


if __name__ == '__main__':
    unittest.main()
