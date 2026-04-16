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


def test_backfill_game_totals_abbr_populates_empty_rows(monkeypatch, tmp_path):
    """Rows with empty abbr strings get populated from teams table."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.init_db()

    conn = database.get_connection()
    conn.execute("INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (143, 'Philadelphia Phillies', 'PHI')")
    conn.execute("INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (119, 'Los Angeles Dodgers', 'LAD')")
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (999001, '2026-04-15', 143, 119, '', '')
    """)
    conn.commit()
    conn.close()

    count = database.backfill_game_totals_abbr()

    conn = database.get_connection()
    row = conn.execute(
        "SELECT home_team_abbr, away_team_abbr FROM game_totals WHERE mlb_game_id = 999001"
    ).fetchone()
    conn.close()
    assert row[0] == "PHI"
    assert row[1] == "LAD"
    assert count >= 1


def test_backfill_game_totals_abbr_skips_populated_rows(monkeypatch, tmp_path):
    """Rows already having abbreviations are not touched."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.init_db()

    conn = database.get_connection()
    conn.execute("INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (143, 'Philadelphia Phillies', 'PHI')")
    conn.execute("INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (119, 'Los Angeles Dodgers', 'LAD')")
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (999002, '2026-04-15', 143, 119, 'PHI', 'LAD')
    """)
    conn.commit()
    conn.close()

    database.backfill_game_totals_abbr()

    conn = database.get_connection()
    row = conn.execute(
        "SELECT home_team_abbr, away_team_abbr FROM game_totals WHERE mlb_game_id = 999002"
    ).fetchone()
    conn.close()
    assert row[0] == "PHI"
    assert row[1] == "LAD"


def test_backfill_game_totals_abbr_handles_null_abbr(monkeypatch, tmp_path):
    """Rows with NULL abbr also get populated."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.init_db()

    conn = database.get_connection()
    conn.execute("INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (143, 'Philadelphia Phillies', 'PHI')")
    conn.execute("INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (119, 'Los Angeles Dodgers', 'LAD')")
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (999003, '2026-04-15', 143, 119, NULL, NULL)
    """)
    conn.commit()
    conn.close()

    database.backfill_game_totals_abbr()

    conn = database.get_connection()
    row = conn.execute(
        "SELECT home_team_abbr, away_team_abbr FROM game_totals WHERE mlb_game_id = 999003"
    ).fetchone()
    conn.close()
    assert row[0] == "PHI"
    assert row[1] == "LAD"
