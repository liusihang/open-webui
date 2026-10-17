"""Private, source-read-only bulk preparation of legacy knowledge retrieval.

The raw SQLite archive preserves selected original rows, including rejected rows.
Only the existing, evidence-backed legacy family enters bounded import shards.
No embeddings, source writes, runtime configuration changes, or implicit apply.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import re
import subprocess
import signal
from contextlib import nullcontext
import time
import zlib
from collections import Counter

import legacy_raw_reuse as legacy
import rag_reuse as reuse

FORMAT = 'owui-legacy-bulk-v1'
EVIDENCE_SHA = 'ace5e678a8498238b89ac0c5d366fdb617d8914b548437acf597226831e23d26'
KEYS = dict(
    knowledge='id',
    knowledge_file='id',
    file='id',
    user='id',
    access_grant='id',
    retrieval_chunk='chunk_uid',
    knowledge_evidence='id',
    knowledge_evidence_asset='id',
    knowledge_vector_space='id',
    knowledge_evidence_embedding='id',
    document_chunk='id',
)
JSON_COLUMNS = {'knowledge': {'data', 'meta'}, 'file': {'data', 'meta'}, 'document_chunk': {'vmetadata'}}
INTEGER_COLUMNS = {
    'created_at',
    'updated_at',
    'row_id',
    'file_version',
    'chunk_version',
    'chunk_index',
    'start_index',
    'page_index',
    'width',
    'height',
    'embedding_dim',
    'active_chunk_count',
    'indexed_chunk_count',
}
BOOLEAN_COLUMNS = {
    'is_active',
    'active',
    'supports_text_query',
    'supports_image_query',
    'supports_text_evidence',
    'supports_image_evidence',
    'supports_multivector',
}
SMALL_TABLES = ['knowledge', 'knowledge_file', 'knowledge_vector_space', 'user', 'access_grant']
LARGE_TABLES = [
    'file',
    'retrieval_chunk',
    'knowledge_evidence',
    'knowledge_evidence_asset',
    'knowledge_evidence_embedding',
    'document_chunk',
]


def extract_dump(args):
    """Only local archive decoding; pg_restore is never passed a DB connection."""
    root = private_root(args.root, create=not args.root.exists())
    if not args.dump_file or not args.dump_receipt:
        raise reuse.ReuseError('explicit owner dump and receipt required')
    receipt = json.loads(args.dump_receipt.read_text())
    if (
        receipt.get('database') != reuse.SOURCE_DATABASE
        or receipt.get('returncode') != 0
        or args.dump_file.stat().st_size != receipt.get('bytes')
    ):
        raise reuse.ReuseError('owner dump receipt or archive size differs')
    large = args.mode == 'extract-large'
    output = root / ('large.sql' if large else 'small.sql')
    command = ['pg_restore', '--data-only', '--no-owner', '--no-privileges', '--file=-']
    for table in LARGE_TABLES if large else SMALL_TABLES:
        command.extend(['--table', table])
    command.append(str(args.dump_file))
    with (
        open(output, 'xb') as handle,
        open(root / ('large-stderr.log' if large else 'small-stderr.log'), 'xb') as errors,
    ):
        os.chmod(output, 0o600)
        os.chmod(errors.name, 0o600)
        result = subprocess.run(command, stdout=handle, stderr=errors, check=False)
    if result.returncode:
        raise reuse.ReuseError('offline pg_restore stream extraction failed')
    summary = {
        'stage': args.mode,
        'bytes': output.stat().st_size,
        'sha256': file_sha(output),
        'dump_owner_sha256': receipt['sha256'],
        'database_calls': 0,
        'source_writes': 0,
    }
    private_write(root / ('large-extract.json' if large else 'small-extract.json'), summary)
    print(json.dumps(summary), flush=True)


def copy_field(value):
    """Decode PostgreSQL COPY TEXT, without interpreting Python/JSON escapes."""
    if value == b'\\N':
        return None
    special = {b'b': b'\b', b'f': b'\f', b'n': b'\n', b'r': b'\r', b't': b'\t', b'v': b'\v', b'\\': b'\\'}

    def replace(match):
        code = match.group(1)
        if code in special:
            return special[code]
        if code[:1] == b'x':
            return bytes([int(code[1:], 16)])
        if code[:1] in b'01234567':
            return bytes([int(code, 8)])
        return code

    return re.sub(rb'\\([0-7]{1,3}|x[0-9a-fA-F]{1,2}|.)', replace, value).decode('utf8')


def dump_rows(path, only_tables=None):
    """Read only pg_restore --data-only COPY streams, never execute dump SQL."""
    table, columns = None, None
    with nullcontext(path) if hasattr(path, 'read') else open(path, 'rb') as handle:
        for line in handle:
            if table is None:
                match = re.fullmatch(rb'COPY public\."?([A-Za-z_][A-Za-z_0-9]*)"? \((.+)\) FROM stdin;\r?\n', line)
                if match:
                    table = match.group(1).decode()
                    columns = [value.strip().strip('"') for value in match.group(2).decode().split(',')]
                continue
            if line.rstrip(b'\r\n') == b'\\.':
                table, columns = None, None
                continue
            if table not in KEYS or (only_tables is not None and table not in only_tables):
                continue
            values = line.rstrip(b'\n').rstrip(b'\r').split(b'\t')
            if len(values) != len(columns):
                raise reuse.ReuseError('COPY stream column count mismatch')
            row = dict(zip(columns, (copy_field(value) for value in values), strict=True))
            for key, value in row.items():
                if value is None:
                    continue
                if key in JSON_COLUMNS.get(table, set()):
                    row[key] = json.loads(value)
                elif key in INTEGER_COLUMNS:
                    row[key] = int(value)
                elif key in BOOLEAN_COLUMNS:
                    if value not in ('t', 'f'):
                        raise reuse.ReuseError('COPY stream boolean invalid')
                    row[key] = value == 't'
            yield table, row


def archive_direct(args):
    """One original-TOC-order COPY stream; never rewind a non-seekable dump.

    Docker pg_dump stdout did not have seek offsets. Scope comes from the already
    completed small COPY extraction, so document_chunk may safely arrive first.
    """
    resume = getattr(args, 'resume', False)
    root = private_root(args.root, create=not resume)
    owner = json.loads(args.dump_receipt.read_text())
    if (
        owner.get('database') != reuse.SOURCE_DATABASE
        or owner.get('returncode') != 0
        or args.dump_file.stat().st_size != owner.get('bytes')
    ):
        raise reuse.ReuseError('owner dump receipt or archive size differs')
    if not args.small_data:
        raise reuse.ReuseError('already completed owner-dump small COPY extraction required')
    if resume:
        checkpoint = json.loads((root / 'resume-checkpoint.json').read_text())
        if (
            checkpoint['dump_sha256'] != owner['sha256']
            or file_sha(root / 'source.sqlite') != checkpoint['sqlite_sha256']
        ):
            raise reuse.ReuseError('resume checkpoint source identity or SQLite hash differs')
    conn = store(root / 'source.sqlite', create=not resume)
    if resume:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA wal_autocheckpoint=2048')
        conn.execute('PRAGMA cache_size=-32768')
    receipt = {
        'format': FORMAT,
        'source': {'database': reuse.SOURCE_DATABASE, 'system_identifier': reuse.SOURCE_SYSTEM_ID},
        'snapshot': {
            'started': owner['started'],
            'finished': owner['finished'],
            'consistency': owner['consistency'],
            'exact_snapshot_instant_unknown': True,
        },
        'dump_sha256': owner['sha256'],
        'started_at': int(time.time()),
        'counts': Counter(),
        'source_writes': 0,
        'embedding_calls': 0,
        'origin': 'single original-TOC-order pg_restore COPY stream from owner-verified complete dump',
        'archive_hash_not_reread': True,
        'file_data_full_preserved_by_business_import': True,
    }
    receipt['resumed_from_checkpoint'] = checkpoint if resume else None
    receipt['replayed_rows_checked_same'] = 0
    digests = {kind: hashlib.sha256() for kind in KEYS}
    kids, fids, sids, collections = set(), set(), set(), set()
    for kind, row in dump_rows(args.small_data):
        if kind not in SMALL_TABLES:
            continue
        if kind == 'user':
            row = {key: row[key] for key in ('id', 'role')}
        if kind == 'access_grant' and row['resource_type'] != 'knowledge':
            continue
        inserted = put(conn, kind, row, allow_same=resume)
        receipt['replayed_rows_checked_same'] += int(not inserted)
        receipt['counts'][kind] += 1
        digests[kind].update(encode(row) + b'\n')
    conn.commit()
    kids = {row['id'] for row in saved(conn, 'knowledge')}
    fids = {row['file_id'] for row in saved(conn, 'knowledge_file')}
    sids = {row['id'] for row in saved(conn, 'knowledge_vector_space')}
    collections = kids | fids
    receipt['scope_copy_sha256'] = file_sha(args.small_data)
    previous, count = None, 0
    errors = open(root / ('decode-resume-stderr.log' if resume else 'decode-stderr.log'), 'xb')
    os.chmod(errors.name, 0o600)
    command = ['pg_restore', '--data-only', '--no-owner', '--no-privileges', '--file=-']
    for table in LARGE_TABLES:
        command.extend(['--table', table])
    command.append(str(args.dump_file))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
    try:
        for kind, row in dump_rows(process.stdout):
            if kind != previous:
                conn.commit()
                if previous:
                    print(
                        json.dumps({'stage': 'offline-decode', 'table': previous, 'rows': receipt['counts'][previous]}),
                        flush=True,
                    )
                previous = kind
            if kind == 'knowledge':
                kids.add(row['id'])
                collections.add(row['id'])
            elif kind == 'knowledge_file':
                fids.add(row['file_id'])
                collections.add(row['file_id'])
            elif kind == 'knowledge_vector_space':
                sids.add(row['id'])
            elif kind == 'user':
                row = {key: row[key] for key in ('id', 'role')}
            elif kind == 'access_grant':
                if row['resource_type'] != 'knowledge':
                    continue
            elif kind == 'file':
                if row['id'] not in fids:
                    continue
                data = reuse.decode(row.get('data'))
                content = data.get('content')
                row['processed_content_sha'] = (
                    hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else None
                )
                row['data'] = {'retrieval_version': data.get('retrieval_version')}
            elif kind in ('retrieval_chunk', 'knowledge_evidence', 'knowledge_evidence_asset'):
                if row.get('knowledge_id') not in kids:
                    continue
            elif kind == 'knowledge_evidence_embedding':
                if row.get('vector_space_id') not in sids:
                    continue
                if row.get('vector_role') == 'image_dense' and row.get('vector_backend_collection'):
                    collections.add(row['vector_backend_collection'])
            elif kind == 'document_chunk':
                metadata = reuse.decode(row['vmetadata'])
                if (
                    row['collection_name'] not in collections
                    and metadata.get('vector_space_id') not in sids
                    and not (metadata.get('modality') == 'image' and metadata.get('vector_role') == 'image_dense')
                ):
                    continue
            inserted = put(conn, kind, row, allow_same=resume)
            receipt['replayed_rows_checked_same'] += int(not inserted)
            receipt['counts'][kind] += 1
            digests[kind].update(encode(row) + b'\n')
            count += 1
            if count % (512 if resume else 128) == 0:
                conn.commit()
                if resume:
                    check_scratch_budget(root)
            if kind == 'document_chunk' and receipt['counts'][kind] % 10000 == 0:
                print(json.dumps({'stage': 'offline-vectors', 'rows': receipt['counts'][kind]}), flush=True)
        process.stdout.close()
        code = process.wait()
        if code:
            raise reuse.ReuseError('offline COPY process failed')
    except Exception:
        process.terminate()
        process.wait()
        raise
    finally:
        errors.close()
    conn.commit()
    if resume:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if receipt['replayed_rows_checked_same'] != checkpoint['full_row_digest']['rows']:
            raise reuse.ReuseError('resume did not replay every committed checkpoint row exactly once')
    if conn.execute('SELECT count(*) FROM records').fetchone()[0] != sum(receipt['counts'].values()):
        raise reuse.ReuseError('archive row counts do not close; duplicate or omitted source row')
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise reuse.ReuseError('offline archive integrity failed')
    conn.close()
    receipt['digests'] = {key: value.hexdigest() for key, value in digests.items()}
    receipt['collection_ids'] = sorted(collections)
    receipt['completed_at'] = int(time.time())
    receipt['archive_bytes'] = (root / 'source.sqlite').stat().st_size
    receipt['archive_sha256'] = file_sha(root / 'source.sqlite')
    private_write(root / 'source-receipt.json', receipt)
    print(
        json.dumps(
            {
                'stage': 'offline-source-complete',
                'counts': receipt['counts'],
                'archive_bytes': receipt['archive_bytes'],
                'archive_sha256': receipt['archive_sha256'],
            }
        ),
        flush=True,
    )


def archive_dump(args):
    """Build raw read-only preparation archive from an already verified dump.

    --small-data contains knowledge/links/spaces/user/grants; --large-data contains
    file and the five old retrieval tables plus document_chunk. These are files
    made by pg_restore --data-only, not live SQL connections.
    """
    root = private_root(args.root, create=True)
    if not args.small_data or not args.large_data or not args.dump_sha256 or not args.snapshot_note:
        raise reuse.ReuseError('explicit offline dump streams, checksum and snapshot provenance required')
    conn = store(root / 'source.sqlite', create=True)
    receipt = {
        'format': FORMAT,
        'source': {'database': reuse.SOURCE_DATABASE, 'system_identifier': reuse.SOURCE_SYSTEM_ID},
        'snapshot': args.snapshot_note,
        'dump_sha256': args.dump_sha256,
        'started_at': int(time.time()),
        'counts': Counter(),
        'digests': {},
        'source_writes': 0,
        'embedding_calls': 0,
        'origin': 'verified pg_dump offline COPY streams; source identity supplied by owner receipt',
    }
    receipt['copy_streams'] = {
        'small': {'bytes': args.small_data.stat().st_size, 'sha256': file_sha(args.small_data)},
        'large': {'bytes': args.large_data.stat().st_size, 'sha256': file_sha(args.large_data)},
    }
    digests = {kind: hashlib.sha256() for kind in KEYS}
    for kind, row in dump_rows(args.small_data):
        if kind == 'user':
            row = {key: row[key] for key in ('id', 'role')}
        if kind == 'access_grant' and row['resource_type'] != 'knowledge':
            continue
        put(conn, kind, row)
        receipt['counts'][kind] += 1
        digests[kind].update(encode(row) + b'\n')
    conn.commit()
    kids = {row['id'] for row in saved(conn, 'knowledge')}
    fids = {row['file_id'] for row in saved(conn, 'knowledge_file')}
    sids = {row['id'] for row in saved(conn, 'knowledge_vector_space')}
    image_collections, vector_scope = set(), kids | fids
    # pg_restore normally orders document_chunk before evidence mappings. A first
    # metadata-only pass discovers actual image backend collections before raw vectors.
    for kind, row in dump_rows(args.large_data, {'knowledge_evidence_embedding'}):
        if kind == 'knowledge_evidence_embedding' and row.get('vector_space_id') in sids:
            if row.get('vector_role') == 'image_dense' and row.get('vector_backend_collection'):
                image_collections.add(row['vector_backend_collection'])
    vector_scope |= image_collections
    for kind, row in dump_rows(args.large_data):
        if kind == 'file':
            if row['id'] not in fids:
                continue
            data = reuse.decode(row.get('data'))
            content = data.get('content')
            row['processed_content_sha'] = (
                hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else None
            )
            row['data'] = {'retrieval_version': data.get('retrieval_version')}
        elif kind in ('retrieval_chunk', 'knowledge_evidence', 'knowledge_evidence_asset'):
            if row.get('knowledge_id') not in kids:
                continue
        elif kind == 'knowledge_evidence_embedding':
            if row.get('vector_space_id') not in sids:
                continue
        elif kind == 'document_chunk':
            if row.get('collection_name') not in vector_scope:
                continue
        else:
            continue
        put(conn, kind, row)
        receipt['counts'][kind] += 1
        digests[kind].update(encode(row) + b'\n')
        if sum(receipt['counts'].values()) % 128 == 0:
            conn.commit()
    conn.commit()
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise reuse.ReuseError('offline archive integrity failed')
    conn.close()
    receipt['digests'] = {key: value.hexdigest() for key, value in digests.items()}
    receipt['collection_ids'] = sorted(vector_scope)
    receipt['completed_at'] = int(time.time())
    receipt['archive_bytes'] = (root / 'source.sqlite').stat().st_size
    receipt['archive_sha256'] = file_sha(root / 'source.sqlite')
    private_write(root / 'source-receipt.json', receipt)
    print(
        json.dumps(
            {
                'stage': 'offline-source-complete',
                'counts': receipt['counts'],
                'archive_bytes': receipt['archive_bytes'],
                'archive_sha256': receipt['archive_sha256'],
            }
        ),
        flush=True,
    )


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()


def private_write(path, value):
    with open(path, 'xb') as handle:
        os.chmod(path, 0o600)
        handle.write(encode(value))


def private_root(path, create=False):
    return reuse._private_dir(path, create=create)


def store(path, create=False):
    conn = sqlite3.connect(path)
    if create:
        os.chmod(path, 0o600)
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute(
            'CREATE TABLE records(kind TEXT,id TEXT,kb TEXT,file TEXT,active INTEGER,role TEXT,col TEXT,payload BLOB,PRIMARY KEY(kind,id))'
        )
        conn.execute('CREATE INDEX records_scope ON records(kind,kb,file)')
        conn.execute('CREATE INDEX records_collection ON records(kind,col,file)')
    return conn


def saved(conn, kind, where='', args=()):
    sql = 'SELECT payload FROM records WHERE kind=?' + (' AND ' + where if where else '')
    for (payload,) in conn.execute(sql, (kind, *args)):
        yield json.loads(zlib.decompress(payload))


def put(conn, kind, row, allow_same=False):
    metadata = reuse.decode(row.get('vmetadata')) if kind == 'document_chunk' else {}
    uid = row.get(KEYS[kind])
    if uid is None and kind == 'knowledge_file':
        uid = reuse.digest(row)
    if uid is None:
        raise reuse.ReuseError('source row primary identity missing: ' + kind)
    values = (
        kind,
        str(uid),
        row.get('knowledge_id'),
        row.get('file_id') or metadata.get('file_id'),
        int(bool(row.get('is_active', True)) and row.get('deleted_at') is None),
        row.get('vector_role') or metadata.get('vector_role'),
        row.get('collection_name'),
        zlib.compress(encode(row), 1),
    )
    try:
        conn.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?)', values)
        return True
    except sqlite3.IntegrityError:
        if not allow_same:
            raise
        previous = conn.execute(
            'SELECT kb,file,active,role,col,payload FROM records WHERE kind=? AND id=?', (kind, str(uid))
        ).fetchone()
        if previous is None or previous[:-1] != values[2:-1] or zlib.decompress(previous[-1]) != encode(row):
            raise reuse.ReuseError('resume row identity/content conflict') from None
        return False


def check_scratch_budget(root):
    space = os.statvfs(root)
    available = space.f_bavail * space.f_frsize
    size = sum(path.stat().st_size for path in Path(root).glob('source.sqlite*') if path.is_file())
    # One GiB operating cushion keeps bounded writes inside Main's hard limits:
    # >=15 GiB available and <=25 GiB task-owned scratch at every stage.
    if available < 16 * 1024**3 or size > 24 * 1024**3:
        raise reuse.ReuseError('SSD scratch budget exceeded; no source assets deleted')


def record_digest(conn):
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute('SELECT kind,id,kb,file,active,role,col,payload FROM records ORDER BY kind,id'):
        digest.update(encode(row[:-1]) + b'\n' + row[-1] + b'\n')
        count += 1
    return {'rows': count, 'sha256': digest.hexdigest()}


def checkpoint_scratch(args):
    """Pause only the verified owner, use SQLite backup, verify every stored row.

    The backup is a committed read snapshot, not an unsafe copy of an active DB.
    The old HDD copy remains recoverable, including its journal if interrupted.
    """
    if not args.source_root or not args.source_pid:
        raise reuse.ReuseError('explicit task-owned source root and PID required')
    source_root = private_root(args.source_root)
    proc = Path('/proc') / str(args.source_pid)
    command = (proc / 'cmdline').read_bytes()
    if (
        b'legacy_bulk_prepare.py' not in command
        or b'archive-direct' not in command
        or str(source_root).encode() not in command
    ):
        raise reuse.ReuseError('checkpoint owner PID identity differs')
    children = []
    for child in Path('/proc').iterdir():
        if not child.name.isdigit():
            continue
        try:
            fields = (child / 'stat').read_text().rsplit(') ', 1)[1].split()
            if int(fields[1]) == args.source_pid and (child / 'comm').read_text().strip() == 'pg_restore':
                children.append(int(child.name))
        except (OSError, ValueError, IndexError):
            continue
    root = private_root(args.root, create=True)
    check_scratch_budget(root)
    source = sqlite3.connect('file:' + str(source_root / 'source.sqlite') + '?mode=ro', uri=True, timeout=60)
    source.execute('BEGIN')
    source.execute('SELECT count(*) FROM records').fetchone()  # Acquire a stable committed read snapshot first.
    owner = json.loads(args.dump_receipt.read_text())
    stopped = []
    try:
        for pid in [*children, args.source_pid]:
            os.kill(pid, signal.SIGSTOP)
            stopped.append(pid)
        target = sqlite3.connect(root / 'source.sqlite')
        os.chmod(root / 'source.sqlite', 0o600)
        target.execute('PRAGMA synchronous=NORMAL')
        source.backup(target, pages=512, progress=lambda *_: check_scratch_budget(root), sleep=0.05)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise reuse.ReuseError('SSD checkpoint integrity differs')
        before, after = record_digest(source), record_digest(target)
        if before != after:
            raise reuse.ReuseError('SSD checkpoint full-row digest differs')
        counts = target.execute('SELECT kind,count(*) FROM records GROUP BY kind').fetchall()
        target.close()
        checkpoint = {
            'dump_sha256': owner['sha256'],
            'source_hdd_root': str(source_root),
            'source_owner_pid': args.source_pid,
            'source_child_pids': children,
            'full_row_digest': after,
            'counts': counts,
            'created_at': int(time.time()),
            'sqlite_sha256': file_sha(root / 'source.sqlite'),
            'sqlite_bytes': (root / 'source.sqlite').stat().st_size,
            'original_hdd_preserved': True,
            'archive_rehashed': False,
        }
        private_write(root / 'resume-checkpoint.json', checkpoint)
        source.execute('ROLLBACK')
        source.close()
        # End only the old offline pair after the verified committed backup exists.
        for pid in [args.source_pid, *children]:
            try:
                os.kill(pid, signal.SIGTERM)
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        stopped.clear()
        print(json.dumps({'stage': 'ssd-checkpoint-complete', **checkpoint}), flush=True)
    finally:
        for pid in stopped:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass


def export(args):
    """One repeatable-read snapshot; each cursor FETCH is bounded to 32 rows."""
    root = private_root(args.root, create=True)
    archive = store(root / 'source.sqlite', create=True)
    receipt = {
        'format': FORMAT,
        'started_at': int(time.time()),
        'counts': {},
        'digests': {},
        'embedding_calls': 0,
        'source_writes': 0,
        'fetch_rows': 32,
    }
    with reuse.connect() as source:
        reuse.begin(source)
        source.execute("SET LOCAL application_name='owui-legacy-bulk-readonly-20261017'")
        receipt['source'] = reuse.identity(source)
        reuse.validate_identity(receipt['source'])
        reuse.check_vector_column(source)
        receipt['snapshot'] = source.execute(
            'SELECT txid_current_snapshot()::text,transaction_timestamp()::text'
        ).fetchone()
        knowledge = reuse.rows(source, 'SELECT * FROM knowledge ORDER BY id')
        kids = [row['id'] for row in knowledge]
        links = reuse.rows(source, 'SELECT * FROM knowledge_file WHERE knowledge_id=ANY(%s)', (kids,))
        fids = sorted({row['file_id'] for row in links})
        spaces = reuse.rows(source, 'SELECT * FROM knowledge_vector_space WHERE knowledge_id=ANY(%s)', (kids,))
        sids = [row['id'] for row in spaces]
        for kind, rows in [('knowledge', knowledge), ('knowledge_file', links), ('knowledge_vector_space', spaces)]:
            digest = hashlib.sha256()
            for row in rows:
                put(archive, kind, row)
                digest.update(encode(row) + b'\n')
            receipt['counts'][kind], receipt['digests'][kind] = len(rows), digest.hexdigest()
        queries = [
            ('user', 'SELECT id,role FROM "user"', ()),
            ('access_grant', "SELECT * FROM access_grant WHERE resource_type='knowledge'", ()),
            # The independent business migration preserves complete file.data.
            # Compute its processed-text hash in PostgreSQL and export only the
            # retrieval version, not megabytes of duplicate parsed content.
            (
                'file',
                'SELECT id,user_id,hash,updated_at,path,meta,'
                "jsonb_build_object('retrieval_version',data->'retrieval_version') AS data,"
                "encode(sha256(convert_to(data->>'content','UTF8')),'hex') AS processed_content_sha "
                'FROM file WHERE id=ANY(%s)',
                (fids,),
            ),
            ('retrieval_chunk', 'SELECT * FROM retrieval_chunk WHERE knowledge_id=ANY(%s)', (kids,)),
            ('knowledge_evidence', 'SELECT * FROM knowledge_evidence WHERE knowledge_id=ANY(%s)', (kids,)),
            ('knowledge_evidence_asset', 'SELECT * FROM knowledge_evidence_asset WHERE knowledge_id=ANY(%s)', (kids,)),
            (
                'knowledge_evidence_embedding',
                'SELECT * FROM knowledge_evidence_embedding WHERE vector_space_id=ANY(%s)',
                (sids,),
            ),
        ]
        for index, (kind, sql, params) in enumerate(queries):
            digest, count = hashlib.sha256(), 0
            with source.cursor(name='bulk_' + str(index)) as cursor:
                cursor.execute(sql, params)
                names = [desc.name for desc in cursor.description]
                while batch := cursor.fetchmany(1 if kind == 'file' else 8):
                    for values in batch:
                        row = dict(zip(names, values, strict=True))
                        put(archive, kind, row)
                        digest.update(encode(row) + b'\n')
                        count += 1
                    archive.commit()
            receipt['counts'][kind], receipt['digests'][kind] = count, digest.hexdigest()
            private_write(root / (kind + '-done.json'), {'count': count, 'digest': digest.hexdigest()})
            print(json.dumps({'stage': 'export', 'table': kind, 'rows': count}), flush=True)
        # Include original KB/file collections and actual image mapping collections.
        # Keep projected rows in the raw archive but never use projected text as originals.
        collections = sorted(
            set(
                kids
                + fids
                + [
                    row['vector_backend_collection']
                    for row in saved(archive, 'knowledge_evidence_embedding')
                    if row.get('vector_role') == 'image_dense' and row.get('vector_backend_collection')
                ]
            )
        )
        digest, count = hashlib.sha256(), 0
        for offset in range(0, len(collections), 32):
            chosen = collections[offset : offset + 32]
            with source.cursor(name='vectors_' + str(offset)) as cursor:
                cursor.execute(
                    'SELECT id,collection_name,text,vector::text AS vector,vmetadata '
                    'FROM document_chunk WHERE collection_name=ANY(%s)',
                    (chosen,),
                )
                names = [desc.name for desc in cursor.description]
                while batch := cursor.fetchmany(8):
                    for values in batch:
                        row = dict(zip(names, values, strict=True))
                        put(archive, 'document_chunk', row)
                        digest.update(encode(row) + b'\n')
                        count += 1
                    archive.commit()
            print(
                json.dumps(
                    {
                        'stage': 'export-vectors',
                        'collections_done': min(offset + 32, len(collections)),
                        'collections_total': len(collections),
                        'rows': count,
                    }
                ),
                flush=True,
            )
        receipt['counts']['document_chunk'] = count
        receipt['digests']['document_chunk'] = digest.hexdigest()
        receipt['collection_ids'] = collections
        receipt['schema'] = reuse.rows(
            source,
            'SELECT table_name,column_name,data_type,udt_name '
            "FROM information_schema.columns WHERE table_schema='public' "
            'AND table_name=ANY(%s) ORDER BY table_name,ordinal_position',
            (list(KEYS),),
        )
        receipt['completed_at'] = int(time.time())
        source.execute('ROLLBACK')
    archive.commit()
    integrity = archive.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity != 'ok':
        raise reuse.ReuseError('private raw archive integrity failed')
    archive.close()
    receipt['archive_bytes'] = (root / 'source.sqlite').stat().st_size
    receipt['archive_sha256'] = file_sha(root / 'source.sqlite')
    private_write(root / 'source-receipt.json', receipt)
    print(
        json.dumps(
            {
                'stage': 'source-complete',
                'counts': receipt['counts'],
                'archive_bytes': receipt['archive_bytes'],
                'archive_sha256': receipt['archive_sha256'],
            }
        ),
        flush=True,
    )


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_ids(conn, kind, identities):
    result = []
    for uid in sorted(set(identities)):
        row = conn.execute('SELECT payload FROM records WHERE kind=? AND id=?', (kind, uid)).fetchone()
        if row:
            result.append(json.loads(zlib.decompress(row[0])))
    return result


def closed_file(file):
    data, meta = reuse.decode(file.get('data')), reuse.decode(file.get('meta'))
    content = data.get('content')
    calculated = (
        hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else file.get('processed_content_sha')
    )
    if calculated is None or calculated != file['hash']:
        return None, 'processed_content_hash_not_closed'
    version = data.get('retrieval_version') or 1
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return None, 'invalid_current_file_version'
    return (data, meta, version), None


def vector_closed(vector, file, meta):
    value = reuse.decode(vector['vmetadata'])
    return (
        value.get('file_id') in (None, file['id'])
        and value.get('hash') in (None, file['hash'])
        and value.get('file_hash') in (None, meta.get('file_hash'))
    )


def fallback_chunk(vector, kb, file, version, now):
    metadata = reuse.decode(vector['vmetadata'])
    return dict(
        chunk_uid=reuse.stable(
            'chunk', legacy_vector_id=vector['id'], file_id=file['id'], version=version, knowledge_id=kb
        ),
        collection_id=None,
        knowledge_id=kb,
        collection_name=kb,
        file_id=file['id'],
        file_version=version,
        chunk_version=1,
        chunk_index=metadata.get('chunk_index'),
        start_index=metadata.get('start_index'),
        content_hash=hashlib.sha256(vector['text'].encode()).hexdigest(),
        chunker_config_hash=reuse.digest({'protocol': 'legacy_manifest_from_original_vector_id'}),
        text=vector['text'],
        metadata={k: v for k, v in metadata.items() if k in legacy.METADATA_KEYS} | {'vector_id': vector['id']},
        is_active=True,
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )


def asset_copy(asset, source_root, destination, source_prefix=None):
    try:
        if not isinstance(asset.get('storage_uri'), str) or not isinstance(asset.get('sha256'), str):
            return None, 'source_asset_metadata_unusable'
        name = 'asset-' + reuse.digest(asset['id']) + '.bin'
        target = destination / name
        if target.exists():
            if file_sha(target) != asset['sha256']:
                raise reuse.ReuseError('asset identity collision')
            return name, None
        original = Path(asset['storage_uri'])
        if source_prefix is not None:
            original = Path(source_root) / original.relative_to(source_prefix)
        path = reuse.confined(original, source_root)
        if path.stat().st_size > reuse.MAX_ASSET_BYTES:
            return None, 'asset_over_individual_budget'
        if file_sha(path) != asset['sha256']:
            return None, 'asset_source_sha_conflict'
        with open(path, 'rb') as source, open(target, 'xb') as output:
            os.chmod(target, 0o600)
            while data := source.read(1024 * 1024):
                output.write(data)
        return name, None
    except (OSError, ValueError, reuse.ReuseError):
        return None, 'asset_missing_or_outside_approved_root'


def prepare(args):
    root = private_root(args.root, create=not args.root.exists())
    read_root = private_root(getattr(args, 'read_root', None) or root)
    source_receipt = json.loads((read_root / 'source-receipt.json').read_text())
    if file_sha(read_root / 'source.sqlite') != source_receipt['archive_sha256']:
        raise reuse.ReuseError('source archive checksum differs')
    conn = sqlite3.connect('file:' + str(read_root / 'source.sqlite') + '?mode=ro', uri=True)
    shard_root, asset_root = root / 'shards', root / 'assets'
    shard_root.mkdir(mode=0o700)
    asset_root.mkdir(mode=0o700)
    now = source_receipt['completed_at']
    canonical_space = legacy.profile(EVIDENCE_SHA, images=True, now=now)
    users = {row['id'] for row in saved(conn, 'user')}
    files = {row['id']: row for row in saved(conn, 'file')}
    knowledge = list(saved(conn, 'knowledge'))
    links = list(saved(conn, 'knowledge_file'))
    spaces = list(saved(conn, 'knowledge_vector_space'))
    maps_by_ref = {}
    for row in saved(conn, 'knowledge_evidence_embedding'):
        if row.get('embedding_status') == 'ready' and row.get('vector_role') == 'image_dense':
            maps_by_ref.setdefault(row['evidence_ref'], []).append(row)
    coverage, shards, totals = [], [], Counter()
    for kbrow in knowledge:
        kb = kbrow['id']
        report = {
            'knowledge_id': kb,
            'owner_present': kbrow['user_id'] in users,
            'files': [],
            'totals': Counter(),
            'isolated': Counter(),
        }
        report['source_active_image_evidence'] = sum(
            row['modality'] == 'image' for row in saved(conn, 'knowledge_evidence', 'kb=? AND active=1', (kb,))
        )
        report['source_manifest_rows'] = conn.execute(
            "SELECT count(*) FROM records WHERE kind='retrieval_chunk' AND kb=?", (kb,)
        ).fetchone()[0]
        kb_spaces = [row for row in spaces if row['knowledge_id'] == kb]
        for link in sorted((row for row in links if row['knowledge_id'] == kb), key=lambda row: row['file_id']):
            fid = link['file_id']
            detail = {'file_id': fid, 'accepted_text': 0, 'accepted_images': 0, 'isolated': Counter()}
            file = files.get(fid)
            if not file:
                detail['isolated']['linked_file_missing'] += 1
                report['files'].append(detail)
                report['isolated'].update(detail['isolated'])
                continue
            closure, error = closed_file(file)
            if error:
                detail['isolated'][error] += 1
                report['files'].append(detail)
                report['isolated'].update(detail['isolated'])
                continue
            data, meta, version = closure
            manifests = list(saved(conn, 'retrieval_chunk', 'kb=? AND file=?', (kb, fid)))
            active = [row for row in manifests if row['is_active'] and row.get('deleted_at') is None]
            detail['source_manifests'], detail['source_active_manifests'] = len(manifests), len(active)
            if not manifests:
                for vector in saved(conn, 'document_chunk', 'col=? AND file=?', (kb, fid)):
                    metadata = reuse.decode(vector['vmetadata'])
                    if metadata.get('modality', 'text') != 'text' or metadata.get('vector_role') not in (
                        None,
                        'text_chunk_dense',
                    ):
                        continue
                    if not vector_closed(vector, file, meta):
                        detail['isolated']['raw_vector_matching_hash_conflict'] += 1
                    elif isinstance(vector.get('text'), str) and vector['text'].strip():
                        active.append(fallback_chunk(vector, kb, file, version, now))
                detail['fallback_manifest_rows'] = len(active)
            chunks = []
            for row in active:
                explicit_version = data.get('retrieval_version')
                if explicit_version is not None and row['file_version'] != explicit_version:
                    detail['isolated']['active_manifest_current_version_conflict'] += 1
                else:
                    chunks.append(row)
            evidence = list(saved(conn, 'knowledge_evidence', 'kb=? AND file=? AND active=1', (kb, fid)))
            assets = list(saved(conn, 'knowledge_evidence_asset', 'kb=? AND file=?', (kb, fid)))
            asset_by_id = {row['id']: row for row in assets if row['status'] == 'ready'}
            images = [row for row in evidence if row['modality'] == 'image']
            detail['source_images'], detail['source_assets'] = len(images), len(assets)
            # Asset bytes, including unaccepted families, are archived separately.
            for asset in assets:
                name, error = asset_copy(
                    asset, args.source_asset_root, asset_root, getattr(args, 'source_asset_prefix', None)
                )
                asset['bundle_file'] = name
                if error:
                    detail['isolated'][error] += 1
            # Bounded shards: at most 350 text + 350 image rows and their mappings.
            groups = [(chunks[n : n + 350], []) for n in range(0, len(chunks), 350)]
            for n in range(0, len(images), 350):
                groups.append((chunks[:1], images[n : n + 350]))
            if images and not chunks:
                detail['isolated']['image_without_current_text_version_seed'] += len(images)
            accepted_refs = {'text': set(), 'image': set()}
            unique_ids = {table: set() for table in reuse.TABLE_KEYS}
            for group_chunks, group_images in groups:
                chunk_ids = {row['chunk_uid'] for row in group_chunks}
                selected_evidence = [
                    row for row in evidence if row.get('retrieval_chunk_uid') in chunk_ids and row['modality'] == 'text'
                ] + group_images
                selected_maps = [row for image in group_images for row in maps_by_ref.get(image['evidence_ref'], [])]
                selected_assets = [
                    asset_by_id[row['asset_id']] for row in group_images if row.get('asset_id') in asset_by_id
                ]
                vector_ids = [reuse.decode(row['metadata']).get('vector_id') for row in group_chunks]
                vector_ids.extend(row['vector_backend_id'] for row in selected_maps)
                vectors = fetch_ids(conn, 'document_chunk', [uid for uid in vector_ids if uid])
                good_vectors = []
                for vector in vectors:
                    if vector_closed(vector, file, meta):
                        good_vectors.append(vector)
                    else:
                        detail['isolated']['original_vector_matching_hash_conflict'] += 1
                bundle = {
                    'format': reuse.FORMAT,
                    'source': source_receipt['source'],
                    'captured_at': now,
                    'scope': {'knowledge_ids': [kb], 'file_ids': [fid], 'limit': 2000},
                    'links': [{'knowledge_id': kb, 'file_id': fid}],
                    'files': [{key: file[key] for key in ('id', 'hash', 'updated_at')}],
                    'chunks': group_chunks,
                    'evidence': selected_evidence,
                    'assets': selected_assets,
                    'mappings': selected_maps,
                    'spaces': kb_spaces,
                    'vectors': good_vectors,
                    'source_closure': {'active_manifest_current_file_snapshot_closed': len(group_chunks)},
                }
                plan = legacy.build_plan(bundle, EVIDENCE_SHA)
                # One existing canonical raw-v1 read profile, even in text-only shards.
                for mapping in plan['tables']['knowledge_evidence_embedding']:
                    mapping['vector_space_id'] = canonical_space['id']
                    mapping['config_fingerprint'] = canonical_space['config_fingerprint']
                    mapping['id'] = reuse.stable('mapping', ref=mapping['evidence_ref'], space=canonical_space['id'])
                if not plan['tables']['knowledge_evidence_embedding']:
                    detail['isolated'].update(plan['isolated'])
                    continue
                plan['tables']['knowledge_vector_space'] = [canonical_space]
                for table, rows in plan['tables'].items():
                    unique_ids[table].update(row[reuse.TABLE_KEYS[table]] for row in rows)
                # Preserve full source metadata in source.sqlite; do not replicate large file.data per vector.
                for table, key in [('document_chunk', 'vmetadata'), ('retrieval_chunk', 'metadata')]:
                    for row in plan['tables'][table]:
                        row[key] = {k: v for k, v in reuse.decode(row[key]).items() if k in legacy.METADATA_KEYS}
                for row in plan['tables']['knowledge_evidence']:
                    accepted_refs[row['modality']].add(row['evidence_ref'])
                name = 'shard-%06d.json' % len(shards)
                # The immutable raw archive already retains the full source.
                # import_plan only needs source/files/links from the bundle;
                # avoid writing a second copy of every wide vector per shard.
                context = {key: bundle[key] for key in ('format', 'source', 'captured_at', 'scope', 'files', 'links')}
                private_write(shard_root / name, {'format': FORMAT, 'bundle': context, 'plan': plan})
                shard = {
                    'name': name,
                    'sha256': file_sha(shard_root / name),
                    'bytes': (shard_root / name).stat().st_size,
                    'knowledge_id': kb,
                    'file_id': fid,
                    'rows': {table: len(rows) for table, rows in plan['tables'].items()},
                }
                shards.append(shard)
                totals.update(shard['rows'])
                detail['isolated'].update(plan['isolated'])
            detail['accepted_text'], detail['accepted_images'] = len(accepted_refs['text']), len(accepted_refs['image'])
            detail['unique_plan_rows'] = {table: len(identities) for table, identities in unique_ids.items()}
            report['files'].append(detail)
            report['totals'].update(
                {
                    'accepted_text': detail['accepted_text'],
                    'accepted_images': detail['accepted_images'],
                    'source_images': detail['source_images'],
                    'source_assets': detail['source_assets'],
                    'current_text_manifests': len(chunks),
                }
            )
            report['isolated'].update(detail['isolated'])
        report['text_searchable'] = report['totals']['accepted_text'] > 0
        report['image_searchable'] = report['totals']['accepted_images'] > 0
        report['all_source_images_searchable'] = (
            report['totals']['accepted_images'] == report['source_active_image_evidence']
        )
        report['raw_rows_archived'] = conn.execute(
            "SELECT count(*) FROM records WHERE kind='document_chunk' AND col=?", (kb,)
        ).fetchone()[0]
        coverage.append(report)
        print(
            json.dumps(
                {
                    'stage': 'prepare',
                    'knowledge_done': len(coverage),
                    'knowledge_total': len(knowledge),
                    'text': report['totals']['accepted_text'],
                    'images': report['totals']['accepted_images'],
                    'isolated': dict(report['isolated']),
                    'shards': len(shards),
                }
            ),
            flush=True,
        )
    asset_files = [
        {'name': path.name, 'bytes': path.stat().st_size, 'sha256': file_sha(path)}
        for path in sorted(asset_root.iterdir())
    ]
    result = {
        'format': FORMAT,
        'source': source_receipt['source'],
        'source_snapshot': source_receipt['snapshot'],
        'source_archive_sha256': source_receipt['archive_sha256'],
        'compatibility_evidence_sha256': EVIDENCE_SHA,
        'required_target_schema': 'c92e41b7d630',
        'canonical_profile': canonical_space['id'],
        'coverage': coverage,
        'shards': shards,
        'asset_files': asset_files,
        'planned_rows_including_replayed_seed_rows': totals,
        'files': [{key: row[key] for key in ('id', 'hash', 'updated_at')} for row in files.values()],
        'links': [{'knowledge_id': row['knowledge_id'], 'file_id': row['file_id']} for row in links],
        'source_writes': 0,
        'embedding_calls': 0,
    }
    unique_counts = Counter()
    for report in coverage:
        for detail in report['files']:
            unique_counts.update(detail.get('unique_plan_rows', {}))
    unique_counts['knowledge_vector_space'] = int(bool(shards))
    result['unique_plan_rows'] = unique_counts
    result['unique_counts_assumption_closed'] = len({row['file_id'] for row in links}) == len(links)
    private_write(root / 'manifest.json', result)
    conn.close()
    print(
        json.dumps(
            {
                'stage': 'prepared',
                'knowledge': len(coverage),
                'text_searchable_kb': sum(row['text_searchable'] for row in coverage),
                'image_searchable_kb': sum(row['image_searchable'] for row in coverage),
                'shards': len(shards),
                'planned_rows': totals,
            }
        ),
        flush=True,
    )


def finalize_bundle(args):
    """Copy completed, verified SSD SQLite once to the HDD deliverable root."""
    root, source = private_root(args.root), private_root(args.read_root)
    manifest = json.loads((root / 'manifest.json').read_text())
    receipt = json.loads((source / 'source-receipt.json').read_text())
    expected = manifest['source_archive_sha256']
    if receipt['archive_sha256'] != expected:
        raise reuse.ReuseError('final bundle source receipt differs')
    path = root / 'source.sqlite'
    with open(source / 'source.sqlite', 'rb') as reader, open(path, 'xb') as output:
        os.chmod(path, 0o600)
        digest = hashlib.sha256()
        while block := reader.read(1024 * 1024):
            digest.update(block)
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if digest.hexdigest() != expected or file_sha(path) != expected:
        raise reuse.ReuseError('HDD final raw archive readback hash differs')
    private_write(root / 'source-receipt.json', receipt)
    summary = {
        'stage': 'bundle-complete',
        'raw_archive_bytes': path.stat().st_size,
        'raw_archive_sha256': expected,
        'manifest_sha256': file_sha(root / 'manifest.json'),
        'shards': len(manifest['shards']),
        'assets': len(manifest['asset_files']),
        'source_writes': 0,
        'embedding_calls': 0,
    }
    private_write(root / 'complete-safe.json', summary)
    print(json.dumps(summary), flush=True)


def complete_assets(args):
    """Archive media independently of text compatibility, then release tree use.

    Rejected text files still retain their original image bytes. Already checked
    private copies use the prepare manifest receipt, not another target-tree read.
    """
    root, source = private_root(args.root), private_root(args.read_root)
    manifest = json.loads((root / 'manifest.json').read_text())
    known = {row['name']: row for row in manifest['asset_files']}
    counts, pending = Counter(), []
    conn = sqlite3.connect('file:' + str(source / 'source.sqlite') + '?mode=ro', uri=True)
    for asset in saved(conn, 'knowledge_evidence_asset'):
        counts['source_assets'] += 1
        name = 'asset-' + reuse.digest(asset['id']) + '.bin'
        if name in known:
            record = known[name]
            if record['sha256'] != asset['sha256'] or (root / 'assets' / name).stat().st_size != record['bytes']:
                raise reuse.ReuseError('captured private asset receipt differs')
            counts['already_sha_verified'] += 1
            continue
        name, error = asset_copy(asset, args.source_asset_root, root / 'assets', args.source_asset_prefix)
        if error:
            counts[error] += 1
            pending.append(
                {
                    'asset_id': asset['id'],
                    'knowledge_id': asset['knowledge_id'],
                    'file_id': asset['file_id'],
                    'reason': error,
                }
            )
            continue
        path = root / 'assets' / name
        known[name] = {'name': name, 'bytes': path.stat().st_size, 'sha256': asset['sha256']}
        counts['new_sha_verified'] += 1
    conn.close()
    manifest['asset_files'] = sorted(known.values(), key=lambda row: row['name'])
    manifest['asset_capture_counts'] = counts
    manifest['pending_assets'] = pending
    previous_sha = file_sha(root / 'manifest.json')
    os.link(root / 'manifest.json', root / 'manifest-before-asset-completion.json')
    private_write(root / 'manifest-with-assets.json', manifest)
    os.replace(root / 'manifest-with-assets.json', root / 'manifest.json')
    summary = {
        'stage': 'asset-tree-released',
        'counts': counts,
        'pending': len(pending),
        'previous_manifest_sha256': previous_sha,
        'manifest_sha256': file_sha(root / 'manifest.json'),
        'all_existing_target_tree_reads_finished': True,
        'target_writes': 0,
    }
    private_write(root / 'asset-tree-release-safe.json', summary)
    print(json.dumps(summary), flush=True)


def import_shards(args):
    """Explicit future target import. Identity arguments are mandatory, never inferred.

    This preparation task does not invoke this mode. Each bounded shard is atomic;
    a repeat rechecks existing row identities and never overwrites vector values.
    """
    root = private_root(args.root)
    manifest = json.loads((root / 'manifest.json').read_text())
    if not args.expected_target_database or not args.expected_target_system_id or not args.asset_root:
        raise reuse.ReuseError('explicit target database, cluster identity and image asset root required')
    if args.expected_target_database in ('webui_db', 'user_test') or args.expected_target_system_id in (
        reuse.SOURCE_SYSTEM_ID,
        '7696569975694159911',
    ):
        raise reuse.ReuseError('existing Live and test targets forbidden')
    if file_sha(root / 'source.sqlite') != manifest['source_archive_sha256']:
        raise reuse.ReuseError('source archive checksum differs')
    # Reuse the existing narrow importer with an explicitly pinned NEW target.
    # No on-disk change to the shared tool and no relaxation of the source guard.
    reuse.TARGET_DATABASE = args.expected_target_database
    reuse.TARGET_SYSTEM_ID = args.expected_target_system_id
    completed = Counter()
    with reuse.connect() as conn:
        actual = reuse.identity(conn)
        reuse.validate_identity(actual, target=True, source=manifest['source'])
        for index, item in enumerate(manifest['shards']):
            if index < args.start_shard or (args.stop_shard is not None and index >= args.stop_shard):
                continue
            shard_path = root / 'shards' / item['name']
            if file_sha(shard_path) != item['sha256']:
                raise reuse.ReuseError('shard checksum differs')
            shard = json.loads(shard_path.read_text())
            reuse.begin(conn, readonly=not args.apply)
            try:
                legacy.preserve_existing_metadata(conn, shard['plan'])
                reuse.prepare_assets(shard['plan'], root / 'assets', args.asset_root, apply=False)
                result = reuse.import_plan(conn, shard['bundle'], shard['plan'], apply=False)
                if args.apply:
                    reuse.prepare_assets(shard['plan'], root / 'assets', args.asset_root, apply=True)
                    result = reuse.import_plan(conn, shard['bundle'], shard['plan'], apply=True)
                conn.execute('COMMIT' if args.apply else 'ROLLBACK')
            except Exception:
                conn.execute('ROLLBACK')
                raise
            completed.update(result['counts'])
            print(
                json.dumps(
                    {
                        'stage': 'applied' if args.apply else 'dry-run',
                        'shard_index': index,
                        'shard_sha256': item['sha256'],
                        'target': actual,
                        'counts': result['counts'],
                        'embedding_calls': 0,
                    }
                ),
                flush=True,
            )
    print(json.dumps({'stage': 'import-complete', 'applied': args.apply, 'counts': completed}), flush=True)


def main():
    os.umask(0o077)
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        'mode',
        choices=[
            'export',
            'extract-small',
            'extract-large',
            'archive-direct',
            'archive-dump',
            'checkpoint-scratch',
            'prepare',
            'complete-assets',
            'finalize-bundle',
            'import',
        ],
    )
    cli.add_argument('--root', type=Path, required=True)
    cli.add_argument('--source-asset-root', type=Path)
    cli.add_argument('--source-asset-prefix', type=Path)
    cli.add_argument('--expected-target-database')
    cli.add_argument('--expected-target-system-id')
    cli.add_argument('--asset-root', type=Path)
    cli.add_argument('--apply', action='store_true')
    cli.add_argument('--start-shard', type=int, default=0)
    cli.add_argument('--stop-shard', type=int)
    cli.add_argument('--small-data', type=Path)
    cli.add_argument('--large-data', type=Path)
    cli.add_argument('--dump-sha256')
    cli.add_argument('--snapshot-note')
    cli.add_argument('--dump-file', type=Path)
    cli.add_argument('--dump-receipt', type=Path)
    cli.add_argument('--resume', action='store_true')
    cli.add_argument('--source-root', type=Path)
    cli.add_argument('--source-pid', type=int)
    cli.add_argument('--read-root', type=Path)
    args = cli.parse_args()
    if args.mode == 'export':
        export(args)
    elif args.mode in ('extract-small', 'extract-large'):
        extract_dump(args)
    elif args.mode == 'checkpoint-scratch':
        checkpoint_scratch(args)
    elif args.mode == 'archive-direct':
        if not args.dump_file or not args.dump_receipt:
            raise reuse.ReuseError('explicit owner dump and receipt required')
        archive_direct(args)
    elif args.mode == 'archive-dump':
        archive_dump(args)
    elif args.mode == 'complete-assets':
        complete_assets(args)
    elif args.mode == 'finalize-bundle':
        finalize_bundle(args)
    elif args.mode == 'prepare':
        if not args.source_asset_root:
            raise reuse.ReuseError('explicit approved source asset root required')
        prepare(args)
    else:
        import_shards(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    'failed': True,
                    'category': type(error).__name__,
                    'reason': str(error) if isinstance(error, reuse.ReuseError) else 'restricted diagnostic',
                }
            ),
            flush=True,
        )
        raise SystemExit(1) from None
