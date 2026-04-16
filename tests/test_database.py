import os
import pytest
import database


@pytest.fixture
def db_conn(tmp_path, monkeypatch):
    """Patch DATABASE_PATH to a temp file, init DB, insert two known teams."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.init_db()
    conn = database.get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (143, 'Philadelphia Phillies', 'PHI')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (119, 'Los Angeles Dodgers', 'LAD')"
    )
    conn.commit()
    conn.close()
    yield db_path


def test_get_team_abbr_known_id(db_conn):
    assert database.get_team_abbr_by_mlb_id(143) == "PHI"


def test_get_team_abbr_another_known_id(db_conn):
    assert database.get_team_abbr_by_mlb_id(119) == "LAD"


def test_get_team_abbr_unknown_id_returns_empty(db_conn):
    assert database.get_team_abbr_by_mlb_id(9999) == ""


def test_get_team_abbr_none_id_returns_empty(db_conn):
    assert database.get_team_abbr_by_mlb_id(None) == ""
