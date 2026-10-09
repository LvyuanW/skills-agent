"""Native Responses messages are shared by generation and input-token counting."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from .base import (
    ProviderError, ProviderConnectionError, ProviderOutputLimitError, blocks,
    encoded_file, parse_arguments, post_stream, validate_calls, validate_tool_pairs,
)


def _input_content(content, role):
    converted = []
    for block in blocks(content):
        kind = block['type']
        if kind == 'text':
            converted.append({'type': 'input_text', 'text': block['text']})
        elif kind == 'image':
            converted.append({'type': 'input_image',
                              'image_url': f"data:{block['mime_type']};base64,{encoded_file(block)}"})
        elif kind == 'file' and block['mime_type'] == 'application/pdf':
            converted.append({'type': 'input_file', 'filename': block.get('name') or Path(block['path']).name,
                              'file_data': f"data:application/pdf;base64,{encoded_file(block)}"})
        elif kind == 'file':
            converted.append({'type': 'input_text', 'text': f"File: {block['path']}"})
        elif kind not in {'thinking', 'redacted_thinking'}:
            raise ProviderError(f'Unsupported content type: {kind}')
    return converted


def _normalized(output):
    content, calls = [], []
    for item in output:
        kind = item['type']
        if kind == 'message':
            for block in item['content']:
                if block['type'] == 'output_text':
                    content.append({'type': 'text', 'text': block['text']})
                elif block['type'] == 'refusal':
                    content.append({'type': 'text', 'text': block['refusal']})
                else:
                    raise ProviderError('Unsupported Responses message content')
        elif kind == 'reasoning':
            for block in item.get('summary', []):
                if block.get('text'):
                    content.append({'type': 'thinking', 'thinking': block['text']})
        elif kind == 'function_call':
            parse_arguments(item['arguments'])
            calls.append({'id': item['call_id'], 'name': item['name'], 'arguments': item['arguments']})
        else:
            raise ProviderError(f'Unsupported Responses output item: {kind}')
    validate_calls(calls)
    return content, calls


class ResponsesMixin:
    def _responses_payload(self, system, messages, tools):
        validate_tool_pairs(messages)
        items = []
        for message in messages:
            role = message['role']
            if role == 'tool':
                items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'],
                              'output': message['content']})
            elif role == 'assistant' and '_responses_output' in message:
                # Keep encrypted reasoning, phase and exact item ordering across requests.
                items.extend(deepcopy(message['_responses_output']))
            else:
                content = _input_content(message.get('content'), role)
                if content:
                    items.append({'role': role, 'content': content})
                for call in message.get('tool_calls', []):
                    items.append({'type': 'function_call', 'call_id': call['id'],
                                  'name': call['name'], 'arguments': call['arguments']})
        payload = {'model': self.model, 'input': items}
        if system:
            payload['instructions'] = system
        if tools:
            payload['tools'] = [{'type': 'function', 'name': t['name'],
                                 'description': t['description'], 'parameters': t['parameters'],
                                 'strict': False} for t in tools]
        return payload

    def _complete_responses(self, system, messages, tools, *, on_event=None, max_output_tokens=None):
        payload = self._responses_payload(system, messages, tools)
        limit = self.max_output_tokens if max_output_tokens is None else max_output_tokens
        if limit <= 0:
            raise ValueError('max_output_tokens must be positive')
        payload.update(stream=True, store=False, max_output_tokens=limit,
                       include=['reasoning.encrypted_content'])
        raw = {'stream_events': []}
        partial, observed = [], {}
        completed = False

        def emit_text(text, kind, channel):
            if not isinstance(text, str):
                raise ProviderError('Invalid Responses text delta')
            if not text:
                return
            field = 'thinking' if kind == 'thinking_delta' else 'text'
            block_kind = 'thinking' if kind == 'thinking_delta' else 'text'
            if partial and observed.get('channel') == channel:
                partial[-1][field] += text
            else:
                partial.append({'type': block_kind, field: text})
            observed['channel'] = channel
            if on_event:
                on_event({'type': kind, 'text': text})

        try:
            with closing(post_stream(self.base_url + '/responses', payload,
                                     {'Authorization': f'Bearer {self.api_key}'})) as stream:
                for event in stream:
                    if event is None:
                        break
                    raw['stream_events'].append(deepcopy(event))
                    kind = event['type']
                    if kind == 'response.completed':
                        response = event['response']
                        if response.get('status') != 'completed':
                            raise ProviderError('Responses completed event has invalid status')
                        raw.update(deepcopy(response))
                        completed = True
                        break
                    if kind in {'response.failed', 'response.incomplete'}:
                        raw.update(deepcopy(event.get('response', {})))
                        reason = raw.get('incomplete_details', {}).get('reason', kind)
                        error_type = ProviderOutputLimitError if reason == 'max_output_tokens' else ProviderError
                        raise error_type(f'OpenAI Responses stopped: {reason}')
                    if kind == 'error':
                        raise ProviderError('Responses stream returned an error')
                    if kind in {'response.created', 'response.in_progress'}:
                        raw.update(deepcopy(event.get('response', {})))
                    elif kind in {'response.output_text.delta', 'response.refusal.delta'}:
                        emit_text(event['delta'], 'text_delta', (kind, event.get('output_index'), event.get('content_index')))
                    elif kind in {'response.reasoning_summary_text.delta', 'response.reasoning_text.delta'}:
                        emit_text(event['delta'], 'thinking_delta', (kind, event.get('output_index'), event.get('summary_index')))
                    elif kind == 'response.output_item.added':
                        item = event['item']
                        if item['type'] == 'function_call' and on_event:
                            on_event({'type': 'tool_call_delta', 'index': event['output_index'],
                                      'id': item['call_id'], 'name': item['name'],
                                      'arguments': item.get('arguments', '')})
                    elif kind == 'response.function_call_arguments.delta' and on_event:
                        on_event({'type': 'tool_call_delta', 'index': event['output_index'],
                                  'arguments': event['delta']})
            if not completed:
                raise ProviderConnectionError('OpenAI Responses stream ended before response.completed')
            content, calls = _normalized(raw['output'])
            result = {'role': 'assistant', 'content': content, '_raw': raw,
                      '_responses_output': deepcopy(raw['output'])}
            if calls:
                result['tool_calls'] = calls
            return result
        except (ProviderError, KeyboardInterrupt) as exc:
            exc.raw = raw
            exc.partial_content = partial
            raise
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            raise ProviderError('Invalid OpenAI Responses stream', raw, partial) from None
