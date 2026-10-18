"""Reserved tokenizer strings remain ordinary text on the gateway wire."""

import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from skills_agent.context.auxiliary import _messages
from skills_agent.providers import OpenAIProvider
from tests.test_stream_openai import chunk


MARKER = '<｜image｜>'


class DeepSeekReservedTextTests(unittest.TestCase):
    def test_generation_keeps_reserved_text_without_local_conversion(self):
        result = json.dumps({'stdout': f'{MARKER}\n{MARKER}', 'stderr': '', 'exit_code': 0},
                            ensure_ascii=False)
        tool = {'role': 'tool', 'tool_call_id': 'call', 'content': result}
        cases = {
            'user': [{'role': 'user', 'content': f'{MARKER} repeated {MARKER}'}],
            'text_block': [{'role': 'user', 'content': [{'type': 'text', 'text': MARKER}]}],
            'tool_json': [
                {'role': 'user', 'content': 'Run shell'},
                {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'call', 'name': 'shell', 'arguments': '{"command":"echo marker"}',
                }]}, tool,
            ],
            'auxiliary_json': _messages({'records': [{'event_id': 'tool-output', 'message': tool}]}),
        }
        for source, messages in cases.items():
            original = deepcopy(messages)
            provider = OpenAIProvider('deepseek-flash', 'key', 'https://api.deepseek.com')
            events = [chunk({'content': 'done'}, 'stop'),
                      {'choices': [], 'usage': {'total_tokens': 9}}, None]
            stream = b''.join(b'data: ' + (b'[DONE]' if event is None else
                                           json.dumps(event).encode()) + b'\n\n' for event in events)
            with self.subTest(source=source), patch(
                'skills_agent.providers.base.request.urlopen', return_value=io.BytesIO(stream),
            ) as send:
                self.assertIsNone(provider.count_tokens('', messages, []))
                send.assert_not_called()
                reply = provider.complete('', messages, [])
                self.assertEqual(provider.count_tokens('', messages + [reply], []), 9)
            wire = json.loads(send.call_args.args[0].data)
            self.assertIn(MARKER, json.dumps(wire, ensure_ascii=False))
            self.assertNotIn('<|image|>', json.dumps(wire, ensure_ascii=False))
            self.assertEqual(messages, original)


if __name__ == '__main__':
    unittest.main()
