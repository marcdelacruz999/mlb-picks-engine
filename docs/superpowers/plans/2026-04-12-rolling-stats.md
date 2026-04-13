# Rolling Stats & Post-Game Data Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every MLB game, collect and store pitcher and team boxscore stats so the 7-agent engine can use rolling 14/21-day windows instead of season-only cumulative stats, improving pick quality as the season progresses.

**Architecture:** A new `collect_boxscores(game_date)` function fetches MLB boxscore API data for all games on a given date and stores results in two new DB tables (`pitcher_game_logs`, `team_game_logs`). Four rolling query functions compute windowed stats from those tables. `collect_game_data()` in `data_mlb.py` attaches rolling stats to each game dict. The three relevant agents (pitching, offense, bullpen) blend rolling stats with season stats using a game-count-weighted blend — graceful degradation to season-only in April when few games are stored.

**Tech Stack:** Python 3.9, SQLite (sqlite3 stdlib), MLB Stats API (free, no key), existing `_parse_ip()` helper in `data_mlb.py`

---

## Environment & Patterns

- **Project root:** `/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/`
- **Python:** 3.9 — no `float | None` union syntax; use `Optional[float]` or string annotations
- **DB:** SQLite at `mlb_picks.db`. All migrations use `except sqlite3.OperationalError: pass` (not bare `except Exception`)
- **Config pattern:** all tunable thresholds live in `config.py`; never hardcode in analysis.py
- **Mock patch:** functions imported at module level in `analysis.py` must be mocked as `analysis.funcname`, not `data_mlb.funcname`
- **Test runner:** `python3 -m pytest tests/ -v --tb=short`
- **Pre-existing failing tests** in `tests/test_analysis_log.py` — do not fix, ignore failures from that file
- **MLB Stats API base:** `https://statsapi.mlb.com/api/v1`
- **MLB innings-pitched strings:** "6.2" = 6 full innings + 2 outs = 6.667. Use existing `_parse_ip()` in `data_mlb.py`
- **Boxscore hydrate endpoint:** `{MLB_BASE}/schedule?sportId=1&date=YYYY-MM-DD&gameType=R&hydrate=boxscore`
- Boxscore `teams.away.pitchers` is a list of player IDs in appearance order; `[0]` = starter
- Boxscore `teams.away.players` is dict keyed `"ID{player_id}"` e.g. `"ID662253"`
- Boxscore `teams.away.teamStats.batting` has: `runs`, `hits`, `homeRuns`, `strikeOuts`, `baseOnBalls`, `leftOnBase`, `atBats`
- `teamStats` is at the game level, not at the `players` level
- **`_parse_ip` import:** already defined in `data_mlb.py`, reuse it

---

## File Structure

| File | Change |
|------|--------|
| `database.py` | Add 2 new tables + 4 migration columns + 4 rolling query functions + `store_boxscores()` |
| `data_mlb.py` | Add `collect_boxscores()` + attach rolling stats in `collect_game_data()` |
| `analysis.py` | Add `_blend()` helper + blend rolling stats in `score_pitching()`, `score_offense()`, `score_bullpen()` |
| `engine.py` | Call `collect_and_store_boxscores()` in `run_results()` + add `--collect` CLI flag |
| `tests/test_rolling_stats.py` | New test file: 9 tests covering DB storage, rolling queries, blend logic |

---

## Task 1: DB Schema — Two New Tables + Rolling Query Functions

**Files:**
- Modify: `database.py`
- Test: `tests/test_rolling_stats.py`

- [ ] **Step 1: Write the failing tests for DB schema and rolling queries**

Create `tests/test_rolling_stats.py`:

```python
import pytest
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
from datetime import date, timedelta


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()


def _insert_pitcher_log(pitcher_id, team_id, game_date, is_starter, ip, er, k, bb, h, hr=0):
    conn = db.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (1000 + pitcher_id, game_date, pitcher_id, f"Pitcher {pitcher_id}",
          team_id, is_starter, ip, er, k, bb, h, hr))
    conn.commit()
    conn.close()


def _insert_team_log(team_id, game_date, runs, hits, hr, k, bb, ab):
    conn = db.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO team_game_logs
        (mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
         strikeouts, walks, at_bats, left_on_base)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (2000 + team_id, game_date, team_id, True, runs, hits, hr, k, bb, ab, 0))
    conn.commit()
    conn.close()


def test_pitcher_game_logs_table_exists():
    conn = db.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pitcher_game_logs)")]
    conn.close()
    assert "pitcher_id" in cols
    assert "innings_pitched" in cols
    assert "earned_runs" in cols
    assert "strikeouts" in cols
    assert "walks" in cols
    assert "is_starter" in cols


def test_team_game_logs_table_exists():
    conn = db.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(team_game_logs)")]
    conn.close()
    assert "team_id" in cols
    assert "runs" in cols
    assert "hits" in cols
    assert "strikeouts" in cols
    assert "at_bats" in cols


def test_get_pitcher_rolling_stats_basic():
    today = date.today()
    for i in range(4):
        _insert_pitcher_log(
            pitcher_id=100,
            team_id=1,
            game_date=(today - timedelta(days=i * 5 + 1)).isoformat(),
            is_starter=True, ip=6.0, er=2, k=7, bb=2, h=5
        )
    result = db.get_pitcher_rolling_stats(100, days=21)
    assert result is not None
    assert result["games"] == 4
    # ERA = 2 ER / 6.0 IP * 9 = 3.00
    assert abs(result["era"] - 3.0) < 0.01
    # WHIP = (5 H + 2 BB) / 6.0 IP = 1.167
    assert abs(result["whip"] - (7 / 6.0)) < 0.01


def test_get_pitcher_rolling_stats_returns_none_for_no_data():
    result = db.get_pitcher_rolling_stats(999, days=21)
    assert result is None


def test_get_pitcher_rolling_stats_filters_by_days():
    today = date.today()
    # Game 25 days ago — outside 21-day window
    _insert_pitcher_log(100, 1, (today - timedelta(days=25)).isoformat(),
                        True, 6.0, 5, 5, 4, 8)
    # Game 10 days ago — inside window
    _insert_pitcher_log(101, 1, (today - timedelta(days=10)).isoformat(),
                        True, 7.0, 1, 9, 1, 4)
    result = db.get_pitcher_rolling_stats(101, days=21)
    assert result is not None
    assert result["games"] == 1


def test_get_team_batting_rolling_basic():
    today = date.today()
    for i in range(5):
        _insert_team_log(
            team_id=10,
            game_date=(today - timedelta(days=i + 1)).isoformat(),
            runs=5, hits=9, hr=1, k=8, bb=3, ab=30
        )
    result = db.get_team_batting_rolling(10, days=14)
    assert result is not None
    assert result["games"] == 5
    assert abs(result["rpg"] - 5.0) < 0.01
    # OBP proxy = (H + BB) / (AB + BB) = (9+3)/(30+3) = 0.364
    assert abs(result["obp_proxy"] - (12 / 33)) < 0.01


def test_get_team_batting_rolling_returns_none_for_no_data():
    result = db.get_team_batting_rolling(999, days=14)
    assert result is None


def test_get_team_bullpen_rolling_basic():
    today = date.today()
    # 3 relief appearances
    for i in range(3):
        _insert_pitcher_log(
            pitcher_id=200 + i,
            team_id=20,
            game_date=(today - timedelta(days=i + 1)).isoformat(),
            is_starter=False, ip=1.0, er=0, k=1, bb=0, h=1
        )
    result = db.get_team_bullpen_rolling(20, days=14)
    assert result is not None
    assert result["games"] == 3
    # 0 ER over 3 IP → ERA 0.0
    assert result["era"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_rolling_stats.py -v --tb=short
```
Expected: all tests FAIL (tables and functions don't exist yet)

- [ ] **Step 3: Add tables, migrations, and rolling query functions to `database.py`**

In `database.py`, in `init_db()`, add the two CREATE TABLE statements after the `scratch_alerts` table (before the `CREATE INDEX` lines):

```python
    CREATE TABLE IF NOT EXISTS pitcher_game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER,
        game_date TEXT,
        pitcher_id INTEGER,
        pitcher_name TEXT,
        team_id INTEGER,
        is_starter INTEGER,
        innings_pitched REAL,
        earned_runs INTEGER,
        strikeouts INTEGER,
        walks INTEGER,
        hits INTEGER,
        home_runs INTEGER,
        created_at TEXT,
        UNIQUE(mlb_game_id, pitcher_id)
    );

    CREATE TABLE IF NOT EXISTS team_game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER,
        game_date TEXT,
        team_id INTEGER,
        is_away INTEGER,
        runs INTEGER,
        hits INTEGER,
        home_runs INTEGER,
        strikeouts INTEGER,
        walks INTEGER,
        at_bats INTEGER,
        left_on_base INTEGER,
        created_at TEXT,
        UNIQUE(mlb_game_id, team_id)
    );
```

Also add index creation after the existing CREATE INDEX lines:
```python
    CREATE INDEX IF NOT EXISTS idx_pitcher_logs_pitcher ON pitcher_game_logs(pitcher_id, game_date);
    CREATE INDEX IF NOT EXISTS idx_pitcher_logs_team ON pitcher_game_logs(team_id, game_date);
    CREATE INDEX IF NOT EXISTS idx_team_logs_team ON team_game_logs(team_id, game_date);
```

Then, after the existing `for col in ("score_pitching", ...)` migration block, add a no-op migration for the new tables (they're created by CREATE TABLE IF NOT EXISTS, no ALTER needed).

Then add four new functions at the end of `database.py` (before the final blank line):

```python
def store_boxscores(pitcher_logs: list, team_logs: list) -> None:
    """Store post-game boxscore data for all pitchers and teams."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    for p in pitcher_logs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO pitcher_game_logs
                (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
                 innings_pitched, earned_runs, strikeouts, walks, hits, home_runs, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (p["mlb_game_id"], p["game_date"], p["pitcher_id"], p["pitcher_name"],
                  p["team_id"], int(p["is_starter"]),
                  p["innings_pitched"], p["earned_runs"], p["strikeouts"],
                  p["walks"], p["hits"], p["home_runs"], now))
        except Exception as e:
            print(f"[DB] store_boxscores pitcher error: {e}")
    for t in team_logs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO team_game_logs
                (mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
                 strikeouts, walks, at_bats, left_on_base, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (t["mlb_game_id"], t["game_date"], t["team_id"], int(t["is_away"]),
                  t["runs"], t["hits"], t["home_runs"], t["strikeouts"],
                  t["walks"], t["at_bats"], t["left_on_base"], now))
        except Exception as e:
            print(f"[DB] store_boxscores team error: {e}")
    conn.commit()
    conn.close()


def get_pitcher_rolling_stats(pitcher_id: int, days: int = 21,
                               as_of_date: str = None) -> "dict | None":
    """
    Rolling ERA, WHIP, K/9, BB/9 for a starter over the last N days.
    Returns None if fewer than 1 game found (caller falls back to season stats).
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT innings_pitched, earned_runs, strikeouts, walks, hits
        FROM pitcher_game_logs
        WHERE pitcher_id=? AND game_date > ? AND innings_pitched > 0
        ORDER BY game_date DESC
    """, (pitcher_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    total_ip = sum(r["innings_pitched"] for r in rows)
    total_er = sum(r["earned_runs"] for r in rows)
    total_k = sum(r["strikeouts"] for r in rows)
    total_bb = sum(r["walks"] for r in rows)
    total_h = sum(r["hits"] for r in rows)
    if total_ip == 0:
        return None
    return {
        "era": round(total_er / total_ip * 9, 2),
        "whip": round((total_h + total_bb) / total_ip, 3),
        "k9": round(total_k / total_ip * 9, 2),
        "bb9": round(total_bb / total_ip * 9, 2),
        "games": len(rows),
        "innings_pitched": round(total_ip, 2),
    }


def get_team_batting_rolling(team_id: int, days: int = 14,
                              as_of_date: str = None) -> "dict | None":
    """
    Rolling runs/game, OBP proxy, HR/game, K% over last N days for a team.
    OBP proxy = (H + BB) / (AB + BB).
    Returns None if fewer than 1 game found.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT runs, hits, home_runs, strikeouts, walks, at_bats
        FROM team_game_logs
        WHERE team_id=? AND game_date > ?
        ORDER BY game_date DESC
    """, (team_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    g = len(rows)
    total_runs = sum(r["runs"] for r in rows)
    total_hits = sum(r["hits"] for r in rows)
    total_hr = sum(r["home_runs"] for r in rows)
    total_k = sum(r["strikeouts"] for r in rows)
    total_bb = sum(r["walks"] for r in rows)
    total_ab = sum(r["at_bats"] for r in rows)
    pa = total_ab + total_bb
    return {
        "rpg": round(total_runs / g, 3),
        "obp_proxy": round((total_hits + total_bb) / pa, 3) if pa > 0 else 0.0,
        "hr_pg": round(total_hr / g, 3),
        "k_pct": round(total_k / max(total_ab, 1), 3),
        "games": g,
    }


def get_team_bullpen_rolling(team_id: int, days: int = 14,
                              as_of_date: str = None) -> "dict | None":
    """
    Rolling ERA, WHIP, K/9 for relief pitchers (is_starter=0) over last N days.
    Returns None if fewer than 1 appearance found.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT innings_pitched, earned_runs, strikeouts, walks, hits
        FROM pitcher_game_logs
        WHERE team_id=? AND is_starter=0 AND game_date > ? AND innings_pitched > 0
        ORDER BY game_date DESC
    """, (team_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    total_ip = sum(r["innings_pitched"] for r in rows)
    total_er = sum(r["earned_runs"] for r in rows)
    total_k = sum(r["strikeouts"] for r in rows)
    total_bb = sum(r["walks"] for r in rows)
    total_h = sum(r["hits"] for r in rows)
    if total_ip == 0:
        return None
    return {
        "era": round(total_er / total_ip * 9, 2),
        "whip": round((total_h + total_bb) / total_ip, 3),
        "k9": round(total_k / total_ip * 9, 2),
        "games": len(rows),
        "innings_pitched": round(total_ip, 2),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_rolling_stats.py -v --tb=short
```
Expected: all 9 tests PASS

- [ ] **Step 5: Run full suite to verify no regressions**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count as before (75 passed), `test_analysis_log.py` failures unchanged

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_rolling_stats.py
git commit -m "feat: add pitcher_game_logs and team_game_logs tables with rolling stat queries"
```

---

## Task 2: `collect_boxscores()` in `data_mlb.py`

Fetches the MLB boxscore API for a given date and returns structured dicts ready for `store_boxscores()`.

**Files:**
- Modify: `data_mlb.py`
- Test: `tests/test_rolling_stats.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_rolling_stats.py`:

```python
from unittest.mock import patch, MagicMock
import data_mlb


def _make_boxscore_response():
    """Minimal MLB API schedule+boxscore response for one game."""
    return {
        "dates": [{
            "date": "2026-04-11",
            "games": [{
                "gamePk": 823480,
                "status": {"abstractGameState": "Final"},
                "teams": {
                    "away": {
                        "team": {"id": 109},
                        "pitchers": [662253, 681911],
                        "players": {
                            "ID662253": {
                                "person": {"id": 662253, "fullName": "Zac Gallen"},
                                "stats": {"pitching": {
                                    "inningsPitched": "6.2", "earnedRuns": 2,
                                    "strikeOuts": 7, "baseOnBalls": 2,
                                    "hits": 5, "homeRuns": 1
                                }}
                            },
                            "ID681911": {
                                "person": {"id": 681911, "fullName": "Joe Mantiply"},
                                "stats": {"pitching": {
                                    "inningsPitched": "1.1", "earnedRuns": 0,
                                    "strikeOuts": 1, "baseOnBalls": 0,
                                    "hits": 1, "homeRuns": 0
                                }}
                            }
                        },
                        "teamStats": {
                            "batting": {
                                "runs": 3, "hits": 7, "homeRuns": 1,
                                "strikeOuts": 9, "baseOnBalls": 2,
                                "leftOnBase": 6, "atBats": 30
                            }
                        }
                    },
                    "home": {
                        "team": {"id": 143},
                        "pitchers": [669302],
                        "players": {
                            "ID669302": {
                                "person": {"id": 669302, "fullName": "Ranger Suarez"},
                                "stats": {"pitching": {
                                    "inningsPitched": "9.0", "earnedRuns": 3,
                                    "strikeOuts": 8, "baseOnBalls": 1,
                                    "hits": 7, "homeRuns": 0
                                }}
                            }
                        },
                        "teamStats": {
                            "batting": {
                                "runs": 4, "hits": 8, "homeRuns": 0,
                                "strikeOuts": 7, "baseOnBalls": 3,
                                "leftOnBase": 8, "atBats": 31
                            }
                        }
                    }
                }
            }]
        }]
    }


def test_collect_boxscores_returns_pitcher_and_team_logs():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _make_boxscore_response()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.collect_boxscores("2026-04-11")

    assert "pitcher_logs" in result
    assert "team_logs" in result

    pitcher_ids = [p["pitcher_id"] for p in result["pitcher_logs"]]
    assert 662253 in pitcher_ids  # Zac Gallen (starter)
    assert 681911 in pitcher_ids  # Joe Mantiply (reliever)
    assert 669302 in pitcher_ids  # Ranger Suarez (starter)

    # Gallen: is_starter=True
    gallen = next(p for p in result["pitcher_logs"] if p["pitcher_id"] == 662253)
    assert gallen["is_starter"] is True
    assert abs(gallen["innings_pitched"] - 6.667) < 0.01  # 6.2 = 6 + 2/3
    assert gallen["earned_runs"] == 2
    assert gallen["strikeouts"] == 7

    # Mantiply: is_starter=False
    mantiply = next(p for p in result["pitcher_logs"] if p["pitcher_id"] == 681911)
    assert mantiply["is_starter"] is False

    # Team logs: 2 teams
    team_ids = [t["team_id"] for t in result["team_logs"]]
    assert 109 in team_ids  # away team
    assert 143 in team_ids  # home team

    away_log = next(t for t in result["team_logs"] if t["team_id"] == 109)
    assert away_log["is_away"] is True
    assert away_log["runs"] == 3
    assert away_log["at_bats"] == 30


def test_collect_boxscores_skips_non_final_games():
    response = _make_boxscore_response()
    response["dates"][0]["games"][0]["status"]["abstractGameState"] = "Live"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.collect_boxscores("2026-04-11")

    assert result["pitcher_logs"] == []
    assert result["team_logs"] == []


def test_collect_boxscores_handles_api_error():
    with patch("data_mlb.requests.get", side_effect=Exception("timeout")):
        result = data_mlb.collect_boxscores("2026-04-11")
    assert result == {"pitcher_logs": [], "team_logs": []}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_rolling_stats.py::test_collect_boxscores_returns_pitcher_and_team_logs tests/test_rolling_stats.py::test_collect_boxscores_skips_non_final_games tests/test_rolling_stats.py::test_collect_boxscores_handles_api_error -v --tb=short
```
Expected: FAIL with `AttributeError: module 'data_mlb' has no attribute 'collect_boxscores'`

- [ ] **Step 3: Implement `collect_boxscores()` in `data_mlb.py`**

Add after `fetch_bullpen_recent_usage()` (around line 468), before the `fetch_team_batting` section:

```python
def collect_boxscores(game_date: str) -> dict:
    """
    Fetch completed boxscores for all games on game_date.
    Returns:
      {
        "pitcher_logs": [ {mlb_game_id, game_date, pitcher_id, pitcher_name,
                           team_id, is_starter, innings_pitched, earned_runs,
                           strikeouts, walks, hits, home_runs}, ... ],
        "team_logs":    [ {mlb_game_id, game_date, team_id, is_away,
                           runs, hits, home_runs, strikeouts, walks,
                           at_bats, left_on_base}, ... ]
      }
    Only includes games with abstractGameState == "Final".
    """
    url = (
        f"{MLB_BASE}/schedule"
        f"?sportId=1&date={game_date}&gameType=R&hydrate=boxscore"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] collect_boxscores error for {game_date}: {e}")
        return {"pitcher_logs": [], "team_logs": []}

    pitcher_logs = []
    team_logs = []

    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("status", {}).get("abstractGameState") != "Final":
                continue

            game_pk = game["gamePk"]

            for side in ("away", "home"):
                team_data = game.get("teams", {}).get(side, {})
                team_id = team_data.get("team", {}).get("id")
                if not team_id:
                    continue

                pitcher_ids = team_data.get("pitchers", [])
                players = team_data.get("players", {})

                for idx, pid in enumerate(pitcher_ids):
                    player = players.get(f"ID{pid}", {})
                    pstats = player.get("stats", {}).get("pitching", {})
                    ip_str = pstats.get("inningsPitched", "0.0")
                    ip = _parse_ip(ip_str)
                    if ip == 0.0:
                        continue  # skip pitchers who didn't record an out
                    pitcher_logs.append({
                        "mlb_game_id": game_pk,
                        "game_date": game_date,
                        "pitcher_id": pid,
                        "pitcher_name": player.get("person", {}).get("fullName", "Unknown"),
                        "team_id": team_id,
                        "is_starter": (idx == 0),
                        "innings_pitched": ip,
                        "earned_runs": pstats.get("earnedRuns", 0) or 0,
                        "strikeouts": pstats.get("strikeOuts", 0) or 0,
                        "walks": pstats.get("baseOnBalls", 0) or 0,
                        "hits": pstats.get("hits", 0) or 0,
                        "home_runs": pstats.get("homeRuns", 0) or 0,
                    })

                bat = team_data.get("teamStats", {}).get("batting", {})
                team_logs.append({
                    "mlb_game_id": game_pk,
                    "game_date": game_date,
                    "team_id": team_id,
                    "is_away": (side == "away"),
                    "runs": bat.get("runs", 0) or 0,
                    "hits": bat.get("hits", 0) or 0,
                    "home_runs": bat.get("homeRuns", 0) or 0,
                    "strikeouts": bat.get("strikeOuts", 0) or 0,
                    "walks": bat.get("baseOnBalls", 0) or 0,
                    "at_bats": bat.get("atBats", 0) or 0,
                    "left_on_base": bat.get("leftOnBase", 0) or 0,
                })

    print(f"[DATA] collect_boxscores {game_date}: {len(pitcher_logs)} pitcher lines, {len(team_logs)} team logs")
    return {"pitcher_logs": pitcher_logs, "team_logs": team_logs}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_rolling_stats.py::test_collect_boxscores_returns_pitcher_and_team_logs tests/test_rolling_stats.py::test_collect_boxscores_skips_non_final_games tests/test_rolling_stats.py::test_collect_boxscores_handles_api_error -v --tb=short
```
Expected: all 3 PASS

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count, no new failures

- [ ] **Step 6: Commit**

```bash
git add data_mlb.py tests/test_rolling_stats.py
git commit -m "feat: add collect_boxscores() — fetch and parse post-game pitcher/team stats"
```

---

## Task 3: Wire Collection into `engine.py` (`--results` + `--collect` flag)

After grading results each night, automatically collect and store boxscore data. Also add a `--collect DATE` flag for manual backfilling.

**Files:**
- Modify: `engine.py`

- [ ] **Step 1: Add `collect_and_store_boxscores()` call at the end of `run_results()`**

In `engine.py`, find `run_results()`. At the very end of the function, after `send_results()` (or after the recap print), add:

```python
    # ── Collect and store post-game boxscore data ──
    print("\n[DATA] Collecting post-game boxscore data...")
    from data_mlb import collect_boxscores
    boxscore_data = collect_boxscores(date.today().isoformat())
    if boxscore_data["pitcher_logs"] or boxscore_data["team_logs"]:
        db.store_boxscores(boxscore_data["pitcher_logs"], boxscore_data["team_logs"])
        print(f"[DB] Stored {len(boxscore_data['pitcher_logs'])} pitcher lines, "
              f"{len(boxscore_data['team_logs'])} team logs.")
    else:
        print("[DATA] No boxscore data collected.")
```

- [ ] **Step 2: Add `--collect` CLI flag to `main()` for manual backfilling**

In `engine.py`, in `main()`, add handling for `--collect` before the existing flag checks:

```python
    if "--collect" in args:
        idx = args.index("--collect")
        target = args[idx + 1] if idx + 1 < len(args) else date.today().isoformat()
        db.init_db()
        print(f"[DATA] Collecting boxscores for {target}...")
        from data_mlb import collect_boxscores
        boxscore_data = collect_boxscores(target)
        db.store_boxscores(boxscore_data["pitcher_logs"], boxscore_data["team_logs"])
        print(f"[DB] Stored {len(boxscore_data['pitcher_logs'])} pitcher lines, "
              f"{len(boxscore_data['team_logs'])} team logs for {target}.")
        return
```

- [ ] **Step 3: Verify manually — collect yesterday's boxscores**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 engine.py --collect 2026-04-11
```
Expected output includes:
```
[DATA] collect_boxscores 2026-04-11: N pitcher lines, N team logs
[DB] Stored N pitcher lines, N team logs for 2026-04-11.
```

Then verify data in DB:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('mlb_picks.db')
conn.row_factory = sqlite3.Row
print('Pitcher logs:', conn.execute('SELECT COUNT(*) FROM pitcher_game_logs').fetchone()[0])
print('Team logs:', conn.execute('SELECT COUNT(*) FROM team_game_logs').fetchone()[0])
# Show sample
for r in conn.execute('SELECT pitcher_name, team_id, is_starter, innings_pitched, earned_runs FROM pitcher_game_logs LIMIT 5'):
    print(dict(r))
conn.close()
"
```
Expected: 100+ pitcher logs and 30 team logs for a full MLB day (15 games × ~7 pitchers avg, 15 games × 2 teams)

- [ ] **Step 4: Run full test suite**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count, no new failures

- [ ] **Step 5: Commit**

```bash
git add engine.py
git commit -m "feat: wire collect_boxscores into --results and add --collect flag for backfilling"
```

---

## Task 4: Attach Rolling Stats in `collect_game_data()`

`collect_game_data()` in `data_mlb.py` is the main data pipeline called at 8am. Add rolling stat lookups here so agents can read them without knowing about the DB.

**Files:**
- Modify: `data_mlb.py`

- [ ] **Step 1: Add import and rolling stat attachment in `collect_game_data()`**

At the top of `data_mlb.py`, confirm `database` is not already imported. It is not — `data_mlb.py` does not import `database`. Add the import inline inside `collect_game_data()` to avoid circular imports (engine.py imports both).

In `collect_game_data()`, after the `away_bullpen_usage` / `home_bullpen_usage` lines (around line 825), add:

```python
        # Rolling stats (from stored game logs — improves as season progresses)
        import database as _db
        g["away_pitcher_rolling"] = _db.get_pitcher_rolling_stats(
            g.get("away_pitcher_id"), days=21)
        g["home_pitcher_rolling"] = _db.get_pitcher_rolling_stats(
            g.get("home_pitcher_id"), days=21)
        g["away_batting_rolling"] = _db.get_team_batting_rolling(
            g.get("away_team_mlb_id"), days=14)
        g["home_batting_rolling"] = _db.get_team_batting_rolling(
            g.get("home_team_mlb_id"), days=14)
        g["away_bullpen_rolling"] = _db.get_team_bullpen_rolling(
            g.get("away_team_mlb_id"), days=14)
        g["home_bullpen_rolling"] = _db.get_team_bullpen_rolling(
            g.get("home_team_mlb_id"), days=14)
```

The `import database as _db` inside the function (not at module level) avoids circular import since `engine.py` imports both `database` and `data_mlb`.

- [ ] **Step 2: Verify with a dry run**

```bash
python3 engine.py --test 2>&1 | grep -E "\[DATA\]|\[DB\]|rolling" | head -20
```
Expected: no errors; rolling stats keys present in game dicts (may be None if no data yet — that's correct for early season)

- [ ] **Step 3: Run full test suite**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count, no new failures

- [ ] **Step 4: Commit**

```bash
git add data_mlb.py
git commit -m "feat: attach rolling pitcher/batting/bullpen stats to game dicts in collect_game_data"
```

---

## Task 5: Blend Function + Pitching Agent Rolling Integration

Add a `_blend()` helper in `analysis.py` and use it in `score_pitching()` to blend rolling SP ERA/WHIP/K9/BB9 with season stats.

**Files:**
- Modify: `analysis.py`
- Test: `tests/test_rolling_stats.py` (extend)

- [ ] **Step 1: Write failing tests for blend function and pitching agent**

Add to `tests/test_rolling_stats.py`:

```python
import analysis


def test_blend_uses_season_when_no_rolling():
    result = analysis._blend(season_val=4.50, rolling_val=None, rolling_games=0)
    assert result == 4.50


def test_blend_season_only_below_threshold():
    # < 5 games → use season only
    result = analysis._blend(season_val=4.50, rolling_val=2.10, rolling_games=3)
    assert result == 4.50


def test_blend_weighted_5_to_9_games():
    # 5-9 games → 40% rolling, 60% season
    result = analysis._blend(season_val=4.50, rolling_val=2.50, rolling_games=7)
    expected = 0.4 * 2.50 + 0.6 * 4.50
    assert abs(result - expected) < 0.001


def test_blend_weighted_10_to_19_games():
    # 10-19 games → 60% rolling, 40% season
    result = analysis._blend(season_val=4.50, rolling_val=2.50, rolling_games=12)
    expected = 0.6 * 2.50 + 0.4 * 4.50
    assert abs(result - expected) < 0.001


def test_blend_weighted_20_plus_games():
    # ≥ 20 games → 75% rolling, 25% season
    result = analysis._blend(season_val=4.50, rolling_val=2.50, rolling_games=25)
    expected = 0.75 * 2.50 + 0.25 * 4.50
    assert abs(result - expected) < 0.001


def test_score_pitching_uses_rolling_when_available():
    game = {
        "away_pitcher_stats": {
            "name": "Away SP", "throws": "R", "era": 5.00, "whip": 1.50,
            "k_per_9": 7.0, "bb_per_9": 3.5, "k_bb_ratio": 2.0,
            "days_rest": 4,
        },
        "home_pitcher_stats": {
            "name": "Home SP", "throws": "R", "era": 5.00, "whip": 1.50,
            "k_per_9": 7.0, "bb_per_9": 3.5, "k_bb_ratio": 2.0,
            "days_rest": 4,
        },
        "away_batting": {"ops": 0.720, "obp": 0.320, "slg": 0.400,
                         "runs": 80, "games_played": 20, "strikeouts": 150, "at_bats": 600},
        "home_batting": {"ops": 0.720, "obp": 0.320, "slg": 0.400,
                         "runs": 80, "games_played": 20, "strikeouts": 150, "at_bats": 600},
        # Away pitcher rolling: much better ERA/WHIP → should push score toward away
        "away_pitcher_rolling": {"era": 1.50, "whip": 0.80, "k9": 10.0, "bb9": 1.5, "games": 20},
        "home_pitcher_rolling": None,
    }
    result = analysis.score_pitching(game)
    # Away pitcher has rolling ERA 1.50 vs season 5.00 → away should have edge (negative score)
    assert result["score"] < 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_rolling_stats.py::test_blend_uses_season_when_no_rolling tests/test_rolling_stats.py::test_blend_weighted_5_to_9_games tests/test_rolling_stats.py::test_blend_weighted_10_to_19_games tests/test_rolling_stats.py::test_blend_weighted_20_plus_games tests/test_rolling_stats.py::test_score_pitching_uses_rolling_when_available -v --tb=short
```
Expected: FAIL with `AttributeError: module 'analysis' has no attribute '_blend'`

- [ ] **Step 3: Add `_blend()` helper in `analysis.py`**

Add after the existing `_clamp()` and `_safe()` helpers (near the top of `analysis.py`, around line 15):

```python
def _blend(season_val: float, rolling_val, rolling_games: int) -> float:
    """
    Blend season stat with rolling stat based on number of rolling games available.
    Gracefully degrades to season-only when rolling data is sparse.
      < 5 games  → 100% season
      5–9 games  → 40% rolling / 60% season
      10–19 games → 60% rolling / 40% season
      ≥ 20 games → 75% rolling / 25% season
    """
    if rolling_val is None or rolling_games < 5:
        return season_val
    if rolling_games < 10:
        w = 0.4
    elif rolling_games < 20:
        w = 0.6
    else:
        w = 0.75
    return rolling_val * w + season_val * (1 - w)
```

- [ ] **Step 4: Update `score_pitching()` to blend rolling stats**

In `score_pitching()`, after reading `home_p` and `away_p` from the game dict, add rolling blending before the ERA/WHIP diff calculations:

```python
    # Blend rolling stats if available
    away_rolling = game.get("away_pitcher_rolling") or {}
    home_rolling = game.get("home_pitcher_rolling") or {}
    away_g = away_rolling.get("games", 0)
    home_g = home_rolling.get("games", 0)

    away_era  = _blend(_safe(away_p.get("era")),  away_rolling.get("era"),  away_g)
    away_whip = _blend(_safe(away_p.get("whip")), away_rolling.get("whip"), away_g)
    away_k9   = _blend(_safe(away_p.get("k_per_9")), away_rolling.get("k9"), away_g)
    away_bb9  = _blend(_safe(away_p.get("bb_per_9")), away_rolling.get("bb9"), away_g)

    home_era  = _blend(_safe(home_p.get("era")),  home_rolling.get("era"),  home_g)
    home_whip = _blend(_safe(home_p.get("whip")), home_rolling.get("whip"), home_g)
    home_k9   = _blend(_safe(home_p.get("k_per_9")), home_rolling.get("k9"), home_g)
    home_bb9  = _blend(_safe(home_p.get("bb_per_9")), home_rolling.get("bb9"), home_g)
```

Then replace the existing diff calculations (the `era_diff`, `whip_diff`, etc. lines) with:

```python
    era_diff  = away_era  - home_era
    whip_diff = away_whip - home_whip
    k9_diff   = home_k9   - away_k9
    bb9_diff  = away_bb9  - home_bb9
    kbb_diff  = _safe(home_p.get("k_bb_ratio")) - _safe(away_p.get("k_bb_ratio"))
```

Note: `k_bb_ratio` is not in rolling stats (requires season context), keep using season value.

Also add rolling note to edge string when blending is active:

After computing `edge` (the final edge string), add:
```python
    if away_g >= 5 or home_g >= 5:
        edge += f" [rolling: {away_g}gs away, {home_g}gs home]"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_rolling_stats.py -v --tb=short -q 2>&1 | tail -15
```
Expected: all blend and pitching tests PASS

- [ ] **Step 6: Run full suite**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count, no new failures

- [ ] **Step 7: Commit**

```bash
git add analysis.py tests/test_rolling_stats.py
git commit -m "feat: add _blend() helper and rolling ERA/WHIP integration in score_pitching()"
```

---

## Task 6: Offense Agent Rolling Integration

Blend rolling `rpg` and `obp_proxy` with season stats in `score_offense()`.

**Files:**
- Modify: `analysis.py`
- Test: `tests/test_rolling_stats.py` (extend)

- [ ] **Step 1: Write failing test**

Add to `tests/test_rolling_stats.py`:

```python
def test_score_offense_uses_rolling_when_available():
    game = {
        "away_batting": {"ops": 0.700, "obp": 0.310, "slg": 0.390,
                         "runs": 80, "games_played": 20,
                         "strikeouts": 150, "at_bats": 600},
        "home_batting": {"ops": 0.700, "obp": 0.310, "slg": 0.390,
                         "runs": 80, "games_played": 20,
                         "strikeouts": 150, "at_bats": 600},
        "away_pitcher_stats": {}, "home_pitcher_stats": {},
        # Away team rolling much better — should push score toward away edge (negative)
        "away_batting_rolling": {"rpg": 7.0, "obp_proxy": 0.380,
                                  "hr_pg": 1.5, "k_pct": 0.18, "games": 20},
        "home_batting_rolling": {"rpg": 3.0, "obp_proxy": 0.280,
                                  "hr_pg": 0.5, "k_pct": 0.28, "games": 20},
    }
    result = analysis.score_offense(game)
    # Away much better rolling → negative score (away edge)
    assert result["score"] < 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_rolling_stats.py::test_score_offense_uses_rolling_when_available -v --tb=short
```
Expected: FAIL (score is 0.0 — rolling not used yet)

- [ ] **Step 3: Update `score_offense()` to blend rolling stats**

In `score_offense()` in `analysis.py`, after reading `home_b` and `away_b`, add rolling blending before the diff calculations:

```python
    # Blend rolling stats if available
    away_bat_r = game.get("away_batting_rolling") or {}
    home_bat_r = game.get("home_batting_rolling") or {}
    away_rg = away_bat_r.get("games", 0)
    home_rg = home_bat_r.get("games", 0)

    away_rpg_season = _safe(away_b.get("runs")) / max(_safe(away_b.get("games_played")), 1)
    home_rpg_season = _safe(home_b.get("runs")) / max(_safe(home_b.get("games_played")), 1)

    away_rpg = _blend(away_rpg_season, away_bat_r.get("rpg"), away_rg)
    home_rpg = _blend(home_rpg_season, home_bat_r.get("rpg"), home_rg)

    # OBP proxy from rolling (use season OBP if rolling not available)
    away_obp = _blend(_safe(away_b.get("obp")), away_bat_r.get("obp_proxy"), away_rg)
    home_obp = _blend(_safe(home_b.get("obp")), home_bat_r.get("obp_proxy"), home_rg)
```

Then replace the existing `rpg_diff`, `obp_diff` calculations:

```python
    ops_diff = _safe(home_b.get("ops")) - _safe(away_b.get("ops"))
    obp_diff = home_obp - away_obp
    slg_diff = _safe(home_b.get("slg")) - _safe(away_b.get("slg"))
    rpg_diff = home_rpg - away_rpg
```

(Keep `ops_diff` and `slg_diff` using season values — they need season-level denominators for accuracy.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_rolling_stats.py -v --tb=short -q 2>&1 | tail -15
```
Expected: all tests PASS

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count, no new failures

- [ ] **Step 6: Commit**

```bash
git add analysis.py tests/test_rolling_stats.py
git commit -m "feat: blend rolling rpg/obp_proxy into score_offense()"
```

---

## Task 7: Bullpen Agent Rolling Integration

Replace the live `fetch_bullpen_recent_usage()` call in `score_bullpen()` with stored rolling data when available, and blend rolling ERA/WHIP with season bullpen stats.

**Files:**
- Modify: `analysis.py`
- Test: `tests/test_rolling_stats.py` (extend)

- [ ] **Step 1: Write failing test**

Add to `tests/test_rolling_stats.py`:

```python
def test_score_bullpen_uses_rolling_era_when_available():
    game = {
        "away_pitching": {"era": 4.00, "whip": 1.35, "k_per_9": 8.5,
                          "saves": 5, "save_opportunities": 7, "holds": 10},
        "home_pitching": {"era": 4.00, "whip": 1.35, "k_per_9": 8.5,
                          "saves": 5, "save_opportunities": 7, "holds": 10},
        "away_bullpen_usage": {"ip_last_3": 0.0, "ip_last_5": 0.0,
                               "games_last_3": 0, "games_last_5": 0},
        "home_bullpen_usage": {"ip_last_3": 0.0, "ip_last_5": 0.0,
                               "games_last_3": 0, "games_last_5": 0},
        # Home bullpen rolling much better ERA (1.80 vs 5.50)
        "away_bullpen_rolling": {"era": 5.50, "whip": 1.60, "k9": 7.0, "games": 20},
        "home_bullpen_rolling": {"era": 1.80, "whip": 0.95, "k9": 10.5, "games": 20},
    }
    result = analysis.score_bullpen(game)
    # Home bullpen much better → positive score
    assert result["score"] > 0.10
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_rolling_stats.py::test_score_bullpen_uses_rolling_era_when_available -v --tb=short
```
Expected: FAIL (score near 0, rolling not used)

- [ ] **Step 3: Update `score_bullpen()` to blend rolling bullpen ERA/WHIP**

In `score_bullpen()` in `analysis.py`, after reading `home_bp` and `away_bp`, add:

```python
    # Blend rolling bullpen stats if available
    away_bp_r = game.get("away_bullpen_rolling") or {}
    home_bp_r = game.get("home_bullpen_rolling") or {}
    away_brg = away_bp_r.get("games", 0)
    home_brg = home_bp_r.get("games", 0)

    away_bp_era  = _blend(_safe(away_bp.get("era")),  away_bp_r.get("era"),  away_brg)
    away_bp_whip = _blend(_safe(away_bp.get("whip")), away_bp_r.get("whip"), away_brg)
    home_bp_era  = _blend(_safe(home_bp.get("era")),  home_bp_r.get("era"),  home_brg)
    home_bp_whip = _blend(_safe(home_bp.get("whip")), home_bp_r.get("whip"), home_brg)
```

Then replace the existing `era_diff` and `whip_diff` calculations:

```python
    era_diff  = away_bp_era  - home_bp_era
    whip_diff = away_bp_whip - home_bp_whip
    k9_diff   = _safe(home_bp.get("k_per_9")) - _safe(away_bp.get("k_per_9"))
```

(Keep `k9_diff` using season values — it's less critical and rolling k9 from relief pitchers is noisier.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_rolling_stats.py -v --tb=short -q 2>&1 | tail -15
```
Expected: all tests PASS

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count, no new failures

- [ ] **Step 6: Commit**

```bash
git add analysis.py tests/test_rolling_stats.py
git commit -m "feat: blend rolling ERA/WHIP into score_bullpen()"
```

---

## Task 8: Backfill Historical Boxscores + Verify End-to-End

Backfill April data and run a full dry-run to verify rolling stats are flowing through to agents.

**Files:**
- No code changes — operational verification

- [ ] **Step 1: Backfill all available April 2026 games**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 << 'EOF'
from datetime import date, timedelta
import subprocess

start = date(2026, 4, 1)
end = date.today() - timedelta(days=1)
d = start
while d <= end:
    print(f"Collecting {d.isoformat()}...")
    result = subprocess.run(
        ["python3", "engine.py", "--collect", d.isoformat()],
        capture_output=True, text=True
    )
    print(result.stdout.strip())
    d += timedelta(days=1)
EOF
```
Expected: each date prints pitcher/team log counts; dates with no games print 0 counts.

- [ ] **Step 2: Verify stored data counts**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('mlb_picks.db')
p = conn.execute('SELECT COUNT(*), COUNT(DISTINCT game_date) FROM pitcher_game_logs').fetchone()
t = conn.execute('SELECT COUNT(*), COUNT(DISTINCT game_date) FROM team_game_logs').fetchone()
print(f'Pitcher logs: {p[0]} rows across {p[1]} dates')
print(f'Team logs: {t[0]} rows across {t[1]} dates')
# Show a sample pitcher rolling stats
import database as db
stats = db.get_pitcher_rolling_stats(592789, days=21)  # Paul Skenes example
print(f'Sample pitcher rolling (id=592789): {stats}')
conn.close()
"
```
Expected: 500+ pitcher logs, 100+ team logs

- [ ] **Step 3: Run full dry-run analysis to verify rolling stats are used**

```bash
python3 engine.py --test 2>&1 | grep -E "rolling|Rolling|\[DATA\]" | head -20
```
Expected: edge strings in game analysis include `[rolling: Xgs away, Ygs home]` for pitchers with data

- [ ] **Step 4: Update `CLAUDE.md` with new CLI flag**

In `CLAUDE.md`, in the CLI Usage section, add:
```
python3 engine.py --collect DATE   # Collect and store boxscore data for DATE (YYYY-MM-DD)
                                   # Run automatically after --results; use for backfilling
```

In the File Structure section, verify `pitcher_game_logs`, `team_game_logs` are noted under Database.

- [ ] **Step 5: Update `INSIGHTS.md` Known Ongoing Issues section**

Remove:
```
- Rolling 7/14/30-day team trends not implemented — season stats only
```
Replace with:
```
- Rolling stats active: 21-day SP ERA/WHIP/K9/BB9, 14-day team R/G + OBP proxy, 14-day bullpen ERA/WHIP
- Blend thresholds: <5 games → season only; 5-9 → 40% rolling; 10-19 → 60%; ≥20 → 75%
```

- [ ] **Step 6: Final full test suite**

```bash
python3 -m pytest tests/ -v --tb=short -q 2>&1 | tail -10
```
Expected: same pass count, no new failures

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md INSIGHTS.md
git commit -m "docs: update CLAUDE.md and INSIGHTS.md to reflect rolling stats implementation"
```

---

## Self-Review

**1. Spec coverage check:**
- ✅ Post-game collection: `collect_boxscores()` called after `--results`, manual via `--collect`
- ✅ Pitcher stats stored: `pitcher_game_logs` with IP, ER, K, BB, H, HR per pitcher per game
- ✅ Team stats stored: `team_game_logs` with R, H, HR, K, BB, AB per team per game
- ✅ Rolling windows: 21-day SP, 14-day team batting, 14-day bullpen
- ✅ Graceful degradation: `_blend()` falls back to season stats when < 5 games
- ✅ Pitching agent: blends rolling ERA/WHIP/K9/BB9
- ✅ Offense agent: blends rolling RPG and OBP proxy
- ✅ Bullpen agent: blends rolling ERA/WHIP
- ✅ Backfill mechanism: `--collect DATE` flag
- ✅ No circular imports: `database` imported inline inside `collect_game_data()`

**2. Placeholder scan:** No TBDs, no "add appropriate error handling" language — all code blocks complete.

**3. Type consistency:**
- `_blend(season_val, rolling_val, rolling_games)` — consistent across all call sites
- `get_pitcher_rolling_stats(pitcher_id, days)` → returns dict with `era, whip, k9, bb9, games` — consistent with how Task 5 reads it
- `get_team_batting_rolling(team_id, days)` → returns dict with `rpg, obp_proxy, hr_pg, k_pct, games` — consistent with Task 6
- `get_team_bullpen_rolling(team_id, days)` → returns dict with `era, whip, k9, games` — consistent with Task 7
- All return `None` when no data — all callers guard with `or {}`
