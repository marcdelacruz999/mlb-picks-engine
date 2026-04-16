"""Tests for weather storage in collect_game_totals()."""
import pytest
from unittest.mock import patch, MagicMock


FAKE_SCHEDULE_RESPONSE = {
    "dates": [{
        "games": [{
            "gamePk": 123456,
            "gameDate": "2026-04-15T18:10:00Z",
            "venue": {"id": 2392},
            "teams": {
                "home": {"team": {"id": 143}},
                "away": {"team": {"id": 119}},
            },
            "linescore": {
                "teams": {
                    "home": {"runs": 5},
                    "away": {"runs": 3},
                }
            },
            "status": {"abstractGameState": "Final"},
        }]
    }]
}


def test_collect_game_totals_stores_weather(tmp_path, monkeypatch):
    """collect_game_totals() stores weather when fetch_venue_weather succeeds."""
    import database
    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "test.db"))
    database.init_db()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = FAKE_SCHEDULE_RESPONSE

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather", return_value={"temp_f": 72.0, "wind_mph": 10.0, "wind_dir": "SW"}) as mock_weather:
        import data_mlb
        records = data_mlb.collect_game_totals("2026-04-15")
        mock_weather.assert_called_once()

    database.store_game_totals(records)

    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 123456"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 72.0
    assert row[1] == 10.0
    assert row[2] == "SW"


def test_collect_game_totals_weather_fetch_failure_stores_none(tmp_path, monkeypatch):
    """collect_game_totals() stores None for weather when fetch_venue_weather raises."""
    import database
    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "test.db"))
    database.init_db()

    # Use a unique gamePk to avoid INSERT OR IGNORE collision with other tests
    error_response = {
        "dates": [{
            "games": [{
                "gamePk": 123458,
                "gameDate": "2026-04-15T18:10:00Z",
                "venue": {"id": 2392},
                "teams": {
                    "home": {"team": {"id": 143}},
                    "away": {"team": {"id": 119}},
                },
                "linescore": {
                    "teams": {
                        "home": {"runs": 5},
                        "away": {"runs": 3},
                    }
                },
                "status": {"abstractGameState": "Final"},
            }]
        }]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = error_response

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather", side_effect=Exception("API down")):
        import data_mlb
        records = data_mlb.collect_game_totals("2026-04-15")  # must not crash

    database.store_game_totals(records)

    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 123458"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None


def test_collect_game_totals_missing_venue_skips_weather(tmp_path, monkeypatch):
    """collect_game_totals() skips weather fetch when venue_id is absent."""
    import database
    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "test.db"))
    database.init_db()

    # Schedule response with no venue field
    no_venue_response = {
        "dates": [{
            "games": [{
                "gamePk": 123457,
                "gameDate": "2026-04-15T18:10:00Z",
                # no "venue" key
                "teams": {
                    "home": {"team": {"id": 143}},
                    "away": {"team": {"id": 119}},
                },
                "linescore": {
                    "teams": {
                        "home": {"runs": 5},
                        "away": {"runs": 3},
                    }
                },
                "status": {"abstractGameState": "Final"},
            }]
        }]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = no_venue_response

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather") as mock_weather:
        import data_mlb
        records = data_mlb.collect_game_totals("2026-04-15")
        mock_weather.assert_not_called()

    database.store_game_totals(records)

    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 123457"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] is None


def test_backfill_game_totals_weather_updates_null_rows(tmp_path, monkeypatch):
    """backfill_game_totals_weather() updates rows where temp_f IS NULL."""
    import database
    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "test.db"))
    database.init_db()

    # Insert a game_totals row with NULL weather
    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (888001, '2026-04-10', 143, 119, 'PHI', 'LAD')
    """)
    conn.commit()
    conn.close()

    mock_sched_resp = MagicMock()
    mock_sched_resp.status_code = 200
    mock_sched_resp.json.return_value = {
        "dates": [{"games": [{"venue": {"id": 2392}, "gameDate": "2026-04-10T18:05:00Z"}]}]
    }

    with patch("data_mlb.requests.get", return_value=mock_sched_resp), \
         patch("data_mlb.fetch_venue_weather_archive", return_value={"temp_f": 65.0, "wind_mph": 7.0, "wind_dir": "NE"}):
        import data_mlb
        count = data_mlb.backfill_game_totals_weather()

    assert count >= 1
    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 888001"
    ).fetchone()
    conn.close()
    assert row[0] == 65.0
    assert row[1] == 7.0
    assert row[2] == "NE"


def test_backfill_game_totals_weather_skips_populated_rows(tmp_path, monkeypatch):
    """backfill_game_totals_weather() skips rows that already have temp_f populated."""
    import database
    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "test.db"))
    database.init_db()

    # Insert a row WITH weather already populated
    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr,
             temp_f, wind_mph, wind_dir)
        VALUES (888002, '2026-04-10', 143, 119, 'PHI', 'LAD', 70.0, 5.0, 'S')
    """)
    conn.commit()
    conn.close()

    with patch("data_mlb.requests.get") as mock_get, \
         patch("data_mlb.fetch_venue_weather_archive") as mock_weather:
        import data_mlb
        count = data_mlb.backfill_game_totals_weather()

    # Row 888002 already has weather — should not be touched
    mock_get.assert_not_called()
    mock_weather.assert_not_called()
    assert count == 0


def test_backfill_game_totals_weather_handles_api_failure(tmp_path, monkeypatch):
    """backfill_game_totals_weather() skips games where MLB API fails, returns 0."""
    import database
    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "test.db"))
    database.init_db()

    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (888003, '2026-04-10', 143, 119, 'PHI', 'LAD')
    """)
    conn.commit()
    conn.close()

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {}

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather_archive") as mock_weather:
        import data_mlb
        count = data_mlb.backfill_game_totals_weather()

    mock_weather.assert_not_called()
    assert count == 0
