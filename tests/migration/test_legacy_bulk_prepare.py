"""Bounded bulk planning preserves raw data and never accepts an unknown family."""

import argparse
import hashlib
import json
import sys
import io
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / 'tools/migration/v0120'
sys.path.insert(0, str(TOOLS))
import legacy_bulk_prepare as bulk  # noqa: E402


def sha(value):
    return hashlib.sha256(value).hexdigest()


def test_copy_text_decode_preserves_real_escapes_and_unicode():
    assert bulk.copy_field(b'\\N') is None
    assert bulk.copy_field(rb'one\tline\nslash\\end') == 'one\tline\nslash\\end'
    assert bulk.copy_field(rb'\303\251') == 'é'
    assert bulk.copy_field(rb'literal\\n') == r'literal\n'


def test_dump_rows_quoted_user_and_json_text(tmp_path):
    path = tmp_path / 'dump.sql'
    path.write_bytes(
        b'COPY public."user" (id, role) FROM stdin;\nowner\tuser\n\\.\n'
        b'COPY public.file (id, data, meta, updated_at) FROM stdin;\n'
        + 'file\t{"content":"中文"}\t{}\t4\n\\.\n'.encode()
    )
    rows = list(bulk.dump_rows(path))
    assert rows[0] == ('user', {'id': 'owner', 'role': 'user'})
    assert rows[1][1]['data']['content'] == '中文'
    assert rows[1][1]['updated_at'] == 4
    assert len(list(bulk.dump_rows(path, {'user'}))) == 1


def test_extract_dump_only_writes_sql_never_connects_database(tmp_path, monkeypatch):
    archive = tmp_path / 'source.dump'
    archive.write_bytes(b'synthetic custom archive')
    receipt = tmp_path / 'owner-receipt.json'
    receipt.write_text(
        json.dumps(
            {
                'database': 'webui_db',
                'returncode': 0,
                'bytes': archive.stat().st_size,
                'sha256': sha(archive.read_bytes()),
            }
        )
    )

    def fake_run(command, *, stdout, stderr, check):
        assert '--file=-' in command
        assert '--dbname' not in command and '-d' not in command
        assert command[-1] == str(archive)
        stdout.write(b'-- private synthetic COPY SQL\n')
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr(bulk.subprocess, 'run', fake_run)
    bulk.extract_dump(
        argparse.Namespace(root=tmp_path / 'streams', dump_file=archive, dump_receipt=receipt, mode='extract-small')
    )
    assert (tmp_path / 'streams/small-extract.json').exists()


def test_direct_archive_keeps_original_order_and_filters_memory(tmp_path, monkeypatch):
    small = tmp_path / 'small.sql'
    small.write_bytes(
        b'COPY public.knowledge (id, user_id) FROM stdin;\nkb\towner\n\\.\n'
        b'COPY public.knowledge_file (id, knowledge_id, file_id) FROM stdin;\nlink\tkb\tfile\n\\.\n'
        b'COPY public.knowledge_vector_space (id, knowledge_id) FROM stdin;\nspace\tkb\n\\.\n'
    )
    archive, receipt = tmp_path / 'archive.dump', tmp_path / 'owner.json'
    archive.write_bytes(b'synthetic')
    receipt.write_text(
        json.dumps(
            {
                'database': 'webui_db',
                'returncode': 0,
                'bytes': 9,
                'sha256': sha(b'synthetic'),
                'started': 'start',
                'finished': 'end',
                'consistency': 'repeatable read',
            }
        )
    )
    # document_chunk intentionally arrives before file metadata.
    stream = (
        b'COPY public.document_chunk (id, collection_name, text, vector, vmetadata) FROM stdin;\n'
        b'original\tkb\ttext\t[1,0]\t{"file_id":"file"}\n'
        b'private-memory\tmemory-user\tprivate\t[1,0]\t{}\n\\.\n'
        b'COPY public.file (id, hash, updated_at, data, meta) FROM stdin;\n'
        + ('file\t' + sha(b'parsed') + '\t4\t{"content":"parsed"}\t{}\n\\.\n').encode()
    )

    class Process:
        def __init__(self, command, stdout, stderr):
            assert '--file=-' in command
            assert '--use-list' not in command and '--list' not in command
            assert '--dbname' not in command and '-d' not in command
            self.stdout = io.BytesIO(stream)

        def wait(self):
            return 0

        def terminate(self):
            pass

    monkeypatch.setattr(bulk.subprocess, 'Popen', Process)
    root = tmp_path / 'raw'
    bulk.archive_direct(argparse.Namespace(root=root, dump_file=archive, dump_receipt=receipt, small_data=small))
    conn = bulk.store(root / 'source.sqlite')
    assert [row['id'] for row in bulk.saved(conn, 'document_chunk')] == ['original']
    file = next(bulk.saved(conn, 'file'))
    assert file['processed_content_sha'] == file['hash']
    assert 'content' not in file['data']
    conn.close()
    first = json.loads((root / 'source-receipt.json').read_text())
    (root / 'source-receipt.json').rename(root / 'original-complete-receipt.json')
    bulk.private_write(
        root / 'resume-checkpoint.json',
        {
            'dump_sha256': first['dump_sha256'],
            'sqlite_sha256': bulk.file_sha(root / 'source.sqlite'),
            'full_row_digest': {'rows': sum(first['counts'].values())},
        },
    )
    monkeypatch.setattr(bulk, 'check_scratch_budget', lambda _: None)
    bulk.archive_direct(
        argparse.Namespace(root=root, dump_file=archive, dump_receipt=receipt, small_data=small, resume=True)
    )
    resumed = json.loads((root / 'source-receipt.json').read_text())
    assert resumed['counts'] == first['counts']
    assert resumed['digests'] == first['digests']
    assert resumed['replayed_rows_checked_same'] == sum(first['counts'].values())


def test_resume_rejects_existing_id_different_content(tmp_path):
    conn = bulk.store(tmp_path / 'source.sqlite', create=True)
    assert bulk.put(conn, 'knowledge', {'id': 'kb', 'user_id': 'owner'}) is True
    assert bulk.put(conn, 'knowledge', {'id': 'kb', 'user_id': 'owner'}, allow_same=True) is False
    with pytest.raises(bulk.reuse.ReuseError, match='resume row identity/content conflict'):
        bulk.put(conn, 'knowledge', {'id': 'kb', 'user_id': 'different'}, allow_same=True)
    conn.close()


def test_asset_preservation_does_not_depend_on_text_acceptance(tmp_path):
    source, root, files = tmp_path / 'raw', tmp_path / 'bundle', tmp_path / 'copied-data'
    for path in (source, root, files):
        path.mkdir(mode=0o700)
    (root / 'assets').mkdir(mode=0o700)
    image = files / 'image.bin'
    image.write_bytes(b'original bytes from rejected-text file')
    conn = bulk.store(source / 'source.sqlite', create=True)
    bulk.put(
        conn,
        'knowledge_evidence_asset',
        {
            'id': 'asset',
            'knowledge_id': 'kb',
            'file_id': 'bad-text-file',
            'sha256': sha(image.read_bytes()),
            'storage_uri': str(image),
        },
    )
    conn.commit()
    conn.close()
    bulk.private_write(root / 'manifest.json', {'asset_files': [], 'shards': []})
    bulk.complete_assets(
        argparse.Namespace(root=root, read_root=source, source_asset_root=files, source_asset_prefix=None)
    )
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['asset_capture_counts']['new_sha_verified'] == 1
    assert manifest['pending_assets'] == []
    assert manifest['shards'] == []  # Capturing bytes never promotes rejected vectors.
    assert json.loads((root / 'asset-tree-release-safe.json').read_text())['all_existing_target_tree_reads_finished']


def test_processed_and_upload_hash_are_distinct():
    file = {
        'id': 'file',
        'hash': sha(b'parsed'),
        'data': {'content': 'parsed'},
        'meta': {'file_hash': sha(b'original bytes')},
    }
    closure, error = bulk.closed_file(file)
    assert error is None
    assert bulk.vector_closed(
        {'vmetadata': {'file_id': 'file', 'hash': file['hash'], 'file_hash': file['meta']['file_hash']}},
        file,
        closure[1],
    )
    assert not bulk.vector_closed({'vmetadata': {'file_hash': file['hash']}}, file, closure[1])


def test_unknown_image_archived_but_not_promoted(tmp_path, capsys):
    root, assets = tmp_path / 'private', tmp_path / 'source-images'
    root.mkdir(mode=0o700)
    assets.mkdir()
    asset = assets / 'picture.bin'
    asset.write_bytes(b'synthetic image bytes')
    db = bulk.store(root / 'source.sqlite', create=True)
    file = {
        'id': 'file',
        'hash': sha(b'parsed'),
        'updated_at': 4,
        'data': {'content': 'parsed'},
        'meta': {'file_hash': sha(b'upload')},
    }
    vector = {
        'id': 'original-vector',
        'collection_name': 'kb',
        'text': 'original text',
        'vector': json.dumps([1.0] + [0.0] * 2047),
        'vmetadata': {
            'file_id': 'file',
            'hash': file['hash'],
            'file_hash': file['meta']['file_hash'],
            'embedding_config': {'engine': 'openai', 'model': bulk.legacy.MODEL},
            'embedding_config_hash': bulk.legacy.TEXT_METADATA_HASH,
        },
    }
    records = {
        'knowledge': [{'id': 'kb', 'user_id': 'orphan'}],
        'knowledge_file': [{'id': 'link', 'knowledge_id': 'kb', 'file_id': 'file'}],
        'file': [file],
        'knowledge_vector_space': [
            {
                'id': 'old-space',
                'knowledge_id': 'kb',
                'embedding_model': bulk.legacy.MODEL,
                'embedding_dim': 2048,
                'vector_backend': 'pgvector',
            }
        ],
        'knowledge_evidence_asset': [
            {
                'id': 'asset',
                'knowledge_id': 'kb',
                'file_id': 'file',
                'asset_ref': 'asset-ref',
                'storage_uri': str(asset),
                'sha256': sha(asset.read_bytes()),
                'status': 'ready',
            }
        ],
        'knowledge_evidence': [
            {
                'id': 'image-e',
                'evidence_ref': 'image-ref',
                'knowledge_id': 'kb',
                'file_id': 'file',
                'asset_id': 'asset',
                'retrieval_chunk_uid': None,
                'is_active': True,
                'deleted_at': None,
                'modality': 'image',
                'evidence_kind': 'document_image',
                'source_name': 'file',
                'content_hash': sha(asset.read_bytes()),
            }
        ],
        'knowledge_evidence_embedding': [
            {
                'id': 'image-map',
                'evidence_ref': 'image-ref',
                'vector_role': 'image_dense',
                'embedding_status': 'ready',
                'vector_backend_id': 'image-vector',
                'vector_backend_collection': 'images',
                'vector_space_id': 'old-space',
            }
        ],
        'document_chunk': [
            vector,
            {
                'id': 'image-vector',
                'collection_name': 'images',
                'text': 'image prompt',
                'vector': vector['vector'],
                'vmetadata': {
                    'file_id': 'file',
                    'vector_space_id': 'old-space',
                    'vector_role': 'image_dense',
                    'modality': 'image',
                    'projection_config_hash': 'UNKNOWN',
                },
            },
        ],
    }
    for kind, rows in records.items():
        for row in rows:
            bulk.put(db, kind, row)
    db.commit()
    db.close()
    before = bulk.file_sha(root / 'source.sqlite')
    bulk.private_write(
        root / 'source-receipt.json',
        {
            'source': {'database': bulk.reuse.SOURCE_DATABASE, 'system_identifier': bulk.reuse.SOURCE_SYSTEM_ID},
            'snapshot': ['snapshot', 'timestamp'],
            'archive_sha256': before,
            'completed_at': 10,
        },
    )
    bulk.prepare(argparse.Namespace(root=root, source_asset_root=assets))
    manifest = json.loads((root / 'manifest.json').read_text())
    coverage = manifest['coverage'][0]
    assert coverage['text_searchable'] is True
    assert coverage['image_searchable'] is False
    assert coverage['all_source_images_searchable'] is False
    assert coverage['owner_present'] is False
    assert coverage['totals']['accepted_text'] == 1
    assert coverage['totals']['accepted_images'] == 0
    assert len(manifest['asset_files']) == 1
    assert bulk.file_sha(root / 'source.sqlite') == before
    for shard in manifest['shards']:
        plan = json.loads((root / 'shards' / shard['name']).read_text())['plan']['tables']
        assert {row['id'] for row in plan['document_chunk']} == {'original-vector'}
        assert plan['knowledge_evidence_asset'] == []
        assert all(row['verification_status'] == 'legacy_read_only' for row in plan['knowledge_vector_space'])
        assert plan['knowledge_vector_space'][0]['id'] == manifest['canonical_profile']
    assert 'original text' not in capsys.readouterr().out


@pytest.mark.parametrize(
    'name,sysid',
    [
        ('webui_db', 'new-cluster'),
        ('new-db', bulk.reuse.SOURCE_SYSTEM_ID),
        ('user_test', 'new-cluster'),
        ('new-db', '7696569975694159911'),
    ],
)
def test_existing_live_and_test_import_targets_forbidden(tmp_path, name, sysid):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    bulk.private_write(root / 'manifest.json', {})
    args = argparse.Namespace(
        root=root, expected_target_database=name, expected_target_system_id=sysid, asset_root=tmp_path
    )
    with pytest.raises(bulk.reuse.ReuseError, match='targets forbidden'):
        bulk.import_shards(args)
