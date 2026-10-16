"""Minimal model-free SSE MCP readiness probe using the standard library."""
from __future__ import annotations

import json
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def probe_sse(url, timeout=45):
    def post(endpoint, payload):
        request = Request(endpoint, json.dumps(payload).encode(),
                          {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        with urlopen(request, timeout=timeout) as response:
            response.read()

    with urlopen(Request(url, headers={'Accept': 'text/event-stream'}), timeout=timeout) as stream:
        def event():
            kind, data = '', []
            while True:
                raw = stream.readline()
                if not raw:
                    raise RuntimeError('MCP SSE stream closed before its response.')
                line = raw.decode().rstrip('\r\n')
                if not line and data:
                    return kind, '\n'.join(data)
                if line.startswith('event:'):
                    kind = line[6:].strip()
                elif line.startswith('data:'):
                    data.append(line[5:].lstrip())

        kind, endpoint = event()
        if kind != 'endpoint':
            raise RuntimeError('MCP SSE server did not advertise its message endpoint.')
        endpoint = urljoin(url, endpoint)

        def request(identifier, method, params):
            post(endpoint, {'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params})
            while True:
                kind, data = event()
                if kind != 'message':
                    continue
                result = json.loads(data)
                if result.get('id') == identifier:
                    if result.get('error'):
                        raise RuntimeError(f'MCP {method} returned {result["error"]}')
                    return result.get('result', {})

        initialized = request(1, 'initialize', {'protocolVersion': '2024-11-05',
            'capabilities': {}, 'clientInfo': {'name': 'tb4-model-free-prewarm', 'version': '1'}})
        post(endpoint, {'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        tools = request(2, 'tools/list', {})
        names = [x['name'] for x in tools.get('tools', [])]
        if not names:
            raise RuntimeError('Task MCP returned no tools.')
        result = {'protocol_version': initialized.get('protocolVersion'), 'tool_names': names,
                  'listed_tools': True, 'no_model_calls': True}
        if 'browser_tabs' in names:
            tabs = request(3, 'tools/call', {'name': 'browser_tabs', 'arguments': {'action': 'list'}})
            if tabs.get('isError'):
                raise RuntimeError('Read-only browser_tabs list failed.')
            result['read_only_browser_tabs_succeeded'] = True
        return result
