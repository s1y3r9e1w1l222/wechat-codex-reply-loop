import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_autoreply.config import Config
from wechat_autoreply.models import Message
from wechat_autoreply.provider import CodexProvider, FakeProvider, ProviderError, build_context, normalize_reply, run_codex


class ProviderTests(unittest.TestCase):
    def test_stdin_backpressure_obeys_deadline_and_cancellation(self):
        original_popen = subprocess.Popen
        for cancel_requested in (False, True):
            with self.subTest(cancel=cancel_requested), tempfile.TemporaryDirectory() as directory:
                captured, errors = [], []
                cancel = threading.Event()
                def popen(*args, **kwargs):
                    child = original_popen(*args, **kwargs)
                    captured.append(child)
                    return child
                def invoke():
                    try:
                        run_codex([sys.executable,'-c','import time; time.sleep(10)',
                                   '-o',str(Path(directory)/'reply.json')],
                                  '字'*100000,directory,3 if cancel_requested else .15,cancel)
                    except ProviderError as error:
                        errors.append(error.code)
                with patch('wechat_autoreply.provider.subprocess.Popen',side_effect=popen):
                    worker = threading.Thread(target=invoke,daemon=True)
                    worker.start()
                    if cancel_requested:
                        time.sleep(.15)
                        cancel.set()
                    worker.join(timeout=1.5)
                    timely = not worker.is_alive()
                    for child in captured:
                        if child.poll() is None:
                            os.killpg(child.pid,signal.SIGKILL)
                    worker.join(timeout=2)
                self.assertTrue(timely,'blocked stdin bypassed deadline/cancellation')
                self.assertEqual(errors,['cancelled' if cancel_requested else 'codex_timeout'])

    def test_oversized_persona_is_rejected_before_launch(self):
        def runner(*args):
            self.fail('oversized persona reached subprocess')
        provider = CodexProvider(Config(),runner=runner,executable='/trusted/codex')
        with self.assertRaisesRegex(ProviderError,'persona_too_large'):
            provider.generate([Message('incoming','你好')],persona='字'*4001)

    def test_context_is_bounded_without_truncating_new_input(self):
        old = [Message('incoming', str(i) * 200) for i in range(25)]
        new = [Message('incoming', '新的问题')]
        ctx = build_context(old + new, new, Config())
        self.assertLessEqual(len(ctx), 20)
        self.assertLessEqual(sum(len(m['content']) for m in ctx), 8000)
        self.assertEqual(ctx[-1]['content'], '新的问题')
        with self.assertRaises(ProviderError):
            build_context([Message('incoming', '字' * 8001)], [Message('incoming', '字' * 8001)], Config())
        with self.assertRaises(ProviderError):
            build_context([Message('incoming', '图片,', 'image')], [Message('incoming', '图片,', 'image')], Config())

    def test_codex_uses_stdin_ephemeral_schema_and_no_phone_tools(self):
        seen = {}
        def runner(argv, prompt, directory, timeout, cancel):
            seen.update(argv=argv, prompt=prompt)
            schema_path = Path(argv[argv.index('--output-schema') + 1])
            self.assertEqual(json.loads(schema_path.read_text())['required'], ['reply'])
            self.assertNotIn('私人聊天内容', ' '.join(argv))
            return json.dumps({'reply': '你好' + chr(10) + '收到'}, ensure_ascii=False)
        provider = CodexProvider(Config(), runner=runner, executable='/trusted/codex')
        result = provider.generate([Message('incoming', '私人聊天内容')])
        self.assertEqual(result, '你好 收到')
        self.assertIn('--ephemeral', seen['argv'])
        self.assertIn('--ignore-user-config', seen['argv'])
        self.assertIn('shell_tool', seen['argv'])
        self.assertIn('plugins', seen['argv'])
        self.assertIn('respect_system_proxy', seen['argv'])
        self.assertIn('私人聊天内容', seen['prompt'])

    def test_bad_or_oversized_outputs_never_become_replies(self):
        for output in ['', 'not json', '{"reply":""}', json.dumps({'reply': '字' * 301}), '{"reply":"hi","tool":"send"}']:
            p = CodexProvider(Config(), runner=lambda *args: output, executable='/trusted/codex')
            with self.assertRaises(ProviderError, msg=output[:20]):
                p.generate([Message('incoming', '你好')])

    def test_fake_provider_is_explicitly_marked_offline(self):
        self.assertIn('离线', FakeProvider().generate([Message('incoming', '你好')]))

    def test_control_characters_are_rejected(self):
        with self.assertRaises(ProviderError):
            normalize_reply('你好\x00', 300)


if __name__ == '__main__':
    unittest.main()
