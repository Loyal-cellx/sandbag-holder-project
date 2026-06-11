import os
import sys

import pytest

# Make the sales-tracker package root importable when pytest is invoked from
# anywhere (e.g. `python -m pytest tests`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the database module at a fresh throwaway SQLite file and init it.

    Every DB function resolves database.DB_PATH at call time, so patching the
    module global isolates both direct database tests and the Flask routes.
    """
    path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.db_init()
    return database


@pytest.fixture
def client(db):
    import app as app_module

    app_module.app.config["TESTING"] = True
    app_module.app.secret_key = "test-secret"
    return app_module.app.test_client()
