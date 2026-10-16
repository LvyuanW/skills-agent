import io
import json
import types
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from skills_agent.context.auxiliary import _messages
from skills_agent.providers import OpenAIProvider
from skills_agent.providers.deepseek import DeepSeekCounter
from tests.test_stream_openai import chunk


MARKER = '<｜image｜>'


def counter_with(recipe=None):
    counter = object.__new__(DeepSeekCounter)
    counter.recipe = recipe if recipe is not None else Mock()
    counter.version = 'v41'
    counter.encoding = Mock()
    counter.encoding.encode.return_value = [1, 2, 3]
    counter.encoding.render_conversation.return_value.image_sources = []
    return counter


class DeepSeekReservedTextTests(unittest.TestCase):
    def test_counting_copy_preserves_images_and_unrelated_fields(self):
        image = {'type': 'image_url', 'image_url': {
            'url': 'https://example.test/image.png?q=%EF%BD%9C', 'detail': 'high',
        }}
        payload = {
            'model': 'deepseek-flash', 'thinking': {'type': 'disabled'},
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': f'中文 {MARKER} twice {MARKER} <think>'}, image,
            ]}],
        }
        original = deepcopy(payload)
        counter = counter_with()
        self.assertEqual(counter.count_request(payload), 3)
        converted = counter.recipe.ChatCompletionRequest.call_args.args[0]
        expected = deepcopy(original)
        expected['messages'][0]['content'][0]['text'] = '中文 <|image|> twice <|image|> <think>'
        self.assertEqual(converted, expected)
        self.assertIsNot(converted, payload)
        self.assertEqual(payload, original)

    def test_generation_sends_original_text_after_local_counting(self):
        provider = OpenAIProvider('deepseek-flash', 'key', 'https://api.deepseek.com')
        messages = [{'role': 'user', 'content': f'Keep {MARKER} verbatim'}]
        original = deepcopy(messages)
        counter = counter_with()
        stream = ('data: ' + json.dumps(chunk({'content': 'done'}, 'stop'))
                  + '\n\ndata: [DONE]\n\n').encode()
        with patch.object(provider, '_counter', return_value=counter), patch(
            'skills_agent.providers.base.request.urlopen', return_value=io.BytesIO(stream),
        ) as send:
            self.assertEqual(provider.count_tokens('', messages, []), 3)
            send.assert_not_called()
            provider.complete('', messages, [])
        counted = counter.recipe.ChatCompletionRequest.call_args.args[0]
        sent = json.loads(send.call_args.args[0].data)
        self.assertEqual(counted['messages'][0]['content'], [
            {'type': 'text', 'text': 'Keep <|image|> verbatim'},
        ])
        self.assertEqual(sent['messages'], [{'role': 'user', 'content': [
            {'type': 'text', 'text': original[0]['content']},
        ]}])
        self.assertEqual(messages, original)

    def test_native_conversion_accepts_reserved_text_in_counting_copy(self):
        try:
            import deepseek_recipe as recipe
        except ImportError:
            self.skipTest('optional deepseek-recipe package is not installed')
        result = json.dumps({'stdout': f'{MARKER}\n{MARKER}', 'stderr': '', 'exit_code': 0},
                            ensure_ascii=False)
        tool = {'role': 'tool', 'tool_call_id': 'call', 'content': result}
        cases = {
            'user': [{'role': 'user', 'content': f'{MARKER} repeated {MARKER}'}],
            'text_block': [{'role': 'user', 'content': [{'type': 'text', 'text': MARKER}]}],
            'tool_json': [
                {'role': 'user', 'content': 'Run shell'},
                {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'call', 'type': 'function',
                    'function': {'name': 'shell', 'arguments': '{"command":"echo marker"}'},
                }]}, tool,
            ],
            'auxiliary_json': _messages({'records': [{'event_id': 'tool-output', 'message': tool}]}),
        }
        for name, messages in cases.items():
            with self.subTest(source=name):
                payload = {'model': 'deepseek-flash', 'messages': messages,
                           'thinking': {'type': 'disabled'}}
                original = deepcopy(payload)
                with self.assertRaises(recipe.ConversionError):
                    recipe.ChatCompletionRequest(payload).convert(recipe.ConversionOptions())
                counter = counter_with(recipe)
                # Exercise native conversion and rendering without a tokenizer or network.
                counter.encoding = types.SimpleNamespace(
                    encode=Mock(return_value=[1, 2, 3]),
                    render_conversation=recipe.DeepseekV41Encoding().render_conversation,
                )
                self.assertEqual(counter.count_request(payload), 3)
                conversation = counter.encoding.encode.call_args.args[0]
                rendered = counter.encoding.render_conversation(conversation)
                self.assertIn('<|image|>', rendered.prompt)
                self.assertNotIn(MARKER, rendered.prompt)
                self.assertEqual(rendered.image_sources, [])
                self.assertEqual(payload, original)
