"""Bounded, copy-only Live RAG export/import. No application import or model call.

Run inside each app's container with DATABASE_URL inherited, never on a DSN printed
in a command. Export is a private transfer artifact, not a database restore. Both
export and import default to dry-run; --apply is explicit. Import never runs DDL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

SOURCE_DATABASE = 'webui_db'
SOURCE_SYSTEM_ID = '7615593547098730535'
TARGET_DATABASE = 'user_test'
TARGET_SYSTEM_ID = '7696569975694159911'
FORMAT = 'owui-rag-copy-v1'
MAX_ROWS = 2000
MAX_FILES = 8
MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_TOTAL_ASSET_BYTES = 256 * 1024 * 1024
TABLE_KEYS = {
    'document_chunk': 'id',
    'retrieval_chunk': 'chunk_uid',
    'knowledge_evidence_asset': 'id',
    'knowledge_vector_space': 'id',
    'knowledge_evidence': 'evidence_ref',
    'knowledge_evidence_embedding': 'id',
    'retrieval_index_state': 'state_id',
}
JSON_TEXT = {'metadata', 'encoding_config', 'anchor_json'}
EXCLUDED = re.compile(r'(^|[-_:])(memory|memories|chat|summary|summaries|knowledge-layers)([-_:]|$)', re.I)


class ReuseError(ValueError):
    """Messages contain categories/identities only, not document text or secrets."""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    ).hexdigest()


def stable(prefix, **parts):
    return prefix + '_' + digest(parts)


def decode(value):
    if value is None:
        return {}
    return json.loads(value) if isinstance(value, str) else dict(value)


def allowed_collection(name):
    return isinstance(name, str) and bool(name) and not EXCLUDED.search(name)


def validate_scope(knowledge_ids, file_ids, limit):
    if not knowledge_ids or not file_ids or len(knowledge_ids) > 4 or len(file_ids) > MAX_FILES:
        raise ReuseError('explicit small knowledge and file allowlists required')
    if not 1 <= limit <= MAX_ROWS:
        raise ReuseError('row limit out of bounds')
    for value in [*knowledge_ids, *file_ids]:
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise ReuseError('invalid scope identity')


def validate_identity(identity, *, target=False, source=None):
    database, sysid = identity['database'], identity['system_identifier']
    expected = (TARGET_DATABASE, TARGET_SYSTEM_ID) if target else (SOURCE_DATABASE, SOURCE_SYSTEM_ID)
    if (database, sysid) != expected:
        raise ReuseError('database identity does not match pinned ' + ('candidate' if target else 'Live'))
    if target and (database == SOURCE_DATABASE or sysid == SOURCE_SYSTEM_ID):
        raise ReuseError('production target forbidden')
    if target and source and (sysid == source['system_identifier'] or identity == source):
        raise ReuseError('source and target must be separate clusters')


def connect():
    import psycopg

    # Keep credentials out of argv, logs, output, and transfer bundles.
    dsn = (
        os.environ['DATABASE_URL']
        .replace('postgresql+psycopg2://', 'postgresql://')
        .replace('postgresql+psycopg://', 'postgresql://')
    )
    return psycopg.connect(dsn, autocommit=True)


def begin(conn, *, readonly=True):
    conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ ' + ('READ ONLY' if readonly else 'READ WRITE'))
    conn.execute("SET LOCAL statement_timeout='15s'")
    conn.execute("SET LOCAL lock_timeout='3s'")
    conn.execute("SET LOCAL idle_in_transaction_session_timeout='60s'")
    conn.execute('SET LOCAL search_path=public,pg_catalog')


def identity(conn):
    row = conn.execute('SELECT current_database(), system_identifier::text FROM pg_control_system()').fetchone()
    return {'database': row[0], 'system_identifier': row[1]}


def rows(conn, query, params=()):
    cursor = conn.execute(query, params)
    names = [c.name for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def bounded(conn, query, params, limit):
    result = rows(conn, query + ' LIMIT %s', (*params, limit + 1))
    if len(result) > limit:
        raise ReuseError('selected scope exceeds row limit')
    return result


def check_vector_column(conn):
    value = conn.execute(
        'SELECT format_type(a.atttypid,a.atttypmod) FROM pg_attribute a '
        "WHERE a.attrelid='public.document_chunk'::regclass AND a.attname='vector'"
    ).fetchone()
    if not value or value[0] != 'halfvec(2048)':
        raise ReuseError('requires actual halfvec(2048), no length conversion permitted')


def source_snapshot(conn, knowledge_ids, file_ids, limit, *, payload=False):
    """All source reads share this caller-owned REPEATABLE READ READ ONLY snapshot."""
    validate_scope(knowledge_ids, file_ids, limit)
    source = identity(conn)
    validate_identity(source)
    check_vector_column(conn)
    links = bounded(
        conn,
        'SELECT knowledge_id,file_id FROM knowledge_file WHERE knowledge_id=ANY(%s) AND file_id=ANY(%s)',
        (knowledge_ids, file_ids),
        limit,
    )
    pairs = {(r['knowledge_id'], r['file_id']) for r in links}
    if {r['file_id'] for r in links} != set(file_ids) or {r['knowledge_id'] for r in links} != set(knowledge_ids):
        raise ReuseError('allowlisted knowledge/file links are not closed')
    files = bounded(conn, 'SELECT id,hash,updated_at FROM file WHERE id=ANY(%s)', (file_ids,), limit)
    if {r['id'] for r in files} != set(file_ids):
        raise ReuseError('source file missing')
    chunk_fields = (
        '*'
        if payload
        else (
            'chunk_uid,knowledge_id,file_id,file_version,collection_name,content_hash,chunker_config_hash,'
            "jsonb_build_object('vector_id',metadata::jsonb->>'vector_id',"
            "'file_id',metadata::jsonb->>'file_id') AS metadata"
        )
    )
    chunks = bounded(
        conn,
        f'SELECT {chunk_fields} FROM retrieval_chunk WHERE knowledge_id=ANY(%s) AND file_id=ANY(%s) '
        'AND is_active AND deleted_at IS NULL ORDER BY chunk_uid',
        (knowledge_ids, file_ids),
        limit,
    )
    evidence_fields = (
        '*'
        if payload
        else ('id,evidence_ref,knowledge_id,file_id,asset_id,retrieval_chunk_uid,modality,content_hash,is_active')
    )
    evidence = bounded(
        conn,
        f'SELECT {evidence_fields} FROM knowledge_evidence WHERE knowledge_id=ANY(%s) '
        'AND file_id=ANY(%s) AND is_active AND deleted_at IS NULL ORDER BY evidence_ref',
        (knowledge_ids, file_ids),
        limit,
    )
    asset_fields = '*' if payload else 'id,file_id,knowledge_id,asset_ref,sha256,storage_uri,status'
    assets = bounded(
        conn,
        f'SELECT {asset_fields} FROM knowledge_evidence_asset WHERE knowledge_id=ANY(%s) '
        "AND file_id=ANY(%s) AND status='ready' ORDER BY id",
        (knowledge_ids, file_ids),
        limit,
    )
    refs = [e['evidence_ref'] for e in evidence]
    mappings = bounded(
        conn,
        'SELECT id,evidence_id,evidence_ref,vector_space_id,vector_backend_id,vector_backend_collection,'
        'vector_role,embedding_status FROM knowledge_evidence_embedding WHERE evidence_ref=ANY(%s) '
        "AND embedding_status='ready' "
        "AND vector_backend_id IS NOT NULL AND vector_role IN ('image_dense','text_chunk_dense') ORDER BY id",
        (refs,),
        limit,
    )
    # Space configuration is provenance metadata, never a claim of compatibility.
    spaces = bounded(conn, 'SELECT * FROM knowledge_vector_space WHERE knowledge_id=ANY(%s)', (knowledge_ids,), limit)
    ids = set()
    for chunk in chunks:
        if (chunk['knowledge_id'], chunk['file_id']) not in pairs or not allowed_collection(chunk['collection_name']):
            raise ReuseError('manifest outside allowlist or excluded collection')
        metadata = decode(chunk['metadata'])
        if metadata.get('vector_id'):
            ids.add(metadata['vector_id'])
    # Old projected text vectors are intentionally not duplicated. Only original
    # manifest vectors and existing original image vectors are eligible.
    image_refs = {e['evidence_ref'] for e in evidence if e['modality'] == 'image'}
    ids.update(m['vector_backend_id'] for m in mappings if m['evidence_ref'] in image_refs)
    if len(ids) > limit:
        raise ReuseError('selected vector identities exceed row limit')
    fields = 'id,collection_name,' + (
        'vmetadata,text,vector::text AS vector'
        if payload
        else "jsonb_build_object('file_id',vmetadata->>'file_id') AS vmetadata,(vector IS NOT NULL) AS has_vector"
    )
    # Wide halfvec->text/TOAST reads can exceed the fixed per-statement timeout
    # even when the primary-key plan is correct. Keep one consistent snapshot,
    # but bound each payload fetch; never raise the Live statement timeout.
    vectors = []
    ordered_ids = sorted(ids)
    for offset in range(0, len(ordered_ids), 64):
        selected = ordered_ids[offset:offset + 64]
        vectors.extend(bounded(
            conn, f'SELECT {fields} FROM document_chunk WHERE id=ANY(%s) ORDER BY id',
            (selected,), min(64, limit),
        ))
    if any(not allowed_collection(v['collection_name']) for v in vectors):
        raise ReuseError('excluded vector collection')
    return {
        'format': FORMAT,
        'source': source,
        'captured_at': int(time.time()),
        'scope': {'knowledge_ids': sorted(knowledge_ids), 'file_ids': sorted(file_ids), 'limit': limit},
        'links': links,
        'files': files,
        'chunks': chunks,
        'evidence': evidence,
        'assets': assets,
        'mappings': mappings,
        'spaces': spaces,
        'vectors': vectors,
    }


def preflight_report(snapshot):
    vectors = {v['id']: v for v in snapshot['vectors']}
    categories = Counter()
    for c in snapshot['chunks']:
        vector = vectors.get(decode(c['metadata']).get('vector_id'))
        if vector is None:
            categories['missing_original_vector'] += 1
        elif vector.get('has_vector') is False:
            categories['null_vector'] += 1
        elif vector['collection_name'] != c['collection_name']:
            categories['collection_conflict'] += 1
        elif decode(vector['vmetadata']).get('file_id') not in (None, c['file_id']):
            categories['source_file_conflict'] += 1
        elif decode(vector['vmetadata']).get('file_id') is None:
            categories['metadata_backfill_required'] += 1
        else:
            categories['original_vector_source_closed'] += 1
    return {
        'source': snapshot['source'],
        'scope': snapshot['scope'],
        'counts': {
            key: len(snapshot[key])
            for key in ('links', 'files', 'chunks', 'evidence', 'assets', 'mappings', 'spaces', 'vectors')
        },
        'classification': dict(categories),
        'unknown_spaces_quarantined': len(snapshot['spaces']),
        'body_or_vector_values_output': False,
        'embedding_calls': 0,
    }


def _private_dir(path, *, create=False):
    path = Path(path)
    if path.is_symlink():
        raise ReuseError('private directory must not be a symlink')
    # Transfer artifacts contain real text/vector/media. Keep them out of a Git
    # checkout even if somebody chooses a mode-700 directory there.
    if any((parent / '.git').exists() for parent in (path.resolve(), *path.resolve().parents)):
        raise ReuseError('private transfer bundle must not be inside a repository')
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ReuseError('private directory must have mode 700')
    return path.resolve()


def confined(path, root):
    path, root = Path(path), Path(root).resolve()
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ReuseError('asset is missing or outside approved data root')
    return resolved


def asset_inventory(snapshot, root):
    """Bounded stat only: no image bytes/OCR/HTTP and no path printed."""
    counts = Counter()
    total = 0
    for asset in snapshot['assets']:
        try:
            path = confined(asset['storage_uri'], root)
            size = path.stat().st_size
            total += size
            counts['in_root_regular_files'] += 1
            if size > MAX_ASSET_BYTES:
                counts['over_per_asset_budget'] += 1
        except (OSError, ReuseError):
            counts['missing_or_outside_root'] += 1
    return {
        **dict(counts),
        'total_bytes': total,
        'over_total_budget': total > MAX_TOTAL_ASSET_BYTES,
        'hash_verified': False,
        'content_read': False,
    }


def export_bundle(snapshot, directory, *, asset_root):
    directory = _private_dir(directory, create=True)
    total = 0
    for asset in snapshot['assets']:
        try:
            source = confined(asset['storage_uri'], asset_root)
            size = source.stat().st_size
            if size > MAX_ASSET_BYTES or total + size > MAX_TOTAL_ASSET_BYTES:
                raise ReuseError('asset byte budget exceeded')
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != asset['sha256']:
                raise ReuseError('asset content identity mismatch')
            name = 'asset-' + digest(asset['id']) + '.bin'
            with open(directory / name, 'xb') as handle:
                os.chmod(directory / name, 0o600)
                handle.write(data)
            asset['bundle_file'] = name
            total += size
        except (OSError, ReuseError):
            asset['bundle_file'] = None
            asset['transfer_status'] = 'missing_or_conflicting_asset'
    with open(directory / 'bundle.json', 'x') as handle:
        os.chmod(directory / 'bundle.json', 0o600)
        json.dump(snapshot, handle, ensure_ascii=False, separators=(',', ':'))
    return {
        'exported': True,
        'asset_bytes': total,
        'assets_unavailable': sum(not asset.get('bundle_file') for asset in snapshot['assets']),
        **preflight_report(snapshot),
    }


def load_bundle(directory):
    directory = _private_dir(directory)
    path = directory / 'bundle.json'
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_size > 128 * 1024 * 1024:
        raise ReuseError('bundle must be a bounded private regular file')
    bundle = json.loads(path.read_text())
    if bundle.get('format') != FORMAT:
        raise ReuseError('unsupported bundle')
    validate_identity(bundle['source'])
    scope = bundle['scope']
    validate_scope(scope['knowledge_ids'], scope['file_ids'], scope['limit'])
    for key in ('chunks', 'evidence', 'assets', 'mappings', 'spaces', 'vectors', 'files', 'links'):
        if not isinstance(bundle.get(key), list) or len(bundle[key]) > scope['limit']:
            raise ReuseError('bundle row budget exceeded')
    return bundle


def build_plan(bundle):  # noqa: C901
    """Translate old schema, never restore it. Match core IDs and quarantine rules.

    All imported spaces remain quarantined: the old schema has no revision or
    encoding provenance. A separate, evidence-backed action may later verify them.
    """
    scope = bundle['scope']
    validate_scope(scope['knowledge_ids'], scope['file_ids'], scope['limit'])
    pairs = {(r['knowledge_id'], r['file_id']) for r in bundle['links']}
    if any(k not in scope['knowledge_ids'] or f not in scope['file_ids'] for k, f in pairs):
        raise ReuseError('bundle contains links outside scope')
    plan = {key: [] for key in TABLE_KEYS}
    isolated = Counter()
    now = bundle['captured_at']
    times = {'created_at': now, 'updated_at': now}
    vectors = {v['id']: v for v in bundle['vectors']}
    if len(vectors) != len(bundle['vectors']):
        raise ReuseError('duplicate vector identity')
    copied = {}
    versions = {}
    old_evidence = {}
    for e in bundle['evidence']:
        if (e['knowledge_id'], e['file_id']) not in pairs:
            raise ReuseError('evidence outside scope')
        if e['modality'] == 'text':
            old_evidence.setdefault(e.get('retrieval_chunk_uid'), []).append(e)
    spaces = {}
    for old in bundle['spaces']:
        if old.get('knowledge_id') not in scope['knowledge_ids'] or old.get('vector_backend') != 'pgvector':
            isolated['unsupported_space'] += 1
            continue
        config = {'legacy_projection_config_hash': old.get('projection_config_hash')}
        fingerprint = digest(
            dict(
                model=old['embedding_model'],
                revision=None,
                dimensions=old.get('embedding_dim'),
                encoding=config,
                supports_images=bool(old.get('supports_image_evidence')),
            )
        )
        space = dict(
            id=old['id'],
            embedding_model=old['embedding_model'],
            model_revision=None,
            embedding_dim=old.get('embedding_dim'),
            distance_metric=old.get('distance_metric', 'cosine'),
            config_fingerprint=fingerprint,
            encoding_config=config,
            verification_status='quarantined',
            supports_text_query=bool(old.get('supports_text_query')),
            supports_image_query=bool(old.get('supports_image_query')),
            supports_text_evidence=bool(old.get('supports_text_evidence')),
            supports_image_evidence=bool(old.get('supports_image_evidence')),
            active=bool(old.get('active')),
            **times,
        )
        spaces[old['id']] = space
        plan['knowledge_vector_space'].append(space)
    mappings_by_ref = {}
    for m in bundle['mappings']:
        mappings_by_ref.setdefault(m['evidence_ref'], []).append(m)

    def add_vector(vector_id, collection, file_id):
        vector = vectors.get(vector_id)
        if not vector:
            isolated['missing_original_vector'] += 1
            return None
        if not isinstance(vector.get('vector'), str) or not vector['vector']:
            isolated['null_vector'] += 1
            return None
        if not allowed_collection(collection) or vector['collection_name'] != collection:
            isolated['excluded_or_conflicting_collection'] += 1
            return None
        metadata = decode(vector['vmetadata'])
        if metadata.get('file_id') not in (None, file_id):
            isolated['source_file_conflict'] += 1
            return None
        vector = {key: vector[key] for key in ('id', 'collection_name', 'text', 'vector')}
        vector['vmetadata'] = {**metadata, 'file_id': file_id}
        if vector_id in copied and copied[vector_id] != vector:
            raise ReuseError('same vector has competing source files')
        return vector

    def add_mapping(evidence, space_id, vector):
        space = spaces.get(space_id)
        if not space:
            isolated['missing_space'] += 1
            return
        plan['knowledge_evidence_embedding'].append(
            dict(
                id=stable('mapping', ref=evidence['evidence_ref'], space=space_id),
                evidence_id=evidence['id'],
                evidence_ref=evidence['evidence_ref'],
                vector_space_id=space_id,
                vector_backend_collection=vector['collection_name'],
                vector_backend_id=vector['id'],
                content_hash=evidence['content_hash'],
                config_fingerprint=space['config_fingerprint'],
                embedding_status='quarantined',
                embedding_error=None,
                **times,
            )
        )

    for chunk in bundle['chunks']:
        file_id, kb = chunk['file_id'], chunk['knowledge_id']
        if (kb, file_id) not in pairs or not chunk['is_active'] or chunk.get('deleted_at') is not None:
            raise ReuseError('inactive or out-of-scope manifest')
        metadata = decode(chunk['metadata'])
        if metadata.get('file_id') not in (None, file_id):
            isolated['manifest_source_conflict'] += 1
            continue
        vector = add_vector(metadata.get('vector_id'), chunk['collection_name'], file_id)
        if not vector:
            continue
        if (
            vector['text'] != chunk['text']
            or hashlib.sha256(chunk['text'].encode()).hexdigest() != chunk['content_hash']
        ):
            isolated['text_or_hash_conflict'] += 1
            continue
        copied[vector['id']] = vector
        version = chunk['file_version']
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ReuseError('invalid source file version')
        if file_id in versions and versions[file_id] != version:
            raise ReuseError('multiple active file versions')
        versions[file_id] = version
        manifest = {
            k: chunk.get(k)
            for k in (
                'chunk_uid',
                'collection_id',
                'knowledge_id',
                'collection_name',
                'file_id',
                'file_version',
                'chunk_version',
                'chunk_index',
                'start_index',
                'content_hash',
                'chunker_config_hash',
                'text',
                'created_at',
                'updated_at',
            )
        }
        manifest.update(
            metadata={**metadata, 'vector_id': vector['id'], 'file_id': file_id}, is_active=True, deleted_at=None
        )
        plan['retrieval_chunk'].append(manifest)
        prior = old_evidence.get(chunk['chunk_uid'], [])
        matching = [
            e for e in prior if e['content_hash'] == chunk['content_hash'] and e.get('content_text') == chunk['text']
        ]
        if prior and len(matching) != 1:
            isolated['legacy_evidence_content_conflict'] += len(prior)
            continue
        e = matching[0] if matching else None
        ref = (
            e['evidence_ref']
            if e
            else stable('ke', knowledge_id=kb, file_id=file_id, version=version, chunk_uid=chunk['chunk_uid'])
        )
        evidence = dict(
            id=stable('evidence', ref=ref),
            evidence_ref=ref,
            knowledge_id=kb,
            file_id=file_id,
            file_version=version,
            asset_id=None,
            retrieval_chunk_uid=chunk['chunk_uid'],
            modality='text',
            evidence_kind='text_chunk',
            source_name=e['source_name'] if e else file_id,
            page_index=e.get('page_index') if e else metadata.get('page_index'),
            anchor_json=(decode(e['anchor_json']) if e.get('anchor_json') is not None else None)
            if e
            else metadata.get('anchor'),
            content_hash=chunk['content_hash'],
            is_active=True,
            deleted_at=None,
            **times,
        )
        plan['knowledge_evidence'].append(evidence)
        # Preserve the old space relation, but use the original text vector rather
        # than the redundant projected text embedding.
        candidate_spaces = {
            m['vector_space_id'] for m in mappings_by_ref.get(ref, []) if m['vector_role'] == 'text_chunk_dense'
        }
        if not candidate_spaces:
            candidate_spaces = {s['id'] for s in bundle['spaces'] if s.get('knowledge_id') == kb}
        for space_id in sorted(candidate_spaces):
            add_mapping(evidence, space_id, vector)
    asset_by_id = {a['id']: a for a in bundle['assets']}
    added_assets = set()
    for old in bundle['evidence']:
        if old['modality'] != 'image':
            continue
        asset = asset_by_id.get(old.get('asset_id'))
        if not asset or asset['file_id'] != old['file_id'] or not asset.get('bundle_file'):
            isolated['missing_or_conflicting_asset'] += 1
            continue
        version = versions.get(old['file_id'])
        if version is None or asset['sha256'] != old['content_hash']:
            isolated['image_source_version_or_hash_unknown'] += 1
            continue
        image_mappings = [m for m in mappings_by_ref.get(old['evidence_ref'], []) if m['vector_role'] == 'image_dense']
        if not image_mappings:
            isolated['missing_image_vector'] += 1
            continue
        image_vectors = []
        for mapping in image_mappings:
            v = add_vector(mapping['vector_backend_id'], mapping['vector_backend_collection'], old['file_id'])
            if v:
                image_vectors.append((mapping, v))
                copied[v['id']] = v
        if not image_vectors:
            continue
        if asset['id'] not in added_assets:
            physical = {
                k: asset.get(k)
                for k in (
                    'id',
                    'asset_ref',
                    'file_id',
                    'asset_kind',
                    'sha256',
                    'mime_type',
                    'width',
                    'height',
                    'page_index',
                    'anchor_json',
                    'caption',
                    'ocr_text',
                    'surrounding_text',
                )
            }
            physical.update(file_version=version, storage_uri=asset['bundle_file'], status='ready', error=None, **times)
            if physical['anchor_json'] is not None:
                physical['anchor_json'] = decode(physical['anchor_json'])
            plan['knowledge_evidence_asset'].append(physical)
            added_assets.add(asset['id'])
        ref = old['evidence_ref']
        evidence = dict(
            id=stable('evidence', ref=ref),
            evidence_ref=ref,
            knowledge_id=old['knowledge_id'],
            file_id=old['file_id'],
            file_version=version,
            asset_id=asset['id'],
            retrieval_chunk_uid=None,
            modality='image',
            evidence_kind=old['evidence_kind'],
            source_name=old['source_name'],
            page_index=old.get('page_index'),
            anchor_json=decode(old['anchor_json']) if old.get('anchor_json') is not None else None,
            content_hash=asset['sha256'],
            is_active=True,
            deleted_at=None,
            **times,
        )
        plan['knowledge_evidence'].append(evidence)
        for mapping, vector in image_vectors:
            add_mapping(evidence, mapping['vector_space_id'], vector)
    for file_id, version in versions.items():
        plan['retrieval_index_state'].append(
            dict(
                state_id=stable('file', file_id=file_id),
                index_kind='map_text',
                knowledge_id=None,
                collection_name=None,
                file_id=file_id,
                file_version=version,
                target_config_hash=None,
                status='quarantined',
                active_chunk_count=None,
                indexed_chunk_count=0,
                last_job_id=None,
                error=None,
                **times,
            )
        )
    plan['document_chunk'] = list(copied.values())
    return {'tables': plan, 'isolated': dict(isolated), 'versions': versions}


def prepare_assets(plan, bundle_dir, asset_root, *, apply=False):
    """No public paths or overwrites. Content-addressed copied media is retry-safe."""
    root = Path(asset_root).resolve()
    if not root.is_dir():
        raise ReuseError('target asset root must already exist')
    written = []
    total = 0
    for asset in plan['tables']['knowledge_evidence_asset']:
        source = confined(Path(bundle_dir) / ('asset-' + digest(asset['id']) + '.bin'), bundle_dir)
        total += source.stat().st_size
        if source.stat().st_size > MAX_ASSET_BYTES or total > MAX_TOTAL_ASSET_BYTES:
            raise ReuseError('asset byte budget exceeded')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset['sha256']:
            raise ReuseError('transferred asset hash mismatch')
        target = root / ('rag-reuse-' + asset['sha256'] + '.bin')
        if target.is_symlink():
            raise ReuseError('target asset symlink forbidden')
        if target.exists() and (
            not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != asset['sha256']
        ):
            raise ReuseError('target asset collision')
        if apply and not target.exists():
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
            written.append(str(target))
        asset['storage_uri'] = str(target)
    return written


def _sql_value(key, value):
    if key in JSON_TEXT and value is not None:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if key == 'vmetadata':
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    return value


def check_existing(existing, proposed, table):
    """Only file_id metadata backfill is mutable; no user/version/content overwrite."""
    if not existing:
        return 'insert'
    if table == 'document_chunk':
        if any(existing[k] != proposed[k] for k in ('id', 'collection_name', 'text', 'vector')):
            raise ReuseError('vector identity/content conflict')
        before, after = decode(existing['vmetadata']), decode(proposed['vmetadata'])
        if before == after:
            return 'same'
        if before.get('file_id') is None and {**before, 'file_id': after['file_id']} == after:
            return 'metadata_only'
        raise ReuseError('existing vector metadata conflict')
    for key, value in proposed.items():
        old = existing[key]
        if key in JSON_TEXT and value is not None:
            old = decode(old)
        if key not in ('created_at', 'updated_at') and old != value:
            raise ReuseError('existing row differs: ' + table)
    return 'same'


def import_plan(conn, bundle, plan, *, apply=False):  # noqa: C901 - one explicit, bounded target transaction
    """One target transaction. No core service commits and no pgvector upsert."""
    target = identity(conn)
    validate_identity(target, target=True, source=bundle['source'])
    check_vector_column(conn)
    available = {
        r[0]
        for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()
    }
    if not set(TABLE_KEYS) <= available:
        raise ReuseError('deploy official slim RAG migration before import; importer never creates schema')
    if conn.execute('SELECT version_num FROM alembic_version').fetchone()[0] != 'c92e41b7d630':
        raise ReuseError('unexpected candidate schema head')
    for link in bundle['links']:
        if not conn.execute(
            'SELECT 1 FROM knowledge_file WHERE knowledge_id=%s AND file_id=%s', (link['knowledge_id'], link['file_id'])
        ).fetchone():
            raise ReuseError('target knowledge/file link missing')
    # A config-only clone intentionally has data=None; compare immutable source
    # file hash and saved update timestamp, never restore file.data or permissions.
    for file in bundle['files']:
        found = conn.execute(
            'SELECT hash,updated_at FROM file WHERE id=%s' + (' FOR UPDATE' if apply else ''), (file['id'],)
        ).fetchone()
        if not found or tuple(found) != (file['hash'], file['updated_at']):
            raise ReuseError('target source file changed or missing')
    outcome = Counter()
    for table in (
        'knowledge_vector_space',
        'document_chunk',
        'retrieval_chunk',
        'knowledge_evidence_asset',
        'knowledge_evidence',
        'knowledge_evidence_embedding',
        'retrieval_index_state',
    ):
        key = TABLE_KEYS[table]
        for proposed in plan['tables'][table]:
            columns = list(proposed)
            selected = ','.join('vector::text AS vector' if col == 'vector' else '"' + col + '"' for col in columns)
            existing = rows(
                conn,
                f'SELECT {selected} FROM "{table}" WHERE "{key}"=%s' + (' FOR UPDATE' if apply else ''),
                (proposed[key],),
            )
            action = check_existing(existing[0] if existing else None, proposed, table)
            outcome[table + ':' + action] += 1
            if not apply or action == 'same':
                continue
            if action == 'metadata_only':
                # No assignment to vector or text: the only allowed UPDATE.
                conn.execute(
                    'UPDATE document_chunk SET vmetadata=%s WHERE id=%s',
                    (_sql_value('vmetadata', proposed['vmetadata']), proposed['id']),
                )
            else:
                names = ','.join('"' + col + '"' for col in columns)
                placeholders = ','.join('%s::halfvec(2048)' if col == 'vector' else '%s' for col in columns)
                conn.execute(
                    f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                    [_sql_value(col, proposed[col]) for col in columns],
                )
            after = rows(conn, f'SELECT {selected} FROM "{table}" WHERE "{key}"=%s', (proposed[key],))
            if check_existing(after[0], proposed, table) != 'same':
                raise ReuseError('target readback mismatch')
    return {
        'applied': apply,
        'target': target,
        'counts': dict(outcome),
        'isolated': plan['isolated'],
        'space_status': 'quarantined',
        'embedding_calls': 0,
        'vector_id_text_value_unchanged': True if apply else None,
        'assertion_depth': 'transactional row readback' if apply else 'planned copy and existing-row conflict check',
    }


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=('preflight', 'export', 'import', 'target-preflight'))
    cli.add_argument('--knowledge-id', action='append', default=[])
    cli.add_argument('--file-id', action='append', default=[])
    cli.add_argument('--max-rows', type=int, default=1000)
    cli.add_argument('--bundle-dir', type=Path)
    cli.add_argument(
        '--asset-root',
        type=Path,
        help='explicit approved image-only root; candidate: /app/backend/data/uploads/document-image-assets',
    )
    cli.add_argument(
        '--apply', action='store_true', help='explicitly write private export or isolated target; default dry-run'
    )
    return cli


def main(argv=None):  # noqa: C901 - four explicit CLI modes with shared transaction cleanup
    os.umask(0o077)
    args = parser().parse_args(argv)
    if (args.mode == 'import' or (args.mode == 'export' and args.apply)) and args.asset_root is None:
        raise ReuseError('explicit image-only --asset-root required, never trust the entire data root')
    with connect() as conn:
        begin(conn, readonly=not (args.mode == 'import' and args.apply))
        try:
            if args.mode == 'target-preflight':
                target = identity(conn)
                validate_identity(target, target=True)
                check_vector_column(conn)
                report = {
                    'target': target,
                    'vectors': conn.execute('SELECT count(*) FROM document_chunk').fetchone()[0],
                    'schema_head': conn.execute('SELECT version_num FROM alembic_version').fetchone()[0],
                    'writes': 0,
                }
                if args.knowledge_id or args.file_id:
                    validate_scope(args.knowledge_id, args.file_id, args.max_rows)
                    links = rows(
                        conn,
                        'SELECT knowledge_id,file_id FROM knowledge_file '
                        'WHERE knowledge_id=ANY(%s) AND file_id=ANY(%s)',
                        (args.knowledge_id, args.file_id),
                    )
                    report['selected_scope'] = {
                        'knowledge_ids': args.knowledge_id,
                        'file_ids': args.file_id,
                        'knowledge_file_links': len(links),
                        'files_present': conn.execute(
                            'SELECT count(*) FROM file WHERE id=ANY(%s)', (args.file_id,)
                        ).fetchone()[0],
                        'links_closed': {r['file_id'] for r in links} == set(args.file_id)
                        and {r['knowledge_id'] for r in links} == set(args.knowledge_id),
                    }
            elif args.mode in ('preflight', 'export'):
                payload = args.mode == 'export' and args.apply
                bundle = source_snapshot(conn, args.knowledge_id, args.file_id, args.max_rows, payload=payload)
                if payload:
                    if args.bundle_dir is None:
                        raise ReuseError('private bundle directory required')
                    report = export_bundle(bundle, args.bundle_dir, asset_root=args.asset_root)
                else:
                    report = {'dry_run': True, **preflight_report(bundle)}
                    if args.asset_root is not None:
                        report['asset_inventory'] = asset_inventory(bundle, args.asset_root)
            else:
                if args.bundle_dir is None:
                    raise ReuseError('private bundle directory required')
                bundle = load_bundle(args.bundle_dir)
                plan = build_plan(bundle)
                # Validate conflicts before placing media. Unreferenced content-addressed
                # media after a later SQL failure is harmless and safe to reuse on retry.
                prepare_assets(plan, args.bundle_dir, args.asset_root, apply=False)
                report = import_plan(conn, bundle, plan, apply=False)
                if args.apply:
                    prepare_assets(plan, args.bundle_dir, args.asset_root, apply=True)
                    report = import_plan(conn, bundle, plan, apply=True)
            conn.execute('COMMIT' if args.mode == 'import' and args.apply else 'ROLLBACK')
            print(json.dumps(report, sort_keys=True))
        except Exception:
            conn.execute('ROLLBACK')
            raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # psycopg exceptions may contain connection parameters, business text or
        # constraint payloads. Emit a category only, never the raw exception.
        print(
            json.dumps(
                {
                    'failed': True,
                    'category': type(error).__name__,
                    'locations': [
                        {'function': frame.name, 'line': frame.lineno}
                        for frame in traceback.extract_tb(error.__traceback__)
                        if frame.filename in (__file__, '<stdin>')
                    ],
                    'reason': str(error)
                    if isinstance(error, ReuseError)
                    else 'restricted diagnostic; no database changes committed',
                }
            )
        )
        sys.exit(1)
