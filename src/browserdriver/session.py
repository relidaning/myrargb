"""Connect to a dedicated local Chrome without routing debugging through a proxy."""

import json
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler


def debugger_websocket(endpoint, timeout):
    opener = build_opener(ProxyHandler({}))
    with opener.open(endpoint.rstrip('/') + '/json/version', timeout=timeout) as response:
        data = json.load(response)
    websocket = data.get('webSocketDebuggerUrl', '')
    target, expected = urlsplit(websocket), urlsplit(endpoint)
    if (target.scheme != 'ws' or target.hostname not in ('localhost', '127.0.0.1', '::1')
            or target.port != expected.port or target.username or target.password):
        raise ValueError('Chrome did not provide a matching local debugging endpoint.')
    return websocket
