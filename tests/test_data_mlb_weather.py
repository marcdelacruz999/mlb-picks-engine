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
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
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
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
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
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
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
