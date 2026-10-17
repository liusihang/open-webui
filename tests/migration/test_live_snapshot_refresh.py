import importlib.util
import sys
from pathlib import Path

import pytest

root = Path(__file__).parents[2] / "tools/migration/v0120"
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("live_snapshot_refresh", root / "live_snapshot_refresh.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def config(mode="live"):
    return {"mode": mode, "source_schema_version": module.OLD_SCHEMA,
            "minimum_free_bytes": 15 * 2**30,
            "source": {"host": "source-only", "database": "webui_db", "system_identifier": module.LIVE_SYSTEM_ID},
            "target": {"host": "owned-target", "database": "live_snapshot", "system_identifier": module.NEW_SYSTEM_ID}}


def test_live_source_and_owned_staging_are_the_only_allowed_live_surfaces():
    module.validate_config(config())


@pytest.mark.parametrize("side,field,value", [
    ("target", "database", "webui_live"),
    ("target", "database", "webui_db"),
    ("target", "system_identifier", module.LIVE_SYSTEM_ID),
    ("source", "database", "live_snapshot"),
    ("source", "system_identifier", module.NEW_SYSTEM_ID),
    ("source", "host", "owned-target"),
])
def test_configuration_cannot_make_production_the_write_destination(side, field, value):
    c = config()
    c[side][field] = value
    with pytest.raises(AssertionError):
        module.validate_config(c)


def test_offline_proof_is_limited_to_two_exact_owned_databases():
    c = config("offline-proof")
    c["source"].update(database=module.PROOF_SOURCE, system_identifier=module.NEW_SYSTEM_ID)
    c["target"]["database"] = module.PROOF_TARGET
    module.validate_config(c)
    c["target"]["database"] = "webui_live"
    with pytest.raises(AssertionError):
        module.validate_config(c)


def test_source_connection_sets_readonly_before_the_first_sql(monkeypatch):
    calls = []
    class Connection:
        def set_session(self, **kwargs):
            calls.append(kwargs)
    class Driver:
        def connect(self, **kwargs):
            calls.append(kwargs)
            return Connection()
    monkeypatch.setitem(sys.modules, "psycopg2", Driver())
    module.connect_source(dict(host="source", database="webui_db", username="example", password="not-real"))
    assert "default_transaction_read_only=on" in calls[0]["options"]
    assert calls[1] == {"readonly": True, "isolation_level": "REPEATABLE READ"}
