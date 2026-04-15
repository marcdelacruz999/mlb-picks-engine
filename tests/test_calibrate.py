import sqlite3
import tempfile
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def _make_db(picks):
    """Create a temp SQLite DB with the picks table populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE picks (
            id INTEGER PRIMARY KEY,
            pick_type TEXT,
            pick_team TEXT,
            confidence INTEGER,
            status TEXT,
            edge_score REAL,
            edge_pitching TEXT,
            edge_offense TEXT,
            edge_bullpen TEXT,
            edge_advanced TEXT,
            edge_market TEXT,
            edge_weather TEXT,
            notes TEXT,
            ml_odds INTEGER,
            ou_odds INTEGER,
            ev_score REAL,
            discord_sent INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    for p in picks:
        conn.execute("""
            INSERT INTO picks (pick_type, pick_team, confidence, status,
                edge_score, edge_pitching, edge_offense, edge_bullpen,
                edge_advanced, edge_market, edge_weather, notes,
                ml_odds, ou_odds, ev_score, discord_sent, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p.get("pick_type","moneyline"), p.get("pick_team","Team A"),
            p.get("confidence",7), p.get("status","won"),
            p.get("edge_score",0.20),
            p.get("edge_pitching",""), p.get("edge_offense",""),
            p.get("edge_bullpen",""), p.get("edge_advanced",""),
            p.get("edge_market",""), p.get("edge_weather",""),
            p.get("notes",""), p.get("ml_odds",-110), p.get("ou_odds",None),
            p.get("ev_score",0.05), p.get("discord_sent",1),
            p.get("created_at","2026-04-14 08:00:00"),
        ))
    conn.commit()
    return conn


def test_fetch_graded_picks_returns_sent_graded_only():
    from calibrate import fetch_graded_picks
    conn = _make_db([
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "pending", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "won", "discord_sent": 0, "created_at": "2026-04-14 08:00:00"},
        {"status": "lost", "discord_sent": 1, "created_at": "2026-04-08 08:00:00"},
    ])
    rows = fetch_graded_picks(conn, days=7, _now="2026-04-14 23:59:59")
    assert len(rows) == 2
    statuses = {r["status"] for r in rows}
    assert statuses == {"won", "lost"}


def test_fetch_graded_picks_window():
    from calibrate import fetch_graded_picks
    conn = _make_db([
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-06 08:00:00"},
    ])
    # 7-day window from Apr 14 → only Apr 14 pick qualifies
    rows = fetch_graded_picks(conn, days=7, _now="2026-04-14 23:59:59")
    assert len(rows) == 1


def test_parse_signals_sp_home_advantage():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Home Team",
        "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
        "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
        "edge_market": "Market edge: 1.5%", "edge_weather": "Clear skies, wind 8 mph out",
        "notes": "",
    }
    sigs = parse_signals(pick)
    assert sigs["sp_home_advantage"] is True
    assert sigs["sp_away_advantage"] is False


def test_parse_signals_bullpen_era_bad():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Home Team",
        "edge_pitching": "", "edge_offense": "",
        "edge_bullpen": "Home top pen (7d): 3 appearances, 5.80 ERA. Away top pen (7d): 2 appearances, 3.20 ERA.",
        "edge_advanced": "", "edge_market": "Market edge: 3.0%",
        "edge_weather": "Wind 10 mph out to CF", "notes": "",
    }
    sigs = parse_signals(pick)
    assert sigs["bullpen_home_era_bad"] is True
    assert sigs["bullpen_away_era_bad"] is False


def test_parse_signals_rust_weak_pen_combo():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Home Team",
        "edge_pitching": "Home SP extended layoff (10 days rest). Away SP recent start.",
        "edge_offense": "",
        "edge_bullpen": "Home top pen (7d): 2 appearances, 6.10 ERA.",
        "edge_advanced": "", "edge_market": "Market edge: 2.5%",
        "edge_weather": "", "notes": "",
        "_pick_side": "home",
    }
    sigs = parse_signals(pick)
    assert sigs["sp_home_layoff"] is True
    assert sigs["bullpen_home_era_bad"] is True
    assert sigs["rust_weak_pen_home"] is True
    assert sigs["rust_weak_pen_away"] is False


def test_parse_signals_market_buckets():
    from calibrate import parse_signals
    def _pick(market_text):
        return {
            "pick_type": "moneyline", "pick_team": "Team",
            "edge_pitching": "", "edge_offense": "", "edge_bullpen": "",
            "edge_advanced": "", "edge_market": market_text,
            "edge_weather": "", "notes": "", "_pick_side": "home",
        }
    assert parse_signals(_pick("Home has +1.5% edge vs market (implied 48.5%, model 50.0%)"))["market_edge_low"] is True
    assert parse_signals(_pick("Home has +3.0% edge vs market (implied 47.0%, model 50.0%)"))["market_edge_mid"] is True
    assert parse_signals(_pick("Away has +5.2% edge vs market (implied 44.8%, model 50.0%)"))["market_edge_high"] is True


def test_parse_signals_wind_strong():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Team",
        "edge_pitching": "", "edge_offense": "", "edge_bullpen": "",
        "edge_advanced": "", "edge_market": "",
        "edge_weather": "Wind 15 mph out to left field. Clear skies.",
        "notes": "", "_pick_side": "home",
    }
    sigs = parse_signals(pick)
    assert sigs["wind_strong"] is True


def test_analyze_signals_win_rate():
    from calibrate import analyze_signals
    # 3 picks: 2 won, 1 lost. All have sp_home_advantage=True.
    picks = [
        {"pick_type": "moneyline", "status": "won",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
        {"pick_type": "moneyline", "status": "won",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
        {"pick_type": "moneyline", "status": "lost",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
    ]
    result = analyze_signals(picks)
    assert result["baseline_win_rate"] == round(2/3, 4)
    sp_row = result["signal_table"]["sp_home_advantage"]
    assert sp_row["n"] == 3
    assert sp_row["wins"] == 2
    assert sp_row["losses"] == 1
    assert sp_row["win_rate"] == round(2/3, 4)


def test_analyze_signals_min_n_filter():
    from calibrate import analyze_signals
    # Only 2 picks with a signal — should not appear in table (min N=3)
    picks = [
        {"pick_type": "moneyline", "status": "won",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
        {"pick_type": "moneyline", "status": "lost",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
    ]
    result = analyze_signals(picks)
    assert "sp_home_advantage" not in result["signal_table"]


def test_suggest_weights_nudge_up():
    from calibrate import suggest_weights
    current = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
               "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    # offense signal winning at 90% vs 60% baseline — should suggest +0.02 to offense
    signal_table = {
        "offense_home_edge": {"n": 6, "wins": 9, "losses": 1, "win_rate": 0.90, "delta": 0.30},
    }
    baseline = 0.60
    suggestions = suggest_weights(current, signal_table, baseline, n_picks=12)
    assert suggestions["offense"] == round(0.23 + 0.02, 4)


def test_suggest_weights_no_change_when_low_n():
    from calibrate import suggest_weights
    current = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
               "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    # offense signal only 4 picks — below N>=5 threshold for suggestions
    signal_table = {
        "offense_home_edge": {"n": 4, "wins": 4, "losses": 0, "win_rate": 1.0, "delta": 0.40},
    }
    baseline = 0.60
    suggestions = suggest_weights(current, signal_table, baseline, n_picks=10)
    assert suggestions == current


def test_suggest_weights_normalizes_to_1():
    from calibrate import suggest_weights
    current = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
               "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    signal_table = {
        "offense_home_edge": {"n": 6, "wins": 9, "losses": 1, "win_rate": 0.90, "delta": 0.30},
    }
    suggestions = suggest_weights(current, signal_table, 0.60, n_picks=12)
    total = round(sum(suggestions.values()), 4)
    assert total == 1.0, f"Weights sum to {total}, expected 1.0"
