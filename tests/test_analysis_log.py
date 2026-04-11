import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import database as db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DATABASE_PATH", db_path)
    db.init_db()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)


def test_analysis_log_table_exists(tmp_db):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_log'"
    ).fetchone()
    conn.close()
    assert row is not None, "analysis_log table should exist after init_db()"
