from types import SimpleNamespace

from open_webui.retrieval.vector.dbs import pgvector
from sqlalchemy.exc import OperationalError


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


class FakeGetQuery:
    def __init__(self, session):
        self.session = session

    def filter(self, *args):
        return self

    def limit(self, limit):
        return self

    def all(self):
        self.session.query_count += 1
        outcome = self.session.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def first(self):
        self.session.query_count += 1
        outcome = self.session.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeGetSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.query_count = 0
        self.rollback_count = 0
        self.remove_count = 0

    def query(self, model):
        return FakeGetQuery(self)

    def rollback(self):
        self.rollback_count += 1

    def remove(self):
        self.remove_count += 1


class FakeInvalidatedSearchSession(FakeGetSession):
    def __init__(self, outcomes):
        super().__init__(outcomes)
        self.execute_count = 0

    def execute(self, statement):
        self.execute_count += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeExecuteResult(outcome)


def _connection_invalidated_error():
    return OperationalError(
        "SELECT document_chunk",
        {},
        Exception("server closed the connection unexpectedly"),
        connection_invalidated=True,
    )


def test_pgvector_get_retries_once_after_invalidated_connection():
    rows = [SimpleNamespace(id="vector-1", text="text 1", vmetadata={"source": "source.pdf"})]
    client = pgvector.PgvectorClient.__new__(pgvector.PgvectorClient)
    client.session = FakeGetSession([_connection_invalidated_error(), rows])

    result = client.get(collection_name="collection-1")

    assert result.ids == [["vector-1"]]
    assert client.session.query_count == 2
    assert client.session.rollback_count == 2
    assert client.session.remove_count == 1


def test_pgvector_get_does_not_retry_other_database_errors():
    error = OperationalError(
        "SELECT document_chunk",
        {},
        Exception("permission denied"),
        connection_invalidated=False,
    )
    client = pgvector.PgvectorClient.__new__(pgvector.PgvectorClient)
    client.session = FakeGetSession([error])

    assert client.get(collection_name='collection-1') is None
    assert client.session.query_count == 1
    assert client.session.rollback_count == 1
    assert client.session.remove_count == 0


def test_pgvector_get_stops_after_one_invalidated_connection_retry():
    client = pgvector.PgvectorClient.__new__(pgvector.PgvectorClient)
    client.session = FakeGetSession(
        [_connection_invalidated_error(), _connection_invalidated_error()]
    )

    assert client.get(collection_name='collection-1') is None
    assert client.session.query_count == 2
    assert client.session.rollback_count == 2
    assert client.session.remove_count == 1


def test_pgvector_search_retries_once_after_invalidated_connection(monkeypatch):
    client = pgvector.PgvectorClient.__new__(pgvector.PgvectorClient)
    client.session = FakeInvalidatedSearchSession(
        [_connection_invalidated_error(), [_row(1)]]
    )
    monkeypatch.setattr(client, 'adjust_vector_length', lambda vector: vector)

    result = client.search(
        collection_name='collection-1',
        vectors=[[0.1, 0.2]],
        limit=1,
    )

    assert result.ids == [['vector-1']]
    assert client.session.execute_count == 2
    assert client.session.rollback_count == 2
    assert client.session.remove_count == 1


def test_pgvector_query_retries_once_after_invalidated_connection():
    rows = [SimpleNamespace(id='vector-1', text='text 1', vmetadata={'kind': 'safe'})]
    client = pgvector.PgvectorClient.__new__(pgvector.PgvectorClient)
    client.session = FakeGetSession([_connection_invalidated_error(), rows])

    result = client.query(
        collection_name='collection-1',
        filter={'kind': 'safe'},
        limit=1,
    )

    assert result.ids == [['vector-1']]
    assert client.session.query_count == 2
    assert client.session.rollback_count == 2
    assert client.session.remove_count == 1


def test_pgvector_has_collection_retries_once_after_invalidated_connection():
    client = pgvector.PgvectorClient.__new__(pgvector.PgvectorClient)
    client.session = FakeGetSession(
        [_connection_invalidated_error(), SimpleNamespace(id='vector-1')]
    )

    assert client.has_collection('collection-1') is True
    assert client.session.query_count == 2
    assert client.session.rollback_count == 2
    assert client.session.remove_count == 1
