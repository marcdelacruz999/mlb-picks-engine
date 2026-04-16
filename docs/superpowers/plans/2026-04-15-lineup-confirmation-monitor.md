# Lineup Confirmation Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `monitor.py` to alert on Discord when a confirmed lineup for a pending pick's team has significantly weaker OPS than expected, enabling manual review before first pitch.

**Architecture:** A second check loop `run_lineup_monitor()` runs after the existing pitcher scratch check in `monitor.py`. It loads pending picks, fetches current lineup IDs from the MLB API for each game, computes blended OPS for the pick team's confirmed starters using `fetch_lineup_batting()`, compares to rolling team OPS from the DB, and fires a Discord alert (deduplicated via a new `lineup_alerts` table) if the drop exceeds 10%.

**Tech Stack:** Python 3.9, SQLite (database.py), MLB Stats API (`statsapi.mlb.com`), Discord webhook (requests POST), pytest

---

## File Map

- **Modify:** `config.py` — add `LINEUP_OPS_DROP_THRESHOLD` and `LINEUP_MIN_PLAYERS_WITH_DATA` constants
- **Modify:** `database.py` — add `lineup_alerts` table (CREATE + migration), `lineup_alert_already_sent()`, `save_lineup_alert()`
- **Modify:** `data_mlb.py` — add `get_current_lineups(mlb_game_id)`
- **Modify:** `monitor.py` — add `run_lineup_monitor()`, call it from `main()`
- **Create:** `tests/test_lineup_monitor.py` — 9 unit tests

---

## Task 1: Add config constants

**Files:**
- Modify: `config.py:44-45`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lineup_monitor.py
import pytest

def test_lineup_config_constants():
    from config import LINEUP_OPS_DROP_THRESHOLD, LINEUP_MIN_PLAYERS_WITH_DATA
    assert LINEUP_OPS_DROP_THRESHOLD == 0.10
    assert LINEUP_MIN_PLAYERS_WITH_DATA == 5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_lineup_config_constants -v
```
Expected: FAIL with `ImportError: cannot import name 'LINEUP_OPS_DROP_THRESHOLD'`

- [ ] **Step 3: Add constants to config.py**

In `config.py`, after the `MIN_BATTER_GAMES = 8` line (line 45), add:

```python
LINEUP_OPS_DROP_THRESHOLD = 0.10     # alert when pick team's confirmed lineup OPS is this much below expected
LINEUP_MIN_PLAYERS_WITH_DATA = 5     # skip alert if fewer than this many starters have OPS data
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_lineup_config_constants -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_lineup_monitor.py
git commit -m "feat: add LINEUP_OPS_DROP_THRESHOLD and LINEUP_MIN_PLAYERS_WITH_DATA to config"
```

---

## Task 2: Add lineup_alerts table and DB functions

**Files:**
- Modify: `database.py` — add table, add two functions

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_lineup_monitor.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_lineup_alert_not_sent_initially tests/test_lineup_monitor.py::test_save_and_detect_lineup_alert tests/test_lineup_monitor.py::test_save_lineup_alert_dedup -v
```
Expected: FAIL with `AttributeError: module 'database' has no attribute 'lineup_alert_already_sent'`

- [ ] **Step 3: Add the lineup_alerts table to database.py**

In `database.py`, find the `CREATE TABLE IF NOT EXISTS scratch_alerts` block (around line 224). Add the new table immediately after the closing `);` of `scratch_alerts`:

```sql
    CREATE TABLE IF NOT EXISTS lineup_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER NOT NULL,
        game_date TEXT NOT NULL,
        ops_actual REAL,
        ops_expected REAL,
        pct_drop REAL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(mlb_game_id, game_date)
    );
```

- [ ] **Step 4: Add migration probe to init_db()**

In `database.py`, find the migration probe section (around line 340, after the `scratch_alerts` table). Add after the last migration block:

```python
    # Migrate: add lineup_alerts table if not present (added 2026-04-15)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lineup_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mlb_game_id INTEGER NOT NULL,
                game_date TEXT NOT NULL,
                ops_actual REAL,
                ops_expected REAL,
                pct_drop REAL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(mlb_game_id, game_date)
            )
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass
```

- [ ] **Step 5: Add the two DB functions at the bottom of database.py**

```python
def lineup_alert_already_sent(mlb_game_id: int, game_date: str) -> bool:
    """Return True if a lineup alert has already been sent for this game today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM lineup_alerts WHERE mlb_game_id=? AND game_date=?",
        (mlb_game_id, game_date)
    ).fetchone()
    conn.close()
    return row is not None


def save_lineup_alert(mlb_game_id: int, game_date: str, ops_actual: float, ops_expected: float, pct_drop: float) -> None:
    """Record that a lineup alert was sent for this game. INSERT OR IGNORE for dedup."""
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO lineup_alerts
           (mlb_game_id, game_date, ops_actual, ops_expected, pct_drop)
           VALUES (?, ?, ?, ?, ?)""",
        (mlb_game_id, game_date, ops_actual, ops_expected, pct_drop)
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_lineup_alert_not_sent_initially tests/test_lineup_monitor.py::test_save_and_detect_lineup_alert tests/test_lineup_monitor.py::test_save_lineup_alert_dedup -v
```
Expected: 3 PASS

- [ ] **Step 7: Commit**

```bash
git add database.py tests/test_lineup_monitor.py
git commit -m "feat: add lineup_alerts table and lineup_alert_already_sent/save_lineup_alert to database.py"
```

---

## Task 3: Add get_current_lineups() to data_mlb.py

**Files:**
- Modify: `data_mlb.py` — add function after `fetch_lineup_batting` (after line ~106)

The MLB Stats API already returns lineup IDs when hydrated with `lineups` (same endpoint used in `fetch_todays_games`). We need a focused function that fetches just lineup confirmation status for a single game.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_lineup_monitor.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_get_current_lineups_structure tests/test_lineup_monitor.py::test_get_current_lineups_not_confirmed -v
```
Expected: FAIL with `AttributeError: module 'data_mlb' has no attribute 'get_current_lineups'`

- [ ] **Step 3: Add get_current_lineups() to data_mlb.py**

Add this function immediately after `fetch_lineup_batting` (after line ~106, before `fetch_todays_games`):

```python
def get_current_lineups(mlb_game_id: int) -> dict:
    """Fetch current lineup confirmation status for a single game.

    Returns:
        {
            "away_ids": [int, ...],
            "home_ids": [int, ...],
            "away_confirmed": bool,
            "home_confirmed": bool,
            "game_status": str,  # e.g. "Preview", "Pre-Game", "Live", "Final"
        }
    """
    url = f"{MLB_BASE}/schedule?gamePks={mlb_game_id}&hydrate=lineups"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching lineups for game {mlb_game_id}: {e}")
        return {"away_ids": [], "home_ids": [], "away_confirmed": False, "home_confirmed": False, "game_status": "unknown"}

    try:
        dates = data.get("dates", [])
        if not dates:
            return {"away_ids": [], "home_ids": [], "away_confirmed": False, "home_confirmed": False, "game_status": "unknown"}
        game = dates[0].get("games", [{}])[0]
        status = game.get("status", {}).get("detailedState", "unknown")
        lineups = game.get("lineups", {})
        away_ids = [p.get("id") for p in lineups.get("awayPlayers", []) if p.get("id")]
        home_ids = [p.get("id") for p in lineups.get("homePlayers", []) if p.get("id")]
        return {
            "away_ids": away_ids,
            "home_ids": home_ids,
            "away_confirmed": bool(away_ids),
            "home_confirmed": bool(home_ids),
            "game_status": status,
        }
    except Exception as e:
        print(f"[DATA] Error parsing lineup data for game {mlb_game_id}: {e}")
        return {"away_ids": [], "home_ids": [], "away_confirmed": False, "home_confirmed": False, "game_status": "unknown"}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_get_current_lineups_structure tests/test_lineup_monitor.py::test_get_current_lineups_not_confirmed -v
```
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add data_mlb.py tests/test_lineup_monitor.py
git commit -m "feat: add get_current_lineups() to data_mlb.py"
```

---

## Task 4: Implement run_lineup_monitor() in monitor.py

**Files:**
- Modify: `monitor.py` — add imports, add `run_lineup_monitor()`, call from `main()`

The function logic:
1. Load today's pending picks and analysis_log entries (already imported in monitor.py)
2. For each pending pick, look up its game in `games` table to get team IDs
3. Check `lineup_alert_already_sent` — skip if already alerted
4. Call `get_current_lineups(mlb_game_id)` — skip if not both confirmed, or if Live/Final
5. Determine pick team side from `ml_pick_team` vs `away_team`/`home_team` in analysis_log
6. Fetch confirmed starter OPS via `fetch_lineup_batting(player_ids)`
7. Compute blended OPS (same formula as analysis.py): for each player, blend rolling OPS (weight 0.8 if ≥20 games, 0.6 if ≥MIN_BATTER_GAMES=8) with season OPS; skip players with 0 season OPS
8. Skip if fewer than `LINEUP_MIN_PLAYERS_WITH_DATA` players have valid OPS
9. Get expected OPS from `get_team_batting_rolling(team_mlb_id)["obp_proxy"]` as proxy — or fall back to `fetch_team_batting(team_mlb_id)["ops"]`
10. Compute `pct_drop = (expected - actual) / expected`; alert if `>= LINEUP_OPS_DROP_THRESHOLD`

**Important:** `get_team_batting_rolling` returns `obp_proxy` (not OPS). Use `fetch_team_batting()` for the season OPS as the expected baseline — it returns `{"ops": float, ...}`. This is consistent with what analysis.py uses for `away_team_ops`/`home_team_ops`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_lineup_monitor.py`:

```python
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

    calls = []
    monkeypatch.setattr(db, "lineup_alert_already_sent", lambda gid, d: True)

    import data_mlb
    monkeypatch.setattr(data_mlb, "get_current_lineups", lambda gid: (_ for _ in ()).throw(AssertionError("should not be called")))

    conn_mock = type("C", (), {"execute": lambda s, q, p=None: type("R", (), {"fetchone": lambda s: {"mlb_game_id": 745444, "away_team_id": 10, "home_team_id": 20}})(), "close": lambda s: None})()
    monkeypatch.setattr(db, "get_connection", lambda: conn_mock)

    # Should complete without calling get_current_lineups
    monitor.run_lineup_monitor()
    assert True  # no exception = dedup worked


def test_run_lineup_monitor_skips_game_in_progress(monkeypatch):
    """No alert fired when game is Live."""
    import monitor, database as db, data_mlb
    from datetime import date

    monkeypatch.setattr(db, "get_today_picks", lambda: [
        {"game_id": 1, "status": "pending", "pick_team": "Houston Astros"}
    ])
    monkeypatch.setattr(db, "get_today_analysis_log", lambda: [
        {"mlb_game_id": 745444, "away_team": "Colorado Rockies", "home_team": "Houston Astros",
         "ml_pick_team": "Houston Astros", "game": "COL @ HOU"}
    ])
    monkeypatch.setattr(db, "lineup_alert_already_sent", lambda gid, d: False)

    conn_mock = type("C", (), {
        "execute": lambda s, q, p=None: type("R", (), {
            "fetchone": lambda s: type("Row", (), {"__getitem__": lambda self, k: {"mlb_game_id": 745444, "away_team_id": 10, "home_team_id": 20}[k]})()
        })(),
        "close": lambda s: None
    })()
    monkeypatch.setattr(db, "get_connection", lambda: conn_mock)

    monkeypatch.setattr(data_mlb, "get_current_lineups", lambda gid: {
        "away_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9],
        "home_ids": [11, 12, 13, 14, 15, 16, 17, 18, 19],
        "away_confirmed": True,
        "home_confirmed": True,
        "game_status": "Live",  # should be skipped
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

    conn_mock = type("C", (), {
        "execute": lambda s, q, p=None: type("R", (), {
            "fetchone": lambda s: type("Row", (), {"__getitem__": lambda self, k: {"mlb_game_id": 745444, "away_team_id": 10, "home_team_id": 20}[k]})()
        })(),
        "close": lambda s: None
    })()
    monkeypatch.setattr(db, "get_connection", lambda: conn_mock)

    monkeypatch.setattr(data_mlb, "get_current_lineups", lambda gid: {
        "away_ids": [], "home_ids": [],
        "away_confirmed": False, "home_confirmed": False, "game_status": "Preview",
    })

    alert_sent = []
    monkeypatch.setattr(monitor, "send_lineup_alert", lambda *a, **kw: alert_sent.append(True))

    monitor.run_lineup_monitor()
    assert alert_sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_run_lineup_monitor_no_alert_when_already_sent tests/test_lineup_monitor.py::test_run_lineup_monitor_skips_game_in_progress tests/test_lineup_monitor.py::test_run_lineup_monitor_skips_lineups_not_posted -v
```
Expected: FAIL with `AttributeError: module 'monitor' has no attribute 'run_lineup_monitor'`

- [ ] **Step 3: Add imports to monitor.py**

At the top of `monitor.py`, add to the existing imports:

```python
from config import LINEUP_OPS_DROP_THRESHOLD, LINEUP_MIN_PLAYERS_WITH_DATA, MIN_BATTER_GAMES
from database import lineup_alert_already_sent, save_lineup_alert
from data_mlb import get_current_lineups, fetch_lineup_batting, fetch_team_batting
```

- [ ] **Step 4: Add send_lineup_alert() to monitor.py**

Add after `send_scratch_alert()` (around line 70):

```python
def send_lineup_alert(game_label: str, pick_team: str, ops_actual: float, ops_expected: float, pct_drop: float, confidence: int) -> bool:
    """Send a lineup weakness alert to Discord."""
    pct_str = f"{pct_drop * 100:.1f}%"
    message = (
        f"⚠️ **LINEUP ALERT** — {game_label}\n"
        f"{pick_team} confirmed lineup OPS: {ops_actual:.3f} "
        f"(expected {ops_expected:.3f} — {pct_str} weaker)\n"
        f"Pick: {pick_team} ML {confidence}/10 — consider revisiting."
    )
    if not DISCORD_WEBHOOK_URL:
        print(f"[MONITOR] No webhook URL — lineup alert:\n{message}")
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
        if resp.ok:
            print(f"[MONITOR] Lineup alert sent for {game_label}")
            return True
        else:
            print(f"[MONITOR] Discord failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[MONITOR] Error sending lineup alert: {e}")
        return False
```

- [ ] **Step 5: Add run_lineup_monitor() to monitor.py**

Add after `run_monitor()` (before `main()`):

```python
def run_lineup_monitor():
    """Check pending picks for lineup weakness after lineups are confirmed."""
    picks = get_today_picks()
    pending_picks = [p for p in picks if p.get("status") == "pending"]

    if not pending_picks:
        print("[MONITOR] No pending picks for lineup check.")
        return

    today = date.today().isoformat()
    log_entries = get_today_analysis_log()
    log_by_mlb_id = {entry["mlb_game_id"]: entry for entry in log_entries}

    conn = get_connection()
    checked = set()

    try:
        for pick in pending_picks:
            game_id = pick["game_id"]

            # Look up mlb_game_id and team IDs from games table
            game_row = conn.execute(
                "SELECT mlb_game_id, away_team_id, home_team_id FROM games WHERE id=?",
                (game_id,)
            ).fetchone()
            if not game_row:
                continue

            mlb_game_id = game_row["mlb_game_id"]

            # One check per game
            if mlb_game_id in checked:
                continue
            checked.add(mlb_game_id)

            # Skip if already alerted today
            if lineup_alert_already_sent(mlb_game_id, today):
                print(f"[MONITOR] Lineup alert already sent for game {mlb_game_id} — skipping.")
                continue

            log = log_by_mlb_id.get(mlb_game_id)
            if not log:
                continue

            # Fetch current lineup state from MLB API
            current = get_current_lineups(mlb_game_id)

            # Skip if game already started
            if current["game_status"] in ("Live", "Final", "Game Over", "Completed"):
                print(f"[MONITOR] Game {mlb_game_id} in progress/final — skipping lineup check.")
                continue

            # Skip if lineups not yet posted
            if not current["away_confirmed"] or not current["home_confirmed"]:
                print(f"[MONITOR] Lineups not yet confirmed for game {mlb_game_id} — skipping.")
                continue

            # Determine pick team side
            pick_team = log.get("ml_pick_team", "")
            away_team = log.get("away_team", "")
            is_home_pick = pick_team.lower().strip() != away_team.lower().strip()
            pick_side = "home" if is_home_pick else "away"
            player_ids = current["home_ids"] if is_home_pick else current["away_ids"]
            team_db_id = game_row["home_team_id"] if is_home_pick else game_row["away_team_id"]

            # Look up mlb_id for the pick team from teams table
            team_row = conn.execute(
                "SELECT mlb_id FROM teams WHERE id=?", (team_db_id,)
            ).fetchone()
            if not team_row:
                print(f"[MONITOR] Could not resolve team mlb_id for game {mlb_game_id} — skipping.")
                continue
            team_mlb_id = team_row["mlb_id"]

            # Compute actual lineup OPS from confirmed starters
            stats = fetch_lineup_batting(player_ids)
            ops_values = []
            for s in stats:
                season_ops = s.get("ops", 0) or 0
                if season_ops == 0:
                    continue  # no OPS data for this player
                rolling = get_batter_rolling_ops(s["player_id"])
                if rolling and rolling["games"] >= MIN_BATTER_GAMES:
                    w = 0.8 if rolling["games"] >= 20 else 0.6
                    blended = rolling["ops"] * w + season_ops * (1 - w)
                else:
                    blended = season_ops
                ops_values.append(blended)

            if len(ops_values) < LINEUP_MIN_PLAYERS_WITH_DATA:
                print(f"[MONITOR] Only {len(ops_values)} players with OPS data for game {mlb_game_id} — skipping.")
                continue

            ops_actual = sum(ops_values) / len(ops_values)

            # Get expected OPS from season team batting stats
            team_batting = fetch_team_batting(team_mlb_id)
            ops_expected = team_batting.get("ops") or 0.0
            if ops_expected == 0.0:
                print(f"[MONITOR] No expected OPS for team {team_mlb_id} — skipping.")
                continue

            pct_drop = (ops_expected - ops_actual) / ops_expected

            if pct_drop >= LINEUP_OPS_DROP_THRESHOLD:
                conf = log.get("ml_confidence", 0)
                game_label = log.get("game", str(mlb_game_id))
                sent = send_lineup_alert(game_label, pick_team, ops_actual, ops_expected, pct_drop, conf)
                if sent:
                    save_lineup_alert(mlb_game_id, today, ops_actual, ops_expected, pct_drop)
            else:
                print(f"[MONITOR] {log.get('game', mlb_game_id)}: lineup OPS drop {pct_drop*100:.1f}% — below threshold, no alert.")
    finally:
        conn.close()
```

- [ ] **Step 6: Add missing import in monitor.py**

`get_batter_rolling_ops` is used inside `run_lineup_monitor`. Add it to the database import line at the top of `monitor.py`:

```python
from database import get_db_connection, get_connection, get_today_picks, get_today_analysis_log, pitcher_already_alerted, save_scratch_alert, lineup_alert_already_sent, save_lineup_alert, get_batter_rolling_ops
```

- [ ] **Step 7: Call run_lineup_monitor() from main()**

In `monitor.py`, update `main()`:

```python
def main():
    run_monitor()
    run_lineup_monitor()
```

- [ ] **Step 8: Run the 3 earlier tests to verify they pass**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_run_lineup_monitor_no_alert_when_already_sent tests/test_lineup_monitor.py::test_run_lineup_monitor_skips_game_in_progress tests/test_lineup_monitor.py::test_run_lineup_monitor_skips_lineups_not_posted -v
```
Expected: 3 PASS

- [ ] **Step 9: Commit**

```bash
git add monitor.py tests/test_lineup_monitor.py
git commit -m "feat: add run_lineup_monitor() to monitor.py — alerts on confirmed lineup OPS weakness"
```

---

## Task 5: Add alert-fires and no-alert tests

**Files:**
- Modify: `tests/test_lineup_monitor.py`

- [ ] **Step 1: Write the remaining failing tests**

Add to `tests/test_lineup_monitor.py`:

```python
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
    monkeypatch.setattr(db, "save_lineup_alert", lambda *a, **kw: None)

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
    monkeypatch.setattr(data_mlb, "fetch_lineup_batting", lambda ids: player_stats)

    monkeypatch.setattr(monitor, "get_batter_rolling_ops", lambda pid: None)  # use season OPS only

    monkeypatch.setattr(data_mlb, "fetch_team_batting", lambda tid: {"ops": expected_ops})

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_lineup_monitor.py::test_run_lineup_monitor_fires_alert_on_ops_drop tests/test_lineup_monitor.py::test_run_lineup_monitor_no_alert_on_small_drop tests/test_lineup_monitor.py::test_run_lineup_monitor_no_alert_on_ops_improvement tests/test_lineup_monitor.py::test_run_lineup_monitor_skips_when_fewer_than_5_players_have_data -v
```
Expected: FAIL (helper references `monitor.get_batter_rolling_ops` which needs to be importable from monitor for monkeypatching)

- [ ] **Step 3: Add get_batter_rolling_ops import to monitor.py so it's patchable**

In `monitor.py`, update the database import to include `get_batter_rolling_ops`, then add a module-level alias so tests can patch it:

In the database import line (already updated in Task 4 Step 6), verify `get_batter_rolling_ops` is included. Then after the imports block, add:

```python
# Module-level alias so tests can monkeypatch monitor.get_batter_rolling_ops
from database import get_batter_rolling_ops
```

And in `run_lineup_monitor()`, reference it directly as `get_batter_rolling_ops` (already done in Task 4 Step 5).

- [ ] **Step 4: Run all 9 tests**

```bash
python3 -m pytest tests/test_lineup_monitor.py -v
```
Expected: 9 PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
python3 -m pytest tests/ -v --ignore=tests/test_optimizer.py 2>&1 | tail -20
```
Expected: all existing tests pass, no regressions (pre-existing failures in `test_f5_picks.py::test_get_sent_pick_today_returns_dict_with_message_id` are known and unrelated)

- [ ] **Step 6: Commit**

```bash
git add tests/test_lineup_monitor.py monitor.py
git commit -m "test: add full test suite for lineup confirmation monitor (9 tests)"
```

---

## Self-Review

**Spec coverage:**
- ✅ `config.py` constants: Task 1
- ✅ `database.py` table + functions: Task 2
- ✅ `data_mlb.py` `get_current_lineups()`: Task 3
- ✅ `monitor.py` `run_lineup_monitor()` + `send_lineup_alert()`: Task 4
- ✅ All 9 tests from spec: Tasks 4 + 5
- ✅ Skip if confirmed at send time: handled by `lineup_alert_already_sent` + game not in log
- ✅ Skip if Live/Final: Task 4 Step 5
- ✅ Skip if lineups not posted: Task 4 Step 5
- ✅ Skip if <5 players with data: Task 5
- ✅ Dedup: Task 2 + Task 4
- ✅ Discord alert format matches spec: Task 4 `send_lineup_alert()`

**Placeholder scan:** No TBDs, no "implement later", all code blocks complete.

**Type consistency:** `get_current_lineups` returns `dict` with `away_ids: list`, `home_ids: list`, `away_confirmed: bool`, `home_confirmed: bool`, `game_status: str` — used consistently in Task 4 and Task 3 tests. `lineup_alert_already_sent(mlb_game_id: int, game_date: str)` and `save_lineup_alert(mlb_game_id, game_date, ops_actual, ops_expected, pct_drop)` — signatures match between Task 2 definition and Task 4 usage.
