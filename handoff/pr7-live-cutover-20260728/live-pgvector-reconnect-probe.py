#!/usr/bin/env python3
from __future__ import annotations

import json

from open_webui.internal.db import ScopedSession, engine
from open_webui.retrieval.vector.dbs.pgvector import DocumentChunk, PgvectorClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError


def terminate_checked_out_connection() -> None:
    backend_pid = ScopedSession.execute(text('SELECT pg_backend_pid()')).scalar_one()
    with engine.connect() as killer:
        terminated = killer.execute(
            text('SELECT pg_terminate_backend(:backend_pid)'),
            {'backend_pid': backend_pid},
        ).scalar_one()
    if terminated is not True:
        raise RuntimeError('probe could not terminate its own checked-out database connection')


def main() -> None:
    client = PgvectorClient.__new__(PgvectorClient)
    client.session = ScopedSession
    try:
        row = ScopedSession.execute(
            select(DocumentChunk.collection_name, DocumentChunk.vector)
            .where(DocumentChunk.vector.is_not(None))
            .limit(1)
        ).one_or_none()
        if row is None:
            raise RuntimeError('no pgvector collection is available for the reconnect probe')
        collection_name, vector = row
        query_vector = vector.to_list()

        terminate_checked_out_connection()
        get_result = client.get(collection_name=collection_name, limit=1)
        if get_result is None or not get_result.ids or not get_result.ids[0]:
            raise RuntimeError('pgvector get did not recover after its own connection was invalidated')
        ScopedSession.remove()

        ScopedSession.execute(text('SELECT 1')).scalar_one()
        terminate_checked_out_connection()
        search_result = client.search(
            collection_name=collection_name,
            vectors=[query_vector],
            limit=1,
        )
        if search_result is None or not search_result.ids or not search_result.ids[0]:
            raise RuntimeError('pgvector search did not recover after its own connection was invalidated')

        print(
            json.dumps(
                {
                    'ok': True,
                    'terminated_own_connections': 2,
                    'get_retry_succeeded': True,
                    'search_retry_succeeded': True,
                    'get_rows': len(get_result.ids[0]),
                    'search_rows': len(search_result.ids[0]),
                },
                separators=(',', ':'),
            )
        )
    finally:
        try:
            ScopedSession.remove()
        except OperationalError:
            pass


if __name__ == '__main__':
    main()
