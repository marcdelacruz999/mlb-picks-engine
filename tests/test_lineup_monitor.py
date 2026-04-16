# tests/test_lineup_monitor.py
import pytest


def test_lineup_config_constants():
    from config import LINEUP_OPS_DROP_THRESHOLD, LINEUP_MIN_PLAYERS_WITH_DATA
    assert LINEUP_OPS_DROP_THRESHOLD == 0.10
    assert LINEUP_MIN_PLAYERS_WITH_DATA == 5


def test_lineup_alert_not_sent_initially():
    import database as db
    db.init_db()
    # Use a fake game ID unlikely to exist
    assert db.lineup_alert_already_sent(99999999, "2026-01-01") is False


def test_save_and_detect_lineup_alert():
    import database as db
    db.init_db()
    mlb_game_id = 88888888
    game_date = "2026-01-01"
    db.save_lineup_alert(mlb_game_id, game_date, ops_actual=0.650, ops_expected=0.750, pct_drop=0.133)
    assert db.lineup_alert_already_sent(mlb_game_id, game_date) is True


def test_save_lineup_alert_dedup():
    import database as db
    db.init_db()
    mlb_game_id = 77777777
    game_date = "2026-01-01"
    # Saving twice should not raise — INSERT OR IGNORE
    db.save_lineup_alert(mlb_game_id, game_date, ops_actual=0.650, ops_expected=0.750, pct_drop=0.133)
    db.save_lineup_alert(mlb_game_id, game_date, ops_actual=0.640, ops_expected=0.750, pct_drop=0.147)
    assert db.lineup_alert_already_sent(mlb_game_id, game_date) is True
