"""Import an archived Live snapshot into an isolated official-schema database.

The operator restores immutable source/test archives into separate databases on
the *target* PostgreSQL server. This program never connects to production. The
target app must be stopped. Passwords and the automation account id live only in
the operator's private JSON configuration. No content is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import os
import threading
from datetime import datetime


BUSINESS_TABLES = (
    "user", "auth", "api_key", "oauth_session", "group", "group_member",
    "folder", "file", "knowledge", "knowledge_directory", "knowledge_file",
    "chat", "chat_message", "chat_file", "shared_chat", "tag", "chatidtag",
    "note", "pinned_note", "memory", "channel", "channel_member",
    "message", "channel_file", "channel_webhook", "message_reaction",
    "prompt", "prompt_history", "feedback", "calendar", "calendar_event",
    "calendar_event_attendee", "automation", "automation_run", "document",
    "access_grant",
)


def business_key_columns(table):
    if table in ("folder", "tag"):
        return ("id", "user_id")
    return ("collection_name",) if table == "document" else ("id",)


def receipt_keys(table, keys):
    """JSON receipts keep the complete official primary key, not just id."""
    return [list(k) if len(business_key_columns(table)) > 1 else k[0] for k in keys]


def keys_from_receipt(table, values):
    return [tuple(v) if len(business_key_columns(table)) > 1 else (v,) for v in values]


def common_columns(source, target):
    """Keep official order; only text/varchar have identical binary payloads."""
    old = {x["name"]: x for x in source}
    result = []
    for c in target:
        if c["name"] in old:
            source_type = old[c["name"]]["type"]
            if source_type != c["type"] and {source_type, c["type"]} != {"text", "varchar"}:
                raise ValueError("column type changed: " + c["name"])
            result.append(c["name"])
    return result


class HashSink:
    def __init__(self, stream=None, capacity_guard=None):
        self.digest = hashlib.sha256()
        self.stream = stream
        self.capacity_guard = capacity_guard
        self.since_check = 0
        self.total_bytes = 0

    def write(self, data):
        self.since_check += len(data)
        self.total_bytes += len(data)
        if self.capacity_guard is not None and self.since_check >= 64 * 1024 * 1024:
            self.capacity_guard()
            self.since_check = 0
        self.digest.update(data)
        if self.stream is not None:
            pending = memoryview(data)
            while pending:
                written = self.stream.write(pending)
                if not written:
                    raise OSError("binary COPY pipe stopped accepting data")
                pending = pending[written:]
        return len(data)


def stream_binary_copy(source_cursor, source_query, target_cursor, target_query, capacity_guard=None, stats=None):
    """Bounded pipe: don't allocate another full DB/table on the small SSD."""
    read_fd, write_fd = os.pipe()
    errors = []
    writer = os.fdopen(write_fd, "wb", buffering=0)
    sink = HashSink(writer, capacity_guard)

    def produce():
        try:
            source_cursor.copy_expert(source_query, sink)
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer.close()

    thread = threading.Thread(target=produce, daemon=True)
    thread.start()
    try:
        with os.fdopen(read_fd, "rb", buffering=0) as reader:
            target_cursor.copy_expert(target_query, reader)
    finally:
        thread.join()
    if errors:
        raise errors[0]
    if stats is not None:
        stats["bytes"] = sink.total_bytes
    return sink.digest.hexdigest()


def run(config_path, receipt_path):
    # Delayed import lets the mapping helpers be tested without a DB driver.
    import psycopg2
    from psycopg2 import sql

    c = json.loads(pathlib.Path(config_path).read_text())
    assert c["database"] == "webui_live"
    assert c["source_database"] != c["database"]
    assert c["test_database"] != c["database"]
    assert c["host"] == "owui-v0120-live-pg-20261017"
    out = pathlib.Path(receipt_path)
    assert out.parent.is_dir()
    def capacity_guard():
        usage = os.statvfs(c["ssd_stat_path"])
        assert usage.f_bavail * usage.f_frsize >= c["minimum_free_bytes"], "shared SSD reserve would be breached"
    capacity_guard()
    kwargs = dict(host=c["host"], user=c["username"], password=c["password"])
    connections = [psycopg2.connect(dbname=c[k], **kwargs) for k in
                   ("database", "source_database", "test_database")]
    dst, src, test = connections
    result = {"started": datetime.now().astimezone().isoformat(), "tables": {},
              "target_system_identifier": c["system_identifier"],
              "source_database": c["source_database"],
              "test_database": c["test_database"], "target_database": c["database"]}

    def identifier(value):
        return sql.Identifier(value)

    def key_condition(table, values):
        keys = business_key_columns(table)
        if not values:
            return sql.SQL("FALSE")
        if len(keys) == 1:
            return sql.SQL("{} = ANY({})").format(identifier(keys[0]), sql.Literal([x[0] for x in values]))
        return sql.SQL("({}) IN ({})").format(
            sql.SQL(",").join(map(identifier, keys)),
            sql.SQL(",").join(sql.SQL("({})").format(sql.SQL(",").join(map(sql.Literal, value))) for value in values))

    def columns(conn, table):
        with conn.cursor() as cur:
            cur.execute("SELECT column_name,udt_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s "
                        "ORDER BY ordinal_position", (table,))
            return [{"name": a, "type": b} for a, b in cur]

    def copy_stage(conn, table, stage, cols):
        names = sql.SQL(",").join(map(identifier, cols))
        with conn.cursor() as source_cur, dst.cursor() as target_cur:
            target_cur.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE public.{} INCLUDING DEFAULTS)").format(
                identifier(stage), identifier(table)))
            q = sql.SQL("COPY (SELECT {} FROM public.{}) TO STDOUT (FORMAT BINARY)").format(names, identifier(table))
            z = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT BINARY)").format(identifier(stage), names)
            digest = stream_binary_copy(source_cur, q.as_string(conn), target_cur, z.as_string(dst), capacity_guard)
            target_cur.execute(sql.SQL("SELECT count(*) FROM {}").format(identifier(stage)))
            count = target_cur.fetchone()[0]
        return count, digest

    def projection_hash(conn, table, cols, condition):
        # Ordered binary COPY covers full JSON/text/credential bytes, not only IDs.
        names = sql.SQL(",").join(map(identifier, cols))
        keys = sql.SQL(",").join(map(identifier, business_key_columns(table)))
        sink = HashSink()
        with conn.cursor() as cur:
            query = sql.SQL("COPY (SELECT {} FROM public.{} WHERE {} ORDER BY {}) "
                            "TO STDOUT (FORMAT BINARY)").format(names, identifier(table), condition, keys)
            cur.copy_expert(query.as_string(conn), sink)
        return sink.digest.hexdigest()

    try:
        for conn in connections:
            with conn.cursor() as cur:
                cur.execute("SELECT system_identifier::text FROM pg_control_system()")
                assert cur.fetchone()[0] == str(c["system_identifier"]), "wrong PostgreSQL cluster"
        with dst.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()[0] == "c92e41b7d630", "wrong official schema"
            cur.execute("SELECT ch.relname,p.relname FROM pg_constraint c "
                        "JOIN pg_class ch ON ch.oid=c.conrelid JOIN pg_class p ON p.oid=c.confrelid "
                        "WHERE c.contype='f'")
            positions = {t: i for i, t in enumerate(BUSINESS_TABLES)}
            for child, parent in cur:
                if child in positions and parent in positions and child != parent:
                    assert positions[parent] < positions[child], "official FK load order changed: " + parent + " -> " + child
        # The staging databases are immutable restored snapshots, not live sources.
        for conn in (src, test):
            conn.rollback()
            conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        mappings = {}
        refresh = c.get("mode", "initial") == "refresh"
        previous = json.loads(pathlib.Path(c["previous_receipt_path"]).read_text()) if refresh else None
        with dst.cursor() as cur:
            cur.execute("SELECT id FROM tool ORDER BY id")
            tool_ids = [r[0] for r in cur]
        def live_condition(table):
            if table == "access_grant":
                return sql.SQL("resource_type NOT IN ('model','skill','tool') OR "
                               "(resource_type='tool' AND resource_id = ANY({}))").format(sql.Literal(tool_ids))
            return sql.SQL("TRUE")
        for table in BUSINESS_TABLES:
            sc, tc = columns(src, table), columns(dst, table)
            assert sc and tc, "missing supported business table: " + table
            with dst.cursor() as cur:
                cur.execute("SELECT a.attname FROM pg_index i "
                            "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord) ON true "
                            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
                            "WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.ord", ("public."+table,))
                assert tuple(r[0] for r in cur) == business_key_columns(table), "official primary key changed: " + table
            cols = common_columns(sc, tc)
            test_cols = [x["name"] for x in tc]
            test_count, test_digest = copy_stage(test, table, "test_" + table, test_cols)
            mappings[table] = (cols, test_cols)
            result["tables"][table] = {
                "test_rows": test_count, "test_binary_sha256": test_digest,
                "common_columns": cols,
                "archive_only_columns": [x["name"] for x in sc if x["name"] not in cols],
                "new_columns": [x["name"] for x in tc if x["name"] not in cols],
                "key_columns": list(business_key_columns(table)),
            }
        with dst.cursor() as cur:
            # Only these explicitly owned target business tables are replaced.
            if not refresh:
                cur.execute(sql.SQL("TRUNCATE {} ").format(sql.SQL(",").join(
                    sql.SQL("public.{}").format(identifier(t)) for t in BUSINESS_TABLES)))
            for table in BUSINESS_TABLES:
                capacity_guard()
                cols = mappings[table][0]
                names = sql.SQL(",").join(map(identifier, cols))
                key_cols = business_key_columns(table)
                key_names = sql.SQL(",").join(map(identifier, key_cols))
                where = live_condition(table)
                with src.cursor() as scur:
                    value_columns = sql.SQL("{},md5(ROW({})::text)").format(key_names,names) if refresh else key_names
                    scur.execute(sql.SQL("SELECT {} FROM public.{} WHERE {} ORDER BY {}").format(
                        value_columns, identifier(table), where, key_names))
                    rows = scur.fetchall()
                    source_rows = {tuple(row[:-1]) if refresh else tuple(row): row[-1] if refresh else None for row in rows}
                    assert len(rows) == len(source_rows), "source violates official target key: " + table
                    assert all(all(value is not None for value in key) for key in source_rows), "source has NULL official target key: " + table
                ids = list(source_rows)
                result["tables"][table]["live_rows"] = len(ids)
                result["tables"][table]["live_source_ids"] = receipt_keys(table, ids)
                selected = ids
                if refresh:
                    cur.execute(sql.SQL("SELECT {},md5(ROW({})::text) FROM public.{} WHERE {}").format(
                        key_names, names, identifier(table), key_condition(table, ids)))
                    target_rows = {tuple(row[:-1]): row[-1] for row in cur}
                    selected = [i for i in ids if source_rows[i] != target_rows.get(i)]
                if selected:
                    condition = key_condition(table, selected)
                    to_table = table
                    if refresh:
                        to_table = "delta_" + table
                        cur.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE public.{} INCLUDING DEFAULTS)").format(
                            identifier(to_table), identifier(table)))
                    q = sql.SQL("COPY (SELECT {} FROM public.{} WHERE {} ORDER BY {}) TO STDOUT (FORMAT BINARY)").format(
                        names, identifier(table), condition, key_names)
                    z = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT BINARY)").format(identifier(to_table), names)
                    with src.cursor() as scur:
                        digest = stream_binary_copy(scur, q.as_string(src), cur, z.as_string(dst), capacity_guard)
                    result["tables"][table]["live_copied_binary_sha256"] = digest
                    if refresh:
                        updates = sql.SQL(",").join(sql.SQL("{}=EXCLUDED.{}").format(identifier(x),identifier(x)) for x in cols if x not in key_cols)
                        cur.execute(sql.SQL("INSERT INTO public.{} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO UPDATE SET {}").format(
                            identifier(table),names,names,identifier(to_table),key_names,updates))
                        cur.execute(sql.SQL("DROP TABLE {}").format(identifier(to_table)))
                result["tables"][table]["live_imported"] = len(selected)
                result["tables"][table]["live_unchanged_row_hash_match"] = len(ids)-len(selected)
                cols = mappings[table][1]
                names = sql.SQL(",").join(map(identifier, cols))
                key_equals = sql.SQL(" AND ").join(sql.SQL("p.{}=t.{}").format(identifier(k),identifier(k)) for k in key_cols)
                conditions = [sql.SQL("NOT EXISTS (SELECT 1 FROM public.{} p WHERE {})").format(identifier(table), key_equals)]
                params = []
                if table in ("user", "auth"):
                    conditions.append(sql.SQL("t.id <> %s"));params.append(c["test_automation_user_id"])
                elif table in ("file", "knowledge"):
                    # Required retrieval seeds remain private to their missing test
                    # owner. The test automation account is never published.
                    conditions.append(sql.SQL("t.user_id <> %s OR t.user_id IS NULL OR "
                                              "t.id IN (SELECT {} FROM knowledge_evidence)").format(
                        identifier("file_id" if table == "file" else "knowledge_id")))
                    params.append(c["test_automation_user_id"])
                elif "user_id" in cols and table not in ("access_grant", "knowledge_file", "knowledge_directory"):
                    conditions.append(sql.SQL("t.user_id IS NULL OR t.user_id IN (SELECT id FROM public.\"user\")"))
                if table == "knowledge_file":
                    conditions.extend([sql.SQL("t.file_id IN (SELECT id FROM public.file)"),
                                       sql.SQL("t.knowledge_id IN (SELECT id FROM public.knowledge)")])
                if table in ("chat_message", "chat_file"):
                    conditions.append(sql.SQL("t.chat_id IN (SELECT id FROM public.chat)"))
                if table == "access_grant":
                    # Live owns permissions on shared business resources. Stale
                    # test grants must not re-publish access removed on Live.
                    # Synthetic seed KBs keep their data but get no public grants.
                    conditions.append(sql.SQL("t.resource_type IN ('model','tool') OR "
                        "(t.resource_type='knowledge' AND t.resource_id IN "
                        "(SELECT id FROM public.knowledge WHERE user_id IN (SELECT id FROM public.\"user\")) "
                        "AND NOT (t.resource_id = ANY({}))) OR "
                        "(t.resource_type='prompt' AND t.resource_id IN "
                        "(SELECT id FROM public.prompt WHERE user_id IN (SELECT id FROM public.\"user\")) "
                        "AND NOT (t.resource_id = ANY({})))").format(
                            sql.Literal(result["tables"]["knowledge"]["live_source_ids"]),
                            sql.Literal(result["tables"]["prompt"]["live_source_ids"])))
                    # Preserve source ACL IDs; don't duplicate equivalent grants.
                    conditions.append(sql.SQL("NOT EXISTS (SELECT 1 FROM public.access_grant p WHERE "
                        "p.resource_type=t.resource_type AND p.resource_id=t.resource_id AND "
                        "p.principal_type=t.principal_type AND p.principal_id=t.principal_id AND "
                        "p.permission=t.permission)"))
                conditions = [sql.SQL("({})").format(x) for x in conditions]
                cur.execute(sql.SQL("INSERT INTO public.{} ({}) SELECT {} FROM {} t WHERE {}").format(
                    identifier(table), names,
                    sql.SQL(",").join(sql.SQL("t.{}").format(identifier(x)) for x in cols),
                    identifier("test_" + table), sql.SQL(" AND ").join(conditions)), params)
                result["tables"][table]["test_only_retained"] = cur.rowcount
                cur.execute(sql.SQL("SELECT count(*) FROM public.{}").format(identifier(table)))
                result["tables"][table]["target_rows"] = cur.fetchone()[0]
                print(json.dumps({"staged_business_table": table,
                                  "live_rows": len(ids),
                                  "live_rows_copied": len(selected),
                                  "test_only_retained": result["tables"][table]["test_only_retained"]}), flush=True)
        if refresh:
            with dst.cursor() as cur:
                for table in reversed(BUSINESS_TABLES):
                    old_ids = keys_from_receipt(table, previous["tables"][table]["live_source_ids"])
                    current_ids = set(keys_from_receipt(table, result["tables"][table]["live_source_ids"]))
                    deleted = [x for x in old_ids if x not in current_ids]
                    cur.execute(sql.SQL("DELETE FROM public.{} WHERE {}").format(identifier(table),key_condition(table, deleted)))
                    result["tables"][table]["deleted_since_previous_live_snapshot"] = cur.rowcount
                    cur.execute(sql.SQL("SELECT count(*) FROM public.{}").format(identifier(table)))
                    result["tables"][table]["target_rows"] = cur.fetchone()[0]
        # Full common-field equality for every imported Live row, before commit.
        for table, (cols, _) in mappings.items():
            ids = keys_from_receipt(table, result["tables"][table]["live_source_ids"])
            condition = key_condition(table, ids)
            # Initial COPY is already ordered by the full official key. Its
            # incremental SHA is the complete immutable source projection;
            # don't serialize multi-GB chats twice merely to obtain it again.
            a = result["tables"][table]["live_copied_binary_sha256"] if not refresh and ids else projection_hash(src, table, cols, condition)
            b = projection_hash(dst, table, cols, condition)
            assert a == b, "full-field mismatch: " + table
            result["tables"][table]["live_exact_projection_sha256"] = a
            result["tables"][table]["live_full_field_match"] = True
            print(json.dumps({"full_field_hash_verified": table}), flush=True)
        capacity_guard()
        dst.commit()
        result["committed"] = True
        result["finished"] = datetime.now().astimezone().isoformat()
        out.write_text(json.dumps(result, indent=2));out.chmod(0o600)
        print(json.dumps({"committed": True, "tables": {
            t: {k: v for k, v in r.items() if k in (
                "live_rows", "live_imported", "test_only_retained", "target_rows", "live_full_field_match")}
            for t, r in result["tables"].items()}}, indent=2))
    finally:
        for conn in connections:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    run(args.config, args.receipt)
