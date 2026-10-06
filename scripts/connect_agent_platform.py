"""Save an existing platform application credential in Reach; never provision one.

Run from the Reach repository using .venv/bin/python. The service key is entered
privately. Only a read-only platform setup request is made before saving locally.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]


def safe_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1'}):
        raise argparse.ArgumentTypeError('Use HTTPS or loopback HTTP')
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise argparse.ArgumentTypeError('Invalid endpoint URL')
    return value.rstrip('/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('organization_id', type=UUID)
    parser.add_argument('--url', type=safe_url, required=True)
    parser.add_argument('--external-user-id', required=True)
    parser.add_argument('--bridge-url', type=safe_url, default='http://127.0.0.1:18000/integrations/agent-platform/whatsapp')
    args = parser.parse_args()
    org = str(args.organization_id)
    destination = ROOT / '.local' / 'agent-platform.json'
    if destination.is_symlink() or destination.parent.is_symlink():
        raise SystemExit('Refusing a symlinked credential path')
    existing = json.loads(destination.read_text()) if destination.is_file() else {
        'base_url': args.url, 'bridge_url': args.bridge_url, 'organizations': {}}
    if existing['base_url'] != args.url or existing['bridge_url'] != args.bridge_url:
        raise SystemExit('Existing Reach configuration uses different endpoints; review it before changing them')
    key = getpass.getpass('Existing platform service key (hidden): ').strip()
    try:
        response = httpx.get(args.url + '/v1/whatsapp/setup', headers={
            'Authorization': 'Bearer ' + key, 'X-External-User-ID': args.external_user_id}, timeout=10, follow_redirects=False)
        response.raise_for_status()
        app_id = str(UUID(response.json()['app_id']))
    except (httpx.HTTPError, ValueError, KeyError):
        raise SystemExit('Could not verify platform access. No Reach configuration was changed.') from None
    if any(k != org and v.get('app_id') == app_id for k,v in existing['organizations'].items()):
        raise SystemExit('Provision a separate platform business credential; this identity already belongs to another Reach workspace.')
    prior = existing['organizations'].get(org)
    if prior and (prior['app_id'] != app_id or prior['external_user_id'] != args.external_user_id):
        raise SystemExit('This Reach workspace is bound to another identity; review its channel before changing identity')
    existing['organizations'][org] = {'app_id': app_id, 'key': key,
        'external_user_id': args.external_user_id, 'secret': prior['secret'] if prior else secrets.token_urlsafe(48)}
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(existing, stream, indent=2)
            stream.write('\n')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print('Existing platform connection verified and saved in Reach CRM.')
    print('No application, service key, number or channel was created.')


if __name__ == '__main__':
    main()
