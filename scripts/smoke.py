#!/usr/bin/env python3
"""Authenticated OPSI API, WebDAV and persistent workbench acceptance."""
import argparse
import base64
import hashlib
import http.client
import json
from pathlib import Path
import ssl
import socket
import urllib.error
import urllib.parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--username', default='adminuser')
    parser.add_argument('--password-file', type=Path, required=True)
    parser.add_argument('--host-id', required=True)
    parser.add_argument('--marker-file', type=Path, required=True)
    parser.add_argument('--identity-fingerprint-file', type=Path, required=True)
    parser.add_argument('--write-marker', action='store_true')
    parser.add_argument('--connect-address', help='Connect to a local port-forward, preserving URL hostname verification')
    parser.add_argument('--ca-file')
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.url)
    if parsed.scheme != 'https':
        parser.error('HTTPS is required')
    context = ssl.create_default_context(cafile=args.ca_file)
    if args.connect_address and args.connect_address not in ('127.0.0.1', '::1'):
        parser.error('--connect-address is restricted to local port-forwards')
    password = args.password_file.read_text().strip()
    auth = base64.b64encode(f'{args.username}:{password}'.encode()).decode()

    def request(path, method='GET', data=None, authenticated=True, content_type=None):
        headers = {'Authorization': f'Basic {auth}'} if authenticated else {}
        if content_type:
            headers['Content-Type'] = content_type
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                context=context, timeout=45)
        if args.connect_address:
            sock = socket.create_connection((args.connect_address, parsed.port or 443), timeout=45)
            connection.sock = context.wrap_socket(sock, server_hostname=parsed.hostname)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            if response.status >= 400:
                raise urllib.error.HTTPError(path, response.status, response.reason, response.headers, None)
            return response.read()
        finally:
            connection.close()

    payload = json.dumps({'id': 1, 'jsonrpc': '2.0', 'method': 'host_getObjects',
                          'params': {'id': args.host_id}}).encode()
    try:
        anonymous = request('/rpc', 'POST', payload, False, 'application/json')
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403):
            raise RuntimeError(f'Unexpected unauthenticated HTTP status {error.code}') from None
    else:
        if not json.loads(anonymous).get('error'):
            raise RuntimeError('Unauthenticated host API unexpectedly succeeded')
    result = json.loads(request('/rpc', 'POST', payload, content_type='application/json'))
    own = [host for host in result.get('result', []) if host.get('id') == args.host_id]
    if result.get('error') or len(own) != 1 or own[0].get('type') != 'OpsiConfigserver':
        # Do not print backend results: host objects include private host keys.
        raise RuntimeError('Authenticated API did not return the expected server identity')
    host_key = own[0].get('opsiHostKey')
    if not isinstance(host_key, str) or len(host_key) != 32:
        raise RuntimeError('Expected a populated OPSI server host key')
    fingerprint = hashlib.sha256(host_key.encode()).hexdigest()
    if args.write_marker:
        args.identity_fingerprint_file.write_text(fingerprint)
        args.identity_fingerprint_file.chmod(0o600)
    elif args.identity_fingerprint_file.read_text() != fingerprint:
        raise RuntimeError('OPSI server host key changed after restart or upgrade')
    print('Authenticated API and server identity: OK')
    request('/depot/')
    print('Authenticated depot WebDAV: OK')
    marker = args.marker_file.read_bytes()
    path = '/workbench/opsi-ci-persistence.txt'
    if args.write_marker:
        request(path, 'PUT', marker, content_type='text/plain')
    if request(path) != marker:
        raise RuntimeError('Workbench marker differs after restart or upgrade')
    print('Persistent workbench content: OK')


if __name__ == '__main__':
    main()
