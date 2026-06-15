from types import SimpleNamespace

from open_webui.retrieval.vector.dbs import pgvector


class FakeExecuteResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, first_rows, exact_rows):
        self._row_batches = [first_rows, exact_rows]
        self.executed_statements = []
        self.rollback_count = 0

    def execute(self, statement):
        statement_text = str(statement)
        self.executed_statements.append(statement_text)
        if "enable_indexscan" in statement_text:
            return FakeExecuteResult()
        return FakeExecuteResult(self._row_batches.pop(0))

    def rollback(self):
        self.rollback_count += 1


def _row(index, source="source.pdf"):
    return SimpleNamespace(
        qid=0,
        id=f"vector-{index}",
        text=f"text {index}",
        vmetadata={"source": source, "chunk_uid": f"chunk-{index}"},
        distance=0.1 + index,
    )


def test_pgvector_search_retries_exact_scan_when_hnsw_underfills_requested_limit(monkeypatch):
    first_rows = [_row(1), _row(2)]
    exact_rows = [_row(index) for index in range(5)]
    client = pgvector.PgvectorClient.__new__(pgvector.PgvectorClient)
    client.session = FakeSession(first_rows, exact_rows)

    monkeypatch.setattr(client, "adjust_vector_length", lambda vector: vector)

    result = client.search(
        collection_name="collection-1",
        vectors=[[0.1, 0.2]],
        limit=5,
    )

    assert len(result.ids[0]) == 5
    assert any("enable_indexscan = off" in statement for statement in client.session.executed_statements)
    assert client.session.rollback_count == 1
