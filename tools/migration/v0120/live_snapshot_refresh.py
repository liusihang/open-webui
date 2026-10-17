"""Refresh only the isolated old-schema snapshot from a read-only source.

Production is SELECT/COPY TO only, in one verified REPEATABLE READ READ ONLY
transaction. All I/U/D, temporary tables and trigger suppression are confined
to the explicitly named old-schema staging DB in the NEW PostgreSQL cluster.
The official-schema importer remains a separate snapshot-only program.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import threading
import time
from datetime import datetime

from live_business_import import BUSINESS_TABLES, HashSink, stream_binary_copy

NEW_SYSTEM_ID = "7697307088288014375"
LIVE_SYSTEM_ID = "7615593547098730535"
OLD_SCHEMA = "a11c0d3f0bd0"
PROOF_SOURCE = "business_snapshot_proof_source_20261017"
PROOF_TARGET = "business_snapshot_proof_target_20261017"


def validate_config(c):
    mode = c.get("mode", "live")
    assert mode in ("live", "offline-proof"), "unknown snapshot refresh mode"
    source, target = c["source"], c["target"]
    assert target["system_identifier"] == NEW_SYSTEM_ID, "wrong staging cluster"
    if mode == "live":
        assert source["database"] == "webui_db"
        assert source["system_identifier"] == LIVE_SYSTEM_ID
        assert target["database"] == "live_snapshot"
        assert source["host"] != target["host"]
    else:
        assert source["database"] == PROOF_SOURCE
        assert target["database"] == PROOF_TARGET
        assert source["system_identifier"] == NEW_SYSTEM_ID
    assert source["database"] != target["database"]
    assert c["source_schema_version"] == OLD_SCHEMA
    assert c["minimum_free_bytes"] >= 15 * 2**30


def connect_source(c):
    import psycopg2

    # The libpq default is already read-only before any SQL can run; the first
    # transaction additionally pins one snapshot for every supported table.
    conn = psycopg2.connect(
        host=c["host"], port=c.get("port", 5432), dbname=c["database"],
        user=c["username"], password=c["password"], connect_timeout=15,
        options="-c default_transaction_read_only=on -c timezone=UTC -c datestyle=ISO,YMD -c extra_float_digits=3",
        application_name="owui_readonly_snapshot_refresh_20261017",
    )
    conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
    return conn


def refresh(config_path, receipt_path):
    import psycopg2
    from psycopg2 import sql

    c = json.loads(pathlib.Path(config_path).read_text())
    validate_config(c)
    out = pathlib.Path(receipt_path)
    assert out.parent.is_dir() and not out.exists(), "receipt must be new"

    def capacity_guard(extra_bytes=0):
        v = os.statvfs(c["ssd_stat_path"])
        assert v.f_bavail * v.f_frsize >= c["minimum_free_bytes"] + extra_bytes, "shared SSD reserve would be breached"

    capacity_guard()
    src = connect_source(c["source"])
    t = c["target"]
    try:
        dst = psycopg2.connect(host=t["host"], port=t.get("port", 5432), dbname=t["database"],
                               user=t["username"], password=t["password"], connect_timeout=15,
                               options="-c timezone=UTC -c datestyle=ISO,YMD -c extra_float_digits=3",
                               application_name="owui_owned_snapshot_write_20261017")
    except BaseException:
        src.close()
        raise
    dst.set_session(isolation_level="REPEATABLE READ")
    result = {"started": datetime.now().astimezone().isoformat(), "mode": c.get("mode", "live"),
              "source_business_writes": 0, "tables": {}, "committed": False,
              "source_container_id": c.get("source_container_id"),
              "target_container_id": c.get("target_container_id")}
    start = time.monotonic()

    def identify(conn, expected, readonly=False):
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(),system_identifier::text,"
                        "current_setting('transaction_read_only'),current_setting('transaction_isolation'),"
                        "pg_current_snapshot()::text,current_setting('server_encoding'),"
                        "current_setting('client_encoding') FROM pg_control_system()")
            name, sysid, ro, isolation, snapshot, server_encoding, client_encoding = cur.fetchone()
            assert name == expected["database"] and sysid == expected["system_identifier"], "wrong PostgreSQL identity"
            assert server_encoding == client_encoding == "UTF8", "binary snapshot requires UTF8 source/staging"
            if readonly:
                assert ro == "on" and isolation == "repeatable read", "source must be RR READ ONLY"
            return {"database": name, "system_identifier": sysid, "readonly": ro,
                    "isolation": isolation, "snapshot": snapshot}

    def metadata(conn, table):
        with conn.cursor() as cur:
            cur.execute("SELECT column_name,udt_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,))
            columns = list(cur)
            cur.execute("SELECT a.attname FROM pg_index i "
                        "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(n,o) ON true "
                        "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.n "
                        "WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.o", ("public."+table,))
            keys = tuple(row[0] for row in cur)
        assert columns and keys, "missing snapshot columns/primary key: " + table
        return columns, keys

    def condition(keys, values):
        if not values:
            return sql.SQL("FALSE")
        names = sql.SQL(",").join(map(sql.Identifier, keys))
        if len(keys) == 1:
            return sql.SQL("{}=ANY({})").format(names, sql.Literal([x[0] for x in values]))
        return sql.SQL("({}) IN ({})").format(names, sql.SQL(",").join(
            sql.SQL("({})").format(sql.SQL(",").join(map(sql.Literal, row))) for row in values))

    def row_hashes(conn, table, names, keys):
        begin = time.monotonic()
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT {},md5(ROW({})::text) FROM public.{} ORDER BY {}").format(
                sql.SQL(",").join(map(sql.Identifier, keys)), names, sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, keys))))
            rows = cur.fetchall()
        values = {tuple(row[:-1]): row[-1] for row in rows}
        assert len(values) == len(rows) and all(all(x is not None for x in k) for k in values), "invalid complete source key"
        return values, time.monotonic()-begin

    def projection_hash(conn, table, names, keys, values):
        sink = HashSink()
        with conn.cursor() as cur:
            q = sql.SQL("COPY (SELECT {} FROM public.{} WHERE {} ORDER BY {}) TO STDOUT (FORMAT BINARY)").format(
                names, sql.Identifier(table), condition(keys, values), sql.SQL(",").join(map(sql.Identifier, keys)))
            cur.copy_expert(q.as_string(conn), sink)
        return sink.digest.hexdigest()

    try:
        result["source"] = identify(src, c["source"], readonly=True)
        result["target"] = identify(dst, c["target"])
        with src.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert [r[0] for r in cur] == [c["source_schema_version"]], "source schema version changed"
        with dst.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            versions = [r[0] for r in cur]
            assert versions in ([], [c["source_schema_version"]]), "not the owned old-schema staging DB"
            cur.execute("SELECT pg_try_advisory_xact_lock(7697307088288014)")
            assert cur.fetchone()[0], "another owned snapshot refresh is running"
            # Target-only. The source snapshot deliberately excludes old model,
            # mode-profile and vector parent data; it is never an app database.
            cur.execute("SET LOCAL session_replication_role='replica'")
        mappings = {}
        for table in BUSINESS_TABLES:
            sc, sk = metadata(src, table)
            tc, tk = metadata(dst, table)
            assert sc == tc and sk == tk, "source/staging schema changed: " + table
            mappings[table] = (sc, sk)
        for table, (cols, keys) in mappings.items():
            capacity_guard()
            begin = time.monotonic()
            names = sql.SQL(",").join(sql.Identifier(x[0]) for x in cols)
            key_names = sql.SQL(",").join(map(sql.Identifier, keys))
            # Independent connections: overlap only these two bounded hash
            # scans, never source writes or multiple inconsistent snapshots.
            source_result = []
            errors = []
            def read_source():
                try:
                    source_result.append(row_hashes(src, table, names, keys))
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=read_source, daemon=True)
            thread.start()
            try:
                before, target_seconds = row_hashes(dst, table, names, keys)
            finally:
                thread.join()
            if errors:
                raise errors[0]
            current, source_seconds = source_result[0]
            inserted = [k for k in current if k not in before]
            updated = [k for k in current if k in before and current[k] != before[k]]
            selected = inserted + updated
            deleted = [k for k in before if k not in current]
            entry = {"key_columns": list(keys), "columns": [x[0] for x in cols],
                     "source_rows": len(current), "previous_rows": len(before),
                     "inserted": len(inserted), "updated": len(updated), "deleted": len(deleted),
                     "inserted_keys": inserted, "updated_keys": updated, "deleted_keys": deleted,
                     "unchanged_complete_row_hash_match": len(current)-len(selected),
                     "source_hash_seconds": source_seconds, "target_hash_seconds": target_seconds}
            entry["complete_source_key_rowhash_map_sha256"] = hashlib.sha256(json.dumps(
                [[list(k),current[k]] for k in sorted(current)],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            with dst.cursor() as cur:
                if selected:
                    stage = "snapshot_delta_"+table
                    cur.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE public.{} INCLUDING DEFAULTS)").format(
                        sql.Identifier(stage), sql.Identifier(table)))
                    q = sql.SQL("COPY (SELECT {} FROM public.{} WHERE {} ORDER BY {}) TO STDOUT (FORMAT BINARY)").format(
                        names, sql.Identifier(table), condition(keys, selected), key_names)
                    z = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT BINARY)").format(sql.Identifier(stage), names)
                    copy_stats = {}
                    with src.cursor() as source_cur:
                        digest = stream_binary_copy(source_cur, q.as_string(src), cur, z.as_string(dst), capacity_guard, copy_stats)
                    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(stage)))
                    assert cur.fetchone()[0] == len(selected), "delta COPY row count mismatch"
                    cur.execute("SELECT pg_total_relation_size(%s::regclass)",(stage,))
                    stored_bytes = cur.fetchone()[0]
                    # Upsert and WAL can temporarily coexist with both the old
                    # row version and temp COPY. Don't spend the reserve merely
                    # because the compressed temp relation happened to fit.
                    peak_extra = max(2*copy_stats["bytes"],4*stored_bytes)
                    capacity_guard(peak_extra)
                    entry["delta_binary_bytes"] = copy_stats["bytes"]
                    entry["pre_upsert_extra_reserve_bytes"] = peak_extra
                    updates = sql.SQL(",").join(sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(x[0]),sql.Identifier(x[0])) for x in cols if x[0] not in keys)
                    action = sql.SQL("DO UPDATE SET {}").format(updates) if any(x[0] not in keys for x in cols) else sql.SQL("DO NOTHING")
                    cur.execute(sql.SQL("INSERT INTO public.{} ({}) SELECT {} FROM {} ON CONFLICT ({}) {}").format(
                        sql.Identifier(table), names, names, sql.Identifier(stage), key_names, action))
                    cur.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(stage)))
                    assert projection_hash(dst, table, names, keys, selected) == digest, "changed full-field binary SHA mismatch: " + table
                    entry["changed_rows_binary_sha256"] = digest
                cur.execute(sql.SQL("DELETE FROM public.{} WHERE {}").format(sql.Identifier(table),condition(keys,deleted)))
                assert cur.rowcount == len(deleted), "deleted key count mismatch"
                cur.execute(sql.SQL("SELECT {} FROM public.{}").format(key_names,sql.Identifier(table)))
                assert {tuple(r) for r in cur} == set(current), "complete final key set mismatch: " + table
            capacity_guard()
            entry["complete_keys_match"] = True
            entry["full_fields_verified"] = True
            entry["seconds"] = time.monotonic()-begin
            result["tables"][table] = entry
            print(json.dumps({"snapshot_table": table, **{k:entry[k] for k in
                ("source_rows","inserted","updated","deleted","seconds")}}),flush=True)
        end_identity = identify(src, c["source"], readonly=True)
        assert end_identity["snapshot"] == result["source"]["snapshot"], "source snapshot changed mid-refresh"
        result["source_readonly_reverified"] = True
        with dst.cursor() as cur:
            if not versions:
                cur.execute("INSERT INTO alembic_version(version_num) VALUES(%s)",(c["source_schema_version"],))
        capacity_guard()
        dst.commit()
        result.update(committed=True, finished=datetime.now().astimezone().isoformat(), seconds=time.monotonic()-start)
        out.write_text(json.dumps(result,indent=2));out.chmod(0o600)
        print(json.dumps({k:v for k,v in result.items() if k != "tables"},indent=2),flush=True)
        return result
    finally:
        src.rollback()
        src.close()
        dst.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",required=True)
    p.add_argument("--receipt",required=True)
    a = p.parse_args()
    refresh(a.config,a.receipt)
