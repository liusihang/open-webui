import importlib.util
from pathlib import Path

import pytest
import hashlib
import io


spec = importlib.util.spec_from_file_location(
    'live_business_import', Path(__file__).parents[2] / 'tools/migration/v0120/live_business_import.py'
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_common_columns_follow_official_order():
    old = [{'name': 'body', 'type': 'json'}, {'name': 'id', 'type': 'text'}, {'name': 'legacy_mode', 'type': 'text'}]
    new = [{'name': 'id', 'type': 'text'}, {'name': 'body', 'type': 'json'}, {'name': 'timer_at', 'type': 'int8'}]
    assert module.common_columns(old, new) == ['id', 'body']


def test_type_change_is_not_silently_cast():
    with pytest.raises(ValueError, match='column type changed'):
        module.common_columns([{'name': 'x', 'type': 'json'}], [{'name': 'x', 'type': 'jsonb'}])


@pytest.mark.parametrize('old,new', [('text', 'varchar'), ('varchar', 'text')])
def test_text_varchar_keep_identical_binary_field_bytes(old, new):
    assert module.common_columns([{'name': 'id', 'type': old}], [{'name': 'id', 'type': new}]) == ['id']


def test_all_official_business_surfaces_are_explicit():
    assert len(module.BUSINESS_TABLES) == len(set(module.BUSINESS_TABLES))
    assert {
        'auth',
        'api_key',
        'chat_message',
        'calendar_event_attendee',
        'automation_run',
        'channel_webhook',
        'prompt_history',
        'feedback',
        'oauth_session',
        'access_grant',
    } <= set(module.BUSINESS_TABLES)
    assert not {
        'config',
        'model',
        'tool',
        'function',
        'skill',
        'document_chunk',
        'knowledge_evidence',
        'retrieval_index_job',
    } & set(module.BUSINESS_TABLES)


def test_bounded_binary_pipe_covers_large_payload():
    payload = bytes(range(256)) * 4096
    output = io.BytesIO()

    class Source:
        def copy_expert(self, query, stream):
            for i in range(0, len(payload), 8192):
                stream.write(payload[i : i + 8192])

    class Target:
        def copy_expert(self, query, stream):
            while True:
                data = stream.read(4096)
                if not data:
                    break
                output.write(data)

    stats = {}
    digest = module.stream_binary_copy(Source(), 'source', Target(), 'target', stats=stats)
    assert output.getvalue() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert stats['bytes'] == len(payload)


def test_hash_sink_handles_partial_writes():
    class Partial:
        def __init__(self):
            self.output = bytearray()

        def write(self, data):
            self.output.extend(data[:2])
            return min(2, len(data))

    stream = Partial()
    sink = module.HashSink(stream)
    assert sink.write(b'abcdefgh') == 8
    assert stream.output == b'abcdefgh'
    assert sink.digest.hexdigest() == hashlib.sha256(b'abcdefgh').hexdigest()


@pytest.mark.parametrize('table', ['folder', 'tag'])
def test_composite_official_keys_do_not_collapse_different_users(table):
    keys = [('same-id', 'user-a'), ('same-id', 'user-b')]
    assert module.business_key_columns(table) == ('id', 'user_id')
    receipt = module.receipt_keys(table, keys)
    assert len(set(module.keys_from_receipt(table, receipt))) == 2
    assert module.keys_from_receipt(table, receipt) == keys


@pytest.mark.parametrize('table,key', [('user', 'id'), ('document', 'collection_name')])
def test_single_key_receipts_preserve_existing_scalar_format(table, key):
    assert module.business_key_columns(table) == (key,)
    assert module.receipt_keys(table, [('one',)]) == ['one']
    assert module.keys_from_receipt(table, ['one']) == [('one',)]


def test_channel_attachments_load_after_all_parent_messages():
    order = {name: i for i, name in enumerate(module.BUSINESS_TABLES)}
    assert order['channel'] < order['message'] < order['channel_file']
    assert order['file'] < order['channel_file']
    assert order['message'] < order['message_reaction']
