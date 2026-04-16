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


def test_get_current_lineups_structure(monkeypatch):
    """get_current_lineups returns expected dict structure on API success."""
    import data_mlb

    fake_response = {
        "dates": [{
            "games": [{
                "status": {"detailedState": "Pre-Game"},
                "lineups": {
                    "awayPlayers": [{"id": 101}, {"id": 102}],
                    "homePlayers": [{"id": 201}, {"id": 202}],
                },
            }]
        }]
    }

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return fake_response

    monkeypatch.setattr(data_mlb.requests, "get", lambda *a, **kw: FakeResp())

    result = data_mlb.get_current_lineups(745444)
    assert result["away_ids"] == [101, 102]
    assert result["home_ids"] == [201, 202]
    assert result["away_confirmed"] is True
    assert result["home_confirmed"] is True
    assert result["game_status"] == "Pre-Game"


def test_get_current_lineups_not_confirmed(monkeypatch):
    """get_current_lineups returns confirmed=False when lineup lists are empty."""
    import data_mlb

    fake_response = {
        "dates": [{
            "games": [{
                "status": {"detailedState": "Preview"},
                "lineups": {},
            }]
        }]
    }

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return fake_response

    monkeypatch.setattr(data_mlb.requests, "get", lambda *a, **kw: FakeResp())

    result = data_mlb.get_current_lineups(745444)
    assert result["away_ids"] == []
    assert result["home_ids"] == []
    assert result["away_confirmed"] is False
    assert result["home_confirmed"] is False


def test_run_lineup_monitor_no_alert_when_already_sent(monkeypatch):
    """No alert fired when lineup_alert_already_sent returns True."""
    import monitor
    import database as db

    monkeypatch.setattr(db, "get_today_picks", lambda: [
        {"game_id": 1, "status": "pending", "pick_team": "Houston Astros"}
    ])
    monkeypatch.setattr(db, "get_today_analysis_log", lambda: [
        {"mlb_game_id": 745444, "away_team": "Colorado Rockies", "home_team": "Houston Astros",
         "ml_pick_team": "Houston Astros", "game": "COL @ HOU"}
    ])

    monkeypatch.setattr(db, "lineup_alert_already_sent", lambda gid, d: True)

    import data_mlb
    api_called = []
    monkeypatch.setattr(data_mlb, "get_current_lineups", lambda gid: api_called.append(gid) or {})

    class FakeConn:
        def execute(self, q, params=None):
            class R:
                def fetchone(self):
                    return {"mlb_game_id": 745444, "away_team_id": 10, "home_team_id": 20}
            return R()
        def close(self): pass

    monkeypatch.setattr(db, "get_connection", lambda: FakeConn())

    monitor.run_lineup_monitor()
    assert api_called == []  # get_current_lineups should NOT be called


def test_run_lineup_monitor_skips_game_in_progress(monkeypatch):
    """No alert fired when game is Live."""
    import monitor, database as db, data_mlb

    monkeypatch.setattr(db, "get_today_picks", lambda: [
        {"game_id": 1, "status": "pending", "pick_team": "Houston Astros"}
    ])
    monkeypatch.setattr(db, "get_today_analysis_log", lambda: [
        {"mlb_game_id": 745444, "away_team": "Colorado Rockies", "home_team": "Houston Astros",
         "ml_pick_team": "Houston Astros", "game": "COL @ HOU"}
    ])
    monkeypatch.setattr(db, "lineup_alert_already_sent", lambda gid, d: False)

    class FakeConn:
        def execute(self, q, params=None):
            class R:
                def fetchone(self):
                    if "games" in q:
                        return {"mlb_game_id": 745444, "away_team_id": 10, "home_team_id": 20}
                    return {"mlb_id": 117}
            return R()
        def close(self): pass

    monkeypatch.setattr(db, "get_connection", lambda: FakeConn())

    monkeypatch.setattr(data_mlb, "get_current_lineups", lambda gid: {
        "away_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9],
        "home_ids": [11, 12, 13, 14, 15, 16, 17, 18, 19],
        "away_confirmed": True,
        "home_confirmed": True,
        "game_status": "Live",
    })

    alert_sent = []
    monkeypatch.setattr(monitor, "send_lineup_alert", lambda *a, **kw: alert_sent.append(True))

    monitor.run_lineup_monitor()
    assert alert_sent == []


def test_run_lineup_monitor_skips_lineups_not_posted(monkeypatch):
    """No alert when lineups not yet confirmed."""
    import monitor, database as db, data_mlb

    monkeypatch.setattr(db, "get_today_picks", lambda: [
        {"game_id": 1, "status": "pending", "pick_team": "Houston Astros"}
    ])
    monkeypatch.setattr(db, "get_today_analysis_log", lambda: [
        {"mlb_game_id": 745444, "away_team": "Colorado Rockies", "home_team": "Houston Astros",
         "ml_pick_team": "Houston Astros", "game": "COL @ HOU"}
    ])
    monkeypatch.setattr(db, "lineup_alert_already_sent", lambda gid, d: False)

    class FakeConn:
        def execute(self, q, params=None):
            class R:
                def fetchone(self):
                    if "games" in q:
                        return {"mlb_game_id": 745444, "away_team_id": 10, "home_team_id": 20}
                    return {"mlb_id": 117}
            return R()
        def close(self): pass

    monkeypatch.setattr(db, "get_connection", lambda: FakeConn())

    monkeypatch.setattr(data_mlb, "get_current_lineups", lambda gid: {
        "away_ids": [], "home_ids": [],
        "away_confirmed": False, "home_confirmed": False, "game_status": "Preview",
    })

    alert_sent = []
    monkeypatch.setattr(monitor, "send_lineup_alert", lambda *a, **kw: alert_sent.append(True))

    monitor.run_lineup_monitor()
    assert alert_sent == []


def _make_monitor_mocks(monkeypatch, pct_drop_actual, num_players_with_ops=9):
    """Helper: wire up full monitor stack with controllable OPS values."""
    import monitor, database as db, data_mlb

    monkeypatch.setattr(db, "get_today_picks", lambda: [
        {"game_id": 1, "status": "pending", "pick_team": "Houston Astros"}
    ])
    monkeypatch.setattr(db, "get_today_analysis_log", lambda: [
        {"mlb_game_id": 745444, "away_team": "Colorado Rockies", "home_team": "Houston Astros",
         "ml_pick_team": "Houston Astros", "ml_confidence": 7, "game": "COL @ HOU"}
    ])
    monkeypatch.setattr(db, "lineup_alert_already_sent", lambda gid, d: False)
    monkeypatch.setattr(monitor, "save_lineup_alert", lambda *a, **kw: None)

    # games table row
    game_row_data = {"mlb_game_id": 745444, "away_team_id": 10, "home_team_id": 20}
    # teams table row
    team_row_data = {"mlb_id": 117}  # HOU mlb_id

    class FakeConn:
        def execute(self, q, params=None):
            class FakeResult:
                def fetchone(self):
                    if "games" in q:
                        return game_row_data
                    if "teams" in q:
                        return team_row_data
                    return None
            return FakeResult()
        def close(self): pass

    monkeypatch.setattr(db, "get_connection", lambda: FakeConn())

    monkeypatch.setattr(data_mlb, "get_current_lineups", lambda gid: {
        "away_ids": list(range(1, 10)),
        "home_ids": list(range(101, 110)),
        "away_confirmed": True, "home_confirmed": True, "game_status": "Pre-Game",
    })

    # Expected OPS = 0.750; actual OPS controlled via pct_drop_actual
    expected_ops = 0.750
    actual_ops = expected_ops * (1 - pct_drop_actual)

    # fetch_lineup_batting returns num_players_with_ops players with OPS data
    player_stats = [{"player_id": 100 + i, "ops": actual_ops, "obp": 0.33, "slg": 0.40}
                    for i in range(num_players_with_ops)]
    monkeypatch.setattr(monitor, "fetch_lineup_batting", lambda ids: player_stats)

    monkeypatch.setattr(monitor, "get_batter_rolling_ops", lambda pid: None)  # use season OPS only

    monkeypatch.setattr(monitor, "fetch_team_batting", lambda tid: {"ops": expected_ops})

    return monitor


def test_run_lineup_monitor_fires_alert_on_ops_drop(monkeypatch):
    """Alert sent when pick team lineup OPS is >10% below expected."""
    import monitor, database as db

    alert_sent = []
    mon = _make_monitor_mocks(monkeypatch, pct_drop_actual=0.15)  # 15% drop
    monkeypatch.setattr(mon, "send_lineup_alert", lambda *a, **kw: alert_sent.append(True) or True)

    mon.run_lineup_monitor()
    assert len(alert_sent) == 1


def test_run_lineup_monitor_no_alert_on_small_drop(monkeypatch):
    """No alert when OPS drop is below 10% threshold."""
    import monitor

    alert_sent = []
    mon = _make_monitor_mocks(monkeypatch, pct_drop_actual=0.05)  # 5% drop
    monkeypatch.setattr(mon, "send_lineup_alert", lambda *a, **kw: alert_sent.append(True) or True)

    mon.run_lineup_monitor()
    assert alert_sent == []


def test_run_lineup_monitor_no_alert_on_ops_improvement(monkeypatch):
    """No alert when confirmed lineup OPS is better than expected."""
    import monitor

    alert_sent = []
    mon = _make_monitor_mocks(monkeypatch, pct_drop_actual=-0.05)  # 5% improvement
    monkeypatch.setattr(mon, "send_lineup_alert", lambda *a, **kw: alert_sent.append(True) or True)

    mon.run_lineup_monitor()
    assert alert_sent == []


def test_run_lineup_monitor_skips_when_fewer_than_5_players_have_data(monkeypatch):
    """No alert when fewer than 5 starters have OPS data."""
    import monitor

    alert_sent = []
    mon = _make_monitor_mocks(monkeypatch, pct_drop_actual=0.20, num_players_with_ops=3)
    monkeypatch.setattr(mon, "send_lineup_alert", lambda *a, **kw: alert_sent.append(True) or True)

    mon.run_lineup_monitor()
    assert alert_sent == []
