from __future__ import annotations

import argparse
import json

import httpx

from open_webui.utils.auth import create_token


ADMIN_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
BASE_URL = 'http://127.0.0.1:8080'
NULL_SENTINEL = '__null__'
EMPTY_SENTINEL = '__empty__'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected', required=True)
    parser.add_argument('--new', required=True)
    parser.add_argument('--samples', type=int, default=20)
    return parser.parse_args()


def decode(value: str) -> str | None:
    if value == NULL_SENTINEL:
        return None
    if value == EMPTY_SENTINEL:
        return ''
    return value


def main() -> None:
    args = parse_args()
    expected = decode(args.expected)
    new_value = decode(args.new)
    token = create_token({'id': ADMIN_ID})
    headers = {'Authorization': f'Bearer {token}'}

    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=30.0) as client:
        response = client.get('/api/v1/configs/models')
        response.raise_for_status()
        config = response.json()
        current = config.get('DEFAULT_MODELS')
        if current != expected:
            raise SystemExit(f'default model changed unexpectedly: {current!r}')

        config['DEFAULT_MODELS'] = new_value
        response = client.post('/api/v1/configs/models', json=config)
        response.raise_for_status()
        updated = response.json()
        if updated.get('DEFAULT_MODELS') != new_value:
            raise SystemExit('models config write did not persist the requested default')

    public_samples = []
    for _ in range(args.samples):
        with httpx.Client(
            base_url=BASE_URL,
            headers={**headers, 'Connection': 'close'},
            timeout=30.0,
        ) as client:
            response = client.get('/api/config')
            response.raise_for_status()
            public_samples.append(response.json().get('default_models'))

    if public_samples != [new_value] * args.samples:
        raise SystemExit(
            f'authenticated public config samples were inconsistent: {sorted(set(public_samples), key=repr)!r}'
        )

    print(
        json.dumps(
            {
                'old_default_models': current,
                'new_default_models': new_value,
                'model_order_count': len(updated.get('MODEL_ORDER_LIST') or []),
                'authenticated_public_sample_count': len(public_samples),
                'authenticated_public_sample_values': sorted(set(public_samples), key=repr),
            },
            sort_keys=True,
        )
    )


if __name__ == '__main__':
    main()
