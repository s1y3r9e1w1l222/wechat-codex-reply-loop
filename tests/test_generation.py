import json
import unittest
from wechat_autoreply.generation import GenerationRequest, build_task_context
from wechat_autoreply.provider import CodexProvider, ProviderError
from wechat_autoreply.config import Config
from wechat_autoreply.models import Message


class GenerationTests(unittest.TestCase):
    def test_proactive_empty_context_is_not_fake_incoming(self):
        request = GenerationRequest('proactive_initial', (), campaign_id=1, sequence=1)
        context = build_task_context(request, Config())
        self.assertEqual(context['messages'], [])
        self.assertEqual(context['new_messages'], [])
        with self.assertRaises(ProviderError):
            build_task_context(GenerationRequest('reply', ()), Config())

    def test_followup_requires_round_and_preserves_prior_text(self):
        request = GenerationRequest('proactive_followup', (), campaign_id=1, sequence=2, sent_in_round=('first',))
        context = build_task_context(request, Config())
        self.assertEqual(context['sent_in_round'], ['first'])
        self.assertEqual(context['sequence'], 2)
        with self.assertRaises(ProviderError):
            build_task_context(GenerationRequest('proactive_followup', (), sequence=4), Config())

    def test_all_task_types_receive_same_persona_and_no_tools(self):
        prompts = []
        def runner(argv, prompt, directory, timeout, cancel):
            prompts.append(prompt)
            self.assertIn('--ignore-user-config', argv)
            self.assertIn('shell_tool', argv)
            return json.dumps({'disposition':'reply','reply':'hello','reason':''})
        provider = CodexProvider(Config(), runner=runner, executable='codex')
        messages = (Message('incoming','hi'),)
        for request in [GenerationRequest('reply',messages,messages),
                        GenerationRequest('proactive_initial',(),campaign_id=1,sequence=1),
                        GenerationRequest('proactive_followup',(),campaign_id=1,sequence=2,sent_in_round=('first',))]:
            self.assertEqual(provider.generate_task(request, 'same style').reply, 'hello')
        self.assertTrue(all('same style' in prompt for prompt in prompts))
        self.assertIn('first', prompts[-1])

    def test_stop_and_attention_do_not_become_sendable_text(self):
        request = GenerationRequest('proactive_initial',(),campaign_id=1,sequence=1)
        for disposition in ('stop_contact','needs_attention'):
            provider = CodexProvider(Config(), runner=lambda *args: json.dumps(
                {'disposition':disposition,'reply':None,'reason':'context'}), executable='codex')
            result = provider.generate_task(request)
            self.assertEqual(result.disposition, disposition)
            self.assertIsNone(result.reply)
        provider = CodexProvider(Config(),runner=lambda *args:'{"disposition":"reply","reply":null,"reason":""}',executable='codex')
        with self.assertRaises(ProviderError):
            provider.generate_task(request)
