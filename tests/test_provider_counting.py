import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from skills_agent.providers import (
    AnthropicProvider, OpenAIProvider, ProviderError, ProviderConnectionError,
    ProviderOutputLimitError, create_provider,
)
from tests.test_providers import anthropic_response, response_events
from tests.test_stream_openai import chunk


TOOLS = [{'name': 'shell', 'description': 'Run a script', 'parameters': {
    'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command'],
}}]


def responses_stream(output, status='completed', **response):
    yield {'type': 'response.' + status, 'response': {
        'id': 'r', 'status': status, 'output': output, **response,
    }}


class CounterTests(unittest.TestCase):
    def test_official_responses_generation_and_count_have_identical_input(self):
        provider = create_provider('openai', 'gpt-test', 'secret')
        messages = [{'role': 'user', 'content': 'Count 你好'}]
        output = [{'type': 'message', 'id': 'm', 'role': 'assistant', 'status': 'completed',
                   'content': [{'type': 'output_text', 'text': 'done', 'annotations': []}]}]
        with patch('skills_agent.providers.openai.post_json', return_value={'input_tokens': 42}) as count, \
                patch('skills_agent.providers.responses.post_stream', return_value=responses_stream(output)) as send:
            self.assertEqual(provider.count_tokens('sys', messages, TOOLS), 42)
            provider.complete('sys', messages, TOOLS, max_output_tokens=32768)
        self.assertEqual(count.call_args.args[0], 'https://api.openai.com/v1/responses/input_tokens')
        actual = send.call_args.args[1]
        self.assertEqual(actual['max_output_tokens'], 32768)
        self.assertFalse(actual['store'])
        self.assertEqual(count.call_args.args[1], {k: v for k, v in actual.items()
                                                  if k not in {'stream', 'store', 'max_output_tokens', 'include'}})
        self.assertEqual(actual['input'][0]['content'][0]['text'], 'Count 你好')

    def test_anthropic_generation_and_count_have_identical_input(self):
        provider = AnthropicProvider('claude-test', 'secret')
        messages = [{'role': 'user', 'content': 'Hello'}]
        with patch('skills_agent.providers.anthropic.post_json', return_value={'input_tokens': 17}) as count, \
                patch('skills_agent.providers.anthropic.post_stream',
                      return_value=response_events(anthropic_response())) as send:
            self.assertEqual(provider.count_tokens('sys', messages, TOOLS), 17)
            provider.complete('sys', messages, TOOLS, max_output_tokens=16000)
        self.assertEqual(count.call_args.args[0], 'https://api.anthropic.com/v1/messages/count_tokens')
        payload = send.call_args.args[1]
        self.assertEqual(payload['max_tokens'], 16000)
        self.assertEqual(count.call_args.args[1], {k: v for k, v in payload.items() if k not in {'stream', 'max_tokens'}})

    def test_disabled_thinking_is_shared_by_official_count_and_generation(self):
        output = [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': 'summary', 'annotations': []},
        ]}]
        cases = [
            (OpenAIProvider('model', 'key'), 'openai', 'responses',
             list(responses_stream(output)), {'stream', 'store', 'max_output_tokens', 'include'},
             'reasoning', {'effort': 'none'}),
            (AnthropicProvider('model', 'key'), 'anthropic', 'anthropic',
             list(response_events(anthropic_response())), {'stream', 'max_tokens'},
             'thinking', {'type': 'disabled'}),
        ]
        for provider, count_module, stream_module, events, output_fields, field, setting in cases:
            with self.subTest(provider=type(provider).__name__), patch(
                f'skills_agent.providers.{count_module}.post_json', return_value={'input_tokens': 42},
            ) as count, patch(
                f'skills_agent.providers.{stream_module}.post_stream',
                return_value=(deepcopy(event) for event in events),
            ) as send:
                self.assertEqual(provider.count_tokens('sys', [], [], disable_thinking=True), 42)
                provider.complete('sys', [], [], disable_thinking=True)
            payload = send.call_args.args[1]
            self.assertEqual(payload[field], setting)
            self.assertEqual(count.call_args.args[1], {
                key: value for key, value in payload.items() if key not in output_fields
            })

    def test_deepseek_disabled_thinking_generation_needs_no_local_counter(self):
        provider = OpenAIProvider('deepseek-flash', 'key', 'https://api.deepseek.com')
        with patch('skills_agent.providers.openai.post_json') as count, patch(
            'skills_agent.providers.openai.post_stream',
            return_value=(event for event in [chunk({'content': 'summary'}, 'stop'),
                                             {'choices': [], 'usage': {'total_tokens': 42}}, None]),
        ) as send:
            self.assertIsNone(provider.count_tokens('sys', [], [], disable_thinking=True))
            reply = provider.complete('sys', [], [], disable_thinking=True)
            self.assertEqual(provider.count_tokens('sys', [reply], [], disable_thinking=True), 42)
        count.assert_not_called()
        payload = send.call_args.args[1]
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['max_tokens'], provider.max_output_tokens)

    def test_official_text_count_uses_valid_request_and_keeps_envelope(self):
        for cls, module in [(OpenAIProvider, 'openai'), (AnthropicProvider, 'anthropic')]:
            provider = cls('model', 'key')
            with self.subTest(cls=cls), patch(f'skills_agent.providers.{module}.post_json',
                                              return_value={'input_tokens': 31}) as count:
                self.assertEqual(provider.count_text('report正文'), 31)
                self.assertEqual(provider.count_text(''), 0)
                self.assertEqual(count.call_count, 1)
                self.assertIn('report正文', json.dumps(count.call_args.args[1], ensure_ascii=False))

    def test_chat_gateway_uses_reported_usage_without_counting_or_estimation(self):
        provider = OpenAIProvider('custom', 'key', 'http://gateway/v1')
        with patch('skills_agent.providers.base.request.urlopen') as send:
            self.assertIsNone(provider.count_tokens('', [{'role': 'user', 'content': 'hello' * 100000}], []))
            self.assertIsNone(provider.count_text('hello'))
        self.assertEqual(provider.token_counter, 'usage')
        send.assert_not_called()

    def test_counter_rejects_non_integer_and_negative_counts(self):
        for value in [None, -1, True, '42', 3.5]:
            with self.subTest(value=value), patch('skills_agent.providers.openai.post_json',
                                                 return_value={'input_tokens': value}), \
                    self.assertRaises(ProviderError):
                OpenAIProvider('model', 'key').count_tokens('', [], [])

    def test_counter_http_transport_is_not_a_generation_request(self):
        with patch('skills_agent.providers.base.request.urlopen',
                   return_value=io.BytesIO(b'{"input_tokens":123}')) as request:
            self.assertEqual(AnthropicProvider('model', 'secret').count_tokens('', [
                {'role': 'user', 'content': 'input'},
            ], []), 123)
        self.assertNotIn('stream', json.loads(request.call_args.args[0].data))

    def test_counter_counts_media_using_the_same_input_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            path.write_bytes(b'png bytes')
            messages = [{'role': 'user', 'content': [{'type': 'image', 'path': str(path), 'mime_type': 'image/png'}]}]
            for cls, module in [(OpenAIProvider, 'openai'), (AnthropicProvider, 'anthropic')]:
                with self.subTest(cls=cls), patch(f'skills_agent.providers.{module}.post_json', return_value={'input_tokens': 100}) as count:
                    self.assertEqual(cls('model', 'secret').count_tokens('', messages, []), 100)
                    self.assertIn('cG5nIGJ5dGVz', json.dumps(count.call_args.args[1]))

    def test_deepseek_default_needs_no_tokenizer(self):
        provider = OpenAIProvider('deepseek-flash', 'key', 'https://api.deepseek.com')
        self.assertEqual(provider.api_format, 'chat')
        self.assertEqual(provider.token_counter, 'usage')
        self.assertIsNone(provider.count_text('hello'))
        self.assertIsNone(provider.count_tokens('', [], []))



class ResponsesTests(unittest.TestCase):
    def test_failed_or_invalid_terminal_status_is_not_an_output_limit(self):
        output = [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': 'must not be accepted', 'annotations': []},
        ]}]
        for kind, status in [('response.failed', 'failed'), ('response.failed', 'incomplete'),
                             ('response.incomplete', 'failed'), ('response.incomplete', None)]:
            response = {'output': output, 'incomplete_details': {'reason': 'max_output_tokens'}}
            if status is not None:
                response['status'] = status
            event = {'type': kind, 'response': response}
            with self.subTest(kind=kind, status=status), patch(
                'skills_agent.providers.responses.post_stream', return_value=(e for e in [event]),
            ), self.assertRaises(ProviderError) as raised:
                OpenAIProvider('model', 'key').complete('', [], [], disable_thinking=True)
            self.assertNotIsInstance(raised.exception, ProviderOutputLimitError)

    def test_output_limit_keeps_summary_from_deltas_or_terminal_output(self):
        output = [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': 'partial summary', 'annotations': []},
        ]}, {'type': 'function_call', 'call_id': 'a', 'name': 'shell', 'arguments': '{private'}]
        terminal = list(responses_stream(output, 'incomplete', incomplete_details={'reason': 'max_output_tokens'}))
        delta = {'type': 'response.output_text.delta', 'output_index': 0,
                 'content_index': 0, 'delta': 'partial summary'}
        for events in (terminal, [delta, *terminal]):
            with self.subTest(deltas=len(events) > 1), patch(
                'skills_agent.providers.responses.post_stream', return_value=(deepcopy(e) for e in events),
            ), self.assertRaises(ProviderOutputLimitError) as raised:
                OpenAIProvider('model', 'key').complete('', [], [], disable_thinking=True)
            self.assertEqual(raised.exception.partial_content, [{'type': 'text', 'text': 'partial summary'}])

    def test_summary_assistant_uses_easy_input_message_schema(self):
        provider = OpenAIProvider('model', 'key')
        payload = provider._responses_payload('', [
            {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Historical summary'}]},
            {'role': 'user', 'content': 'Continue'},
        ], [])
        self.assertEqual(payload['input'][0], {
            'role': 'assistant', 'content': [{'type': 'input_text', 'text': 'Historical summary'}],
        })

    def test_stream_order_and_native_reasoning_roundtrip(self):
        reasoning = {'type': 'reasoning', 'id': 'r1', 'summary': [{'type': 'summary_text', 'text': 'think'}],
                     'encrypted_content': 'opaque'}
        call = {'type': 'function_call', 'id': 'fc', 'call_id': 'call', 'name': 'shell', 'arguments': '{"command":"pwd"}', 'status': 'completed'}
        answer = {'type': 'message', 'id': 'm', 'role': 'assistant', 'status': 'completed',
                  'phase': 'commentary', 'content': [{'type': 'output_text', 'text': 'checking', 'annotations': []}]}
        events = [
            {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0, 'delta': 'checking'},
            {'type': 'response.reasoning_summary_text.delta', 'output_index': 1, 'summary_index': 0, 'delta': 'think'},
            {'type': 'response.output_item.added', 'output_index': 2, 'item': {**call, 'arguments': ''}},
            {'type': 'response.function_call_arguments.delta', 'output_index': 2, 'delta': call['arguments']},
            *responses_stream([answer, reasoning, call]),
        ]
        observed = []
        provider = OpenAIProvider('model', 'secret')
        with patch('skills_agent.providers.responses.post_stream', return_value=(event for event in events)):
            reply = provider.complete('', [{'role': 'user', 'content': 'start'}], TOOLS, on_event=observed.append)
        self.assertEqual([e['type'] for e in observed], ['text_delta', 'thinking_delta', 'tool_call_delta', 'tool_call_delta'])
        self.assertEqual(reply['_responses_output'], [answer, reasoning, call])
        self.assertEqual(reply['tool_calls'][0]['id'], 'call')
        messages = [reply, {'role': 'tool', 'tool_call_id': 'call', 'content': '/tmp'}]
        with patch('skills_agent.providers.openai.post_json', return_value={'input_tokens': 88}) as count:
            provider.count_tokens('sys', messages, TOOLS)
        self.assertEqual(count.call_args.args[1]['input'][:3], [answer, reasoning, call])
        self.assertEqual(count.call_args.args[1]['input'][3], {'type': 'function_call_output', 'call_id': 'call', 'output': '/tmp'})
        self.assertNotIn('_raw', json.dumps(count.call_args.args[1]))

    def test_broken_stream_is_retryable_but_truncation_is_not(self):
        provider = OpenAIProvider('model', 'secret')
        text = {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0, 'delta': 'partial'}
        with patch('skills_agent.providers.responses.post_stream', return_value=(event for event in [text])), self.assertRaises(ProviderConnectionError) as raised:
            provider.complete('', [], [])
        self.assertEqual(raised.exception.partial_content, [{'type': 'text', 'text': 'partial'}])
        with patch('skills_agent.providers.responses.post_stream', return_value=responses_stream([], 'incomplete', incomplete_details={'reason': 'max_output_tokens'})), self.assertRaises(ProviderOutputLimitError):
            provider.complete('', [], [])

    def test_provider_network_failure_has_specific_class(self):
        with patch('skills_agent.providers.base.request.urlopen', side_effect=URLError('private URL')), self.assertRaises(ProviderConnectionError):
            OpenAIProvider('model', 'key').complete('', [], [])

    def test_invalid_responses_tool_arguments_reach_tool_validation_unchanged(self):
        output = [{'type': 'function_call', 'call_id': 'a', 'name': 'shell', 'arguments': '{'}]
        with patch('skills_agent.providers.responses.post_stream', return_value=responses_stream(output)):
            reply = OpenAIProvider('model', 'key').complete('', [], [])
        self.assertEqual(reply['tool_calls'], [{'id': 'a', 'name': 'shell', 'arguments': '{'}])

    def test_chat_and_messages_distinguish_connection_from_output_limit(self):
        cases = [
            ('openai', OpenAIProvider('model', 'key', api_format='chat'), [chunk({'content': 'partial'})],
             [chunk({'content': 'partial'}, 'length'), None]),
            ('anthropic', AnthropicProvider('model', 'key'), list(response_events(anthropic_response()))[:-1],
             list(response_events(anthropic_response(reason='max_tokens')))),
        ]
        for module, provider, disconnected, truncated in cases:
            for events, error in [(disconnected, ProviderConnectionError), (truncated, ProviderOutputLimitError)]:
                with self.subTest(module=module, error=error), patch(
                    f'skills_agent.providers.{module}.post_stream', return_value=(event for event in events),
                ), self.assertRaises(error):
                    provider.complete('', [], [])


class ThinkingCompactionTests(unittest.TestCase):
    def test_anthropic_defers_compaction_until_thinking_tool_turn_finishes(self):
        provider = AnthropicProvider('model', 'key')
        assistant = {'role': 'assistant', 'content': [], '_anthropic_content': [
            {'type': 'thinking', 'thinking': '', 'signature': 'opaque'},
            {'type': 'tool_use', 'id': 'a', 'name': 'shell', 'input': {}},
        ], 'tool_calls': [{'id': 'a', 'name': 'shell', 'arguments': '{}'}]}
        conversation = [{'role': 'user', 'content': 'task'}, assistant,
                        {'role': 'tool', 'tool_call_id': 'a', 'content': 'complete locally'}]
        self.assertFalse(provider.can_compact(conversation))
        self.assertFalse(provider.can_compact(conversation + [{'role': 'user', 'content': 'continue'}]))
        conversation.append({'role': 'assistant', 'content': [{'type': 'text', 'text': 'Finished'}]})
        self.assertTrue(provider.can_compact(conversation))
        self.assertTrue(OpenAIProvider('model', 'key').can_compact(conversation))
        plain_call = deepcopy(assistant)
        plain_call['_anthropic_content'] = plain_call['_anthropic_content'][1:]
        self.assertTrue(provider.can_compact([plain_call, {'role': 'tool', 'tool_call_id': 'a', 'content': 'ok'}]))

    def test_anthropic_native_blocks_roundtrip_until_compaction_only(self):
        native = [
            {'type': 'thinking', 'thinking': '', 'signature': 'opaque'},
            {'type': 'text', 'text': 'check'},
            {'type': 'tool_use', 'id': 'a', 'name': 'shell', 'input': {'command': 'pwd'}},
            {'type': 'redacted_thinking', 'data': 'opaque-redacted'},
        ]
        provider = AnthropicProvider('model', 'key')
        with patch('skills_agent.providers.anthropic.post_stream', return_value=response_events(anthropic_response(native, 'tool_use'))):
            reply = provider.complete('sys', [], TOOLS)
        original = deepcopy(reply)
        messages = [reply, {'role': 'tool', 'tool_call_id': 'a', 'content': 'ok'}]
        self.assertEqual(provider._payload('sys', messages, TOOLS)['messages'][0]['content'], native)
        messages.append({'role': 'assistant', 'content': [{'type': 'text', 'text': 'Finished'}]})
        self.assertTrue(provider.can_compact(messages))
        compacted = provider.compact_messages(messages)
        self.assertEqual(reply, original)
        stripped = [block for block in native if block['type'] not in {'thinking', 'redacted_thinking'}]
        self.assertEqual(provider._payload('sys', compacted, TOOLS)['messages'][0]['content'], stripped)
        self.assertEqual(compacted[0]['_raw']['content'], native)
        self.assertEqual(compacted[0]['tool_calls'], reply['tool_calls'])
