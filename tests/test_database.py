import os
import pytest
import database


@pytest.fixture
def db_conn(tmp_path, monkeypatch):
    """Patch DATABASE_PATH to a temp file, init DB, insert two known teams."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
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
    monkeypatch.setattr(database, "DB_PATH", db_path)
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
    monkeypatch.setattr(database, "DB_PATH", db_path)
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
    monkeypatch.setattr(database, "DB_PATH", db_path)
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


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


def test_update_game_total_weather(db_path, monkeypatch):
    """update_game_total_weather patches weather fields on an existing row."""
    # Insert a game_totals row with NULL weather
    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (777001, '2026-04-15', 143, 119, 'PHI', 'LAD')
    """)
    conn.commit()
    conn.close()

    database.update_game_total_weather(777001, 72.0, 8.5, "SW")

    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 777001"
    ).fetchone()
    conn.close()
    assert row[0] == 72.0
    assert row[1] == 8.5
    assert row[2] == "SW"


def test_update_game_total_weather_none_values(db_path, monkeypatch):
    """update_game_total_weather accepts None values (no-op weather)."""
    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (777002, '2026-04-15', 143, 119, 'PHI', 'LAD')
    """)
    conn.commit()
    conn.close()

    database.update_game_total_weather(777002, None, None, None)

    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 777002"
    ).fetchone()
    conn.close()
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None


def test_get_game_totals_missing_weather_returns_null_rows(db_path, monkeypatch):
    """get_game_totals_missing_weather returns only rows where temp_f IS NULL."""
    conn = database.get_connection()
    # Row with NULL weather
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr, temp_f)
        VALUES (777003, '2026-04-14', 143, 119, 'PHI', 'LAD', NULL)
    """)
    # Row with weather populated
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr, temp_f, wind_mph, wind_dir)
        VALUES (777004, '2026-04-14', 143, 119, 'PHI', 'LAD', 68.0, 5.0, 'N')
    """)
    conn.commit()
    conn.close()

    rows = database.get_game_totals_missing_weather()
    ids = [r[0] for r in rows]
    assert 777003 in ids
    assert 777004 not in ids


def test_get_game_totals_missing_weather_returns_game_date(db_path, monkeypatch):
    """get_game_totals_missing_weather returns (mlb_game_id, game_date) tuples."""
    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (777005, '2026-04-13', 143, 119, 'PHI', 'LAD')
    """)
    conn.commit()
    conn.close()

    rows = database.get_game_totals_missing_weather()
    match = [r for r in rows if r[0] == 777005]
    assert len(match) == 1
    assert match[0][1] == '2026-04-13'


def test_pitcher_game_logs_new_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pitcher_game_logs)").fetchall()]
    assert "pitch_count" in cols
    assert "batters_faced" in cols
    assert "ground_outs" in cols
    assert "fly_outs" in cols
    assert "inherited_runners" in cols
    assert "inherited_runners_scored" in cols
    conn.close()

def test_batter_game_logs_new_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(batter_game_logs)").fetchall()]
    assert "runs" in cols
    assert "stolen_bases" in cols
    assert "hit_by_pitch" in cols
    assert "plate_appearances" in cols
    conn.close()

def test_team_game_logs_pitching_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(team_game_logs)").fetchall()]
    assert "pitching_strikeouts" in cols
    assert "pitching_walks" in cols
    assert "pitching_hits_allowed" in cols
    assert "pitching_earned_runs" in cols
    assert "pitching_home_runs_allowed" in cols
    conn.close()

def test_collect_boxscores_returns_new_pitcher_fields(monkeypatch):
    """collect_boxscores pitcher dicts include new fields."""
    import data_mlb
    import unittest.mock as mock

    fake_schedule = {"dates": [{"games": [{"gamePk": 999, "status": {"abstractGameState": "Final"}}]}]}
    fake_boxscore = {
        "teams": {
            "away": {
                "team": {"id": 1},
                "pitchers": [100],
                "players": {
                    "ID100": {
                        "person": {"fullName": "Test Pitcher"},
                        "stats": {
                            "pitching": {
                                "inningsPitched": "6.0",
                                "earnedRuns": 2,
                                "strikeOuts": 7,
                                "baseOnBalls": 1,
                                "hits": 5,
                                "homeRuns": 0,
                                "numberOfPitches": 94,
                                "battersFaced": 22,
                                "groundOuts": 8,
                                "airOuts": 5,
                                "inheritedRunners": 0,
                                "inheritedRunnersScored": 0,
                            }
                        },
                    }
                },
                "teamStats": {"batting": {}, "pitching": {}},
            },
            "home": {
                "team": {"id": 2},
                "pitchers": [],
                "players": {},
                "teamStats": {"batting": {}, "pitching": {}},
            },
        }
    }

    def fake_get(url, timeout=15):
        r = mock.MagicMock()
        r.raise_for_status = lambda: None
        if "schedule" in url:
            r.json.return_value = fake_schedule
        else:
            r.json.return_value = fake_boxscore
        return r

    monkeypatch.setattr(data_mlb.requests, "get", fake_get)
    monkeypatch.setattr(data_mlb.time, "sleep", lambda x: None)

    result = data_mlb.collect_boxscores("2026-04-17")
    pitcher = result["pitcher_logs"][0]
    assert pitcher["pitch_count"] == 94
    assert pitcher["batters_faced"] == 22
    assert pitcher["ground_outs"] == 8
    assert pitcher["fly_outs"] == 5
    assert pitcher["inherited_runners"] == 0
    assert pitcher["inherited_runners_scored"] == 0
