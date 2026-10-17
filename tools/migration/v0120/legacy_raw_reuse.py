"""Bounded old-vector reuse with explicit read-only raw-v1 compatibility.

No OCR, chunking or embedding. The companion rag_reuse importer pins source and
target clusters, checks file snapshots and makes atomic, conflict-stopping copies.
Historical metadata is evidence of observed compatibility, not weights provenance.
"""

import ast
import hashlib
import json
import math
from collections import Counter

import rag_reuse as copy

MODEL = 'Qwen3-VL-Embedding-2B'
RAW_URL = 'http://192.168.1.164:8080/v1'
TEXT_METADATA_HASH = '7873dd4c25f5cef9e2aad33bef0bbff945079a56ac621a44490e443433d4cda2'
IMAGE_METADATA_HASH = '7ceb543b7b2f88f8c6a02d1268e1bf3cdbfdf732e9a0576370bc78ad0c017c4a'
METADATA_KEYS = {
    'file_id',
    'vector_id',
    'hash',
    'file_hash',
    'content_hash',
    'embedding_config',
    'embedding_config_hash',
    'page_index',
    'page',
    'start_index',
    'chunk_index',
    'anchor',
    'modality',
    'vector_role',
    'asset_ref',
    'evidence_ref',
    'projection_config_hash',
    'vector_space_id',
    'knowledge_id',
}


def decode_config(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = ast.literal_eval(value)
    return value if isinstance(value, dict) else {}


def accepted_vector(vector, *, image=False):
    """Narrow accepted historical family; all other values remain quarantined."""
    metadata = copy.decode(vector['vmetadata'])
    try:
        values = json.loads(vector['vector'])
        if (
            len(values) != 2048
            or not any(values)
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values)
        ):
            return False
        if image:
            return (
                metadata.get('projection_config_hash') == IMAGE_METADATA_HASH
                and metadata.get('vector_role') == 'image_dense'
                and metadata.get('modality') == 'image'
            )
        config = decode_config(metadata.get('embedding_config'))
        return (
            config == {'engine': 'openai', 'model': MODEL}
            and metadata.get('embedding_config_hash') == TEXT_METADATA_HASH
        )
    except (ValueError, TypeError, SyntaxError):
        return False


def source_snapshot(conn, knowledge_ids, file_ids, limit):
    bundle = copy.source_snapshot(conn, knowledge_ids, file_ids, limit, payload=True)
    bundle['legacy_fallback'] = []
    source_files = {
        row['id']: row for row in copy.rows(conn, 'SELECT id,hash,meta,data FROM file WHERE id=ANY(%s)', (file_ids,))
    }
    # file.hash is processed text SHA; meta.file_hash is uploaded bytes SHA.
    # They are distinct in Live. Never compare a vector's file_hash to file.hash.
    for row in source_files.values():
        row['data'] = copy.decode(row['data'])
        row['meta'] = copy.decode(row['meta'])
        content = row['data'].get('content')
        if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != row['hash']:
            raise copy.ReuseError('source processed content does not close against current file hash')
    # Raw collections without manifests are only read for current explicit links.
    # Presence of ANY manifest for a file forbids guessing a replacement version.
    for kb, file_id in sorted({(x['knowledge_id'], x['file_id']) for x in bundle['links']}):
        any_manifest = conn.execute(
            'SELECT 1 FROM retrieval_chunk WHERE knowledge_id=%s AND file_id=%s LIMIT 1', (kb, file_id)
        ).fetchone()
        if any_manifest:
            continue
        file = source_files[file_id]
        version = copy.decode(file['data']).get('retrieval_version') or 1
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise copy.ReuseError('source file version is invalid')
        vectors = copy.bounded(
            conn,
            'SELECT id,collection_name,text,vector::text AS vector,vmetadata FROM document_chunk '
            "WHERE collection_name=%s AND vmetadata->>'file_id'=%s ORDER BY id",
            (kb, file_id),
            limit,
        )
        count = 0
        for vector in vectors:
            metadata = copy.decode(vector['vmetadata'])
            if metadata.get('modality', 'text') != 'text' or metadata.get('vector_role') not in (
                None,
                'text_chunk_dense',
            ):
                continue
            if not isinstance(vector['text'], str) or not vector['text'].strip():
                continue
            # Hash metadata, where present, must refer to this immutable file.
            if metadata.get('hash') not in (None, file['hash']) or metadata.get('file_hash') not in (
                None,
                file['meta'].get('file_hash'),
            ):
                raise copy.ReuseError('old raw collection file hash conflict')
            content_hash = hashlib.sha256(vector['text'].encode()).hexdigest()
            chunker = copy.digest({'protocol': 'legacy_manifest_from_original_vector_id'})
            uid = copy.stable('chunk', legacy_vector_id=vector['id'], file_id=file_id, version=version, knowledge_id=kb)
            now = bundle['captured_at']
            bundle['chunks'].append(
                {
                    'chunk_uid': uid,
                    'collection_id': None,
                    'knowledge_id': kb,
                    'collection_name': kb,
                    'file_id': file_id,
                    'file_version': version,
                    'chunk_version': 1,
                    'chunk_index': metadata.get('chunk_index'),
                    'start_index': metadata.get('start_index'),
                    'content_hash': content_hash,
                    'chunker_config_hash': chunker,
                    'text': vector['text'],
                    'metadata': {k: v for k, v in metadata.items() if k in METADATA_KEYS} | {'vector_id': vector['id']},
                    'is_active': True,
                    'deleted_at': None,
                    'created_at': now,
                    'updated_at': now,
                }
            )
            bundle['vectors'].append(vector)
            count += 1
        bundle['legacy_fallback'].append({'knowledge_id': kb, 'file_id': file_id, 'rows': count})
    for key in ('chunks', 'vectors'):
        if len(bundle[key]) > limit:
            raise copy.ReuseError('selected legacy scope exceeds row budget')
    closure = Counter()
    for chunk in bundle['chunks']:
        source = source_files[chunk['file_id']]
        version = source['data'].get('retrieval_version')
        if version is not None and version != chunk['file_version']:
            raise copy.ReuseError('active manifest version differs from current file version')
        closure['active_manifest_current_file_snapshot_closed'] += 1
    for vector in bundle['vectors']:
        metadata = copy.decode(vector['vmetadata'])
        source = source_files[metadata['file_id']]
        if metadata.get('hash') not in (None, source['hash']) or metadata.get('file_hash') not in (
            None,
            source['meta'].get('file_hash'),
        ):
            raise copy.ReuseError('original vector hash differs from its matching source hash field')
    bundle['source_closure'] = dict(closure)
    return bundle


def profile(evidence_sha256, *, images, now):
    if not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64:
        raise copy.ReuseError('explicit compatibility evidence hash required')
    config = {
        'protocol': 'legacy_raw_v1',
        'text_api_base_url': RAW_URL,
        'engine': 'openai',
        'model': MODEL,
        'query_prefix': None,
        'source_metadata_hashes': [TEXT_METADATA_HASH] + ([IMAGE_METADATA_HASH] if images else []),
        'compatibility_evidence_sha256': evidence_sha256,
        'provenance': 'observed_compatibility_not_verified_revision',
        'image_prompt': 'Represent the given image for retrieval.',
    }
    fingerprint = copy.digest(
        dict(model=MODEL, revision=None, dimensions=2048, encoding=config, supports_images=images)
    )
    return dict(
        id='legacy_raw_' + fingerprint,
        embedding_model=MODEL,
        model_revision=None,
        embedding_dim=2048,
        distance_metric='cosine',
        config_fingerprint=fingerprint,
        encoding_config=config,
        verification_status='legacy_read_only',
        supports_text_query=True,
        supports_image_query=images,
        supports_text_evidence=True,
        supports_image_evidence=images,
        active=True,
        created_at=now,
        updated_at=now,
    )


def build_plan(bundle, evidence_sha256):
    closed = (bundle.get('source_closure') or {}).get('active_manifest_current_file_snapshot_closed')
    if closed != len(bundle['chunks']):
        raise copy.ReuseError('export with current legacy source closure before importing')
    plan = copy.build_plan(bundle)
    tables = plan['tables']
    vectors = {x['id']: x for x in tables['document_chunk']}
    chunks = {x['chunk_uid']: x for x in tables['retrieval_chunk']}
    old_images = {
        m['evidence_ref']: m['vector_backend_id'] for m in bundle['mappings'] if m['vector_role'] == 'image_dense'
    }
    maps, rejected = [], Counter()
    images = any(e['modality'] == 'image' for e in tables['knowledge_evidence'])
    space = profile(evidence_sha256, images=images, now=bundle['captured_at'])
    for evidence in tables['knowledge_evidence']:
        image = evidence['modality'] == 'image'
        vector_id = (
            old_images.get(evidence['evidence_ref'])
            if image
            else chunks[evidence['retrieval_chunk_uid']]['metadata']['vector_id']
        )
        vector = vectors.get(vector_id)
        if not vector or not accepted_vector(vector, image=image):
            rejected['unaccepted_raw_source'] += 1
            continue
        if image:
            old_space_id = copy.decode(vector['vmetadata']).get('vector_space_id')
            old_space = next((s for s in bundle['spaces'] if s['id'] == old_space_id), None)
            if not old_space or old_space['embedding_model'] != MODEL:
                rejected['unaccepted_image_space'] += 1
                continue
        maps.append(
            dict(
                id=copy.stable('mapping', ref=evidence['evidence_ref'], space=space['id']),
                evidence_id=evidence['id'],
                evidence_ref=evidence['evidence_ref'],
                vector_space_id=space['id'],
                vector_backend_collection=vector['collection_name'],
                vector_backend_id=vector['id'],
                content_hash=evidence['content_hash'],
                config_fingerprint=space['config_fingerprint'],
                embedding_status='ready',
                embedding_error=None,
                created_at=bundle['captured_at'],
                updated_at=bundle['captured_at'],
            )
        )
    # Unknown source rows are not promoted or silently indexed by a new model.
    tables['knowledge_evidence_embedding'] = maps
    tables['knowledge_vector_space'] = [space] if maps else []
    accepted_refs = {row['evidence_ref'] for row in maps}
    tables['knowledge_evidence'] = [row for row in tables['knowledge_evidence'] if row['evidence_ref'] in accepted_refs]
    chunk_ids = {row['retrieval_chunk_uid'] for row in tables['knowledge_evidence'] if row['retrieval_chunk_uid']}
    asset_ids = {row['asset_id'] for row in tables['knowledge_evidence'] if row['asset_id']}
    vector_ids = {row['vector_backend_id'] for row in maps}
    accepted_files = {row['file_id'] for row in tables['knowledge_evidence']}
    tables['retrieval_chunk'] = [row for row in tables['retrieval_chunk'] if row['chunk_uid'] in chunk_ids]
    tables['knowledge_evidence_asset'] = [row for row in tables['knowledge_evidence_asset'] if row['id'] in asset_ids]
    tables['document_chunk'] = [row for row in tables['document_chunk'] if row['id'] in vector_ids]
    tables['retrieval_index_state'] = [
        row for row in tables['retrieval_index_state'] if row['file_id'] in accepted_files
    ]
    plan['isolated'].update(rejected)
    plan['legacy_counts'] = {'read_compatible': len(maps), 'source_rejected': sum(rejected.values())}
    return plan


def preserve_existing_metadata(conn, plan):
    """No large duplicate file.data; existing matching identities are left intact."""
    for table, key, field in [('document_chunk', 'id', 'vmetadata'), ('retrieval_chunk', 'chunk_uid', 'metadata')]:
        for row in plan['tables'][table]:
            existing = copy.rows(conn, f'SELECT * FROM {table} WHERE {key}=%s', (row[key],))
            if existing:
                if copy.decode(existing[0][field]).get('file_id') not in (None, copy.decode(row[field]).get('file_id')):
                    raise copy.ReuseError('existing metadata has a different file identity')
                # This does NOT accept a content difference: normal importer still
                # checks every other field, including actual vector/text identity.
                row[field] = copy.decode(existing[0][field])
            else:
                row[field] = {k: v for k, v in copy.decode(row[field]).items() if k in METADATA_KEYS}


def main(argv=None):
    import os

    os.umask(0o077)
    cli = copy.parser()
    cli.add_argument('--compatibility-evidence-sha256')
    args = cli.parse_args(argv)
    if args.mode not in ('export', 'import') or args.bundle_dir is None or args.asset_root is None:
        raise copy.ReuseError('legacy tool requires export/import with explicit bundle and image roots')
    with copy.connect() as conn:
        copy.begin(conn, readonly=not (args.mode == 'import' and args.apply))
        try:
            if args.mode == 'export':
                bundle = source_snapshot(conn, args.knowledge_id, args.file_id, args.max_rows)
                result = (
                    copy.export_bundle(bundle, args.bundle_dir, asset_root=args.asset_root)
                    if args.apply
                    else {
                        'dry_run': True,
                        **copy.preflight_report(bundle),
                        'source_closure': bundle['source_closure'],
                    }
                )
            else:
                bundle = copy.load_bundle(args.bundle_dir)
                plan = build_plan(bundle, args.compatibility_evidence_sha256)
                preserve_existing_metadata(conn, plan)
                copy.prepare_assets(plan, args.bundle_dir, args.asset_root, apply=False)
                result = copy.import_plan(conn, bundle, plan, apply=False)
                if args.apply:
                    copy.prepare_assets(plan, args.bundle_dir, args.asset_root, apply=True)
                    result = copy.import_plan(conn, bundle, plan, apply=True)
                result.update(space_status='legacy_read_only', legacy_counts=plan['legacy_counts'])
            conn.execute('COMMIT' if args.mode == 'import' and args.apply else 'ROLLBACK')
            print(json.dumps(result, sort_keys=True))
        except Exception:
            conn.execute('ROLLBACK')
            raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # psycopg diagnostic payloads can contain real documents; never print them.
        print(
            json.dumps(
                {
                    'failed': True,
                    'category': type(error).__name__,
                    'reason': str(error) if isinstance(error, copy.ReuseError) else 'restricted diagnostic',
                }
            )
        )
        raise SystemExit(1) from None
