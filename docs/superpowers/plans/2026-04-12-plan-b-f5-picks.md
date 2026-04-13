# Plan B: F5 (First 5 Innings) Picks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add First 5 Innings (F5) picks as a new pick type. F5 bets isolate SP quality and eliminate bullpen variance — exactly where the engine's strongest signal (pitching, +0.080 lift) lives.

**Architecture:** F5 picks are only recommended when the pitching score is strong (|score| ≥ 0.20). Odds come from The Odds API `baseball_mlb_h1` sport key. F5 grading uses inning-by-inning linescore from MLB API (innings 1-5). New pick_type values `f5_ml`, `f5_over`, `f5_under` — the DB `picks` table CHECK constraint is dropped in a migration.

**Tech Stack:** Python 3.9, SQLite, The Odds API (same key as full-game), MLB Stats API `/game/{pk}/linescore`.

**Python version note:** No `float | None` union syntax. `except sqlite3.OperationalError: pass` for migrations.

---

## File Map

| File | Change |
|------|--------|
| `data_odds.py` | Add `fetch_f5_odds()` using sport `baseball_mlb_h1` |
| `data_odds.py` | Add `match_f5_odds_to_game(f5_odds, home, away)` |
| `analysis.py` | Add `_analyze_f5_pick(game, f5_odds, pitching_score)` — only fires when |pitching| ≥ 0.20 |
| `analysis.py` | In `analyze_game()`: call `_analyze_f5_pick()`, attach to result as `"f5_pick"` |
| `analysis.py` | In `risk_filter()`: evaluate and approve F5 picks using same confidence/EV gates |
| `database.py` | Migration: remove CHECK constraint on `picks.pick_type` to allow f5 values |
| `engine.py` | In `run_results()`: grade F5 picks using innings 1-5 linescore |
| `engine.py` | In `run_analysis()`: pass f5_odds to `analyze_game()` |
| `discord_bot.py` | In `_format_pick_message()`: format F5 picks correctly |
| `tests/test_f5_picks.py` | New test file: 14 tests |

---

### Task 1: F5 Odds Fetch

**Files:**
- Modify: `data_odds.py`
- Test: `tests/test_f5_picks.py`

**Background:** The Odds API serves F5 lines under sport key `baseball_mlb_h1`. The response format is identical to the full-game endpoint. We add a separate `fetch_f5_odds()` function (separate API call, separate quota usage). F5 totals are typically ~4-5 runs (lower than full-game).

- [ ] **Step 1: Write failing tests**

Create `tests/test_f5_picks.py`:

```python
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch, MagicMock


def _make_f5_api_response(home="Red Sox", away="Yankees",
                          home_ml=-130, away_ml=110, total=4.5,
                          over_price=-115, under_price=-105):
    return [{
        "id": "abc123",
        "sport_key": "baseball_mlb_h1",
        "commence_time": "2099-01-01T20:00:00Z",  # future date — passes pre-game filter
        "home_team": home,
        "away_team": away,
        "bookmakers": [{
            "title": "DraftKings",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": home, "price": home_ml},
                    {"name": away, "price": away_ml},
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "point": total, "price": over_price},
                    {"name": "Under", "point": total, "price": under_price},
                ]},
            ]
        }]
    }]


def test_fetch_f5_odds_calls_correct_sport_key():
    import data_odds
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _make_f5_api_response()
    mock_resp.headers = {"x-requests-remaining": "450"}

    with patch("data_odds.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        data_odds.fetch_f5_odds()
        call_url = mock_get.call_args[0][0]
        assert "baseball_mlb_h1" in call_url


def test_fetch_f5_odds_returns_parsed_list():
    import data_odds
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _make_f5_api_response()
    mock_resp.headers = {"x-requests-remaining": "450"}

    with patch("data_odds.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        result = data_odds.fetch_f5_odds()

    assert len(result) == 1
    assert result[0]["consensus"]["total_line"] == 4.5
    assert result[0]["consensus"]["home_ml"] is not None


def test_match_f5_odds_to_game_finds_game():
    import data_odds
    odds_list = [{
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "consensus": {"total_line": 4.5, "home_ml": -130, "away_ml": 110},
        "bookmakers": [],
    }]
    result = data_odds.match_f5_odds_to_game(odds_list, "Boston Red Sox", "New York Yankees")
    assert result.get("consensus", {}).get("total_line") == 4.5


def test_match_f5_odds_to_game_returns_empty_when_no_match():
    import data_odds
    result = data_odds.match_f5_odds_to_game([], "Sox", "Yankees")
    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_f5_picks.py::test_fetch_f5_odds_calls_correct_sport_key -v
```
Expected: `FAILED` — `AttributeError: module 'data_odds' has no attribute 'fetch_f5_odds'`

- [ ] **Step 3: Add `fetch_f5_odds` and `match_f5_odds_to_game` to `data_odds.py`**

Add after `fetch_odds()` (after line 55):

```python
def fetch_f5_odds() -> list:
    """
    Fetch F5 (First 5 Innings) MLB odds from The Odds API.
    Uses sport key 'baseball_mlb_h1'.
    Returns same structure as fetch_odds() but with F5 lines.
    """
    if not ODDS_API_KEY:
        print("[ODDS] No API key set — skipping F5 odds fetch.")
        return []

    url = f"{ODDS_BASE}/sports/baseball_mlb_h1/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,totals",
        "oddsFormat": "american",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"[ODDS] F5: Fetched {len(data)} games. API calls remaining: {remaining}")
        return _parse_odds(data)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print("[ODDS] F5 market not available (baseball_mlb_h1 not found).")
        else:
            print(f"[ODDS] F5 HTTP error: {e}")
        return []
    except Exception as e:
        print(f"[ODDS] F5 error: {e}")
        return []


def match_f5_odds_to_game(f5_odds_list: list, home_team: str, away_team: str) -> dict:
    """
    Find F5 odds entry matching a specific game.
    Same logic as match_odds_to_game but for the F5 list.
    """
    home_lower = home_team.lower()
    away_lower = away_team.lower()
    for entry in f5_odds_list:
        entry_home = entry.get("home_team", "").lower()
        entry_away = entry.get("away_team", "").lower()
        if (_name_match(home_lower, entry_home) and
                _name_match(away_lower, entry_away)):
            return entry
    return {}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_f5_picks.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add data_odds.py tests/test_f5_picks.py
git commit -m "feat: add F5 odds fetch from baseball_mlb_h1 sport key"
```

---

### Task 2: F5 Analysis in `analysis.py`

**Files:**
- Modify: `analysis.py`
- Test: `tests/test_f5_picks.py`

**Background:** F5 picks only make sense when the pitching matchup is strongly one-sided (pitching_score ≥ 0.20 or ≤ -0.20). The F5 ML pick aligns with the pitching agent's direction. F5 totals project ~42% of full-game total (starters typically pitch 5 innings, less bullpen variance). Confidence based on pitching score magnitude.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_f5_picks.py`:

```python
def test_analyze_f5_pick_recommends_home_ml_when_home_pitching_edge():
    from analysis import _analyze_f5_pick

    game = {
        "home_team_name": "Boston Red Sox",
        "away_team_name": "New York Yankees",
        "projected_away_score": 3.5,
        "projected_home_score": 4.2,
    }
    f5_odds = {"consensus": {"home_ml": -130, "away_ml": 110,
                              "total_line": 4.5, "over_price": -115, "under_price": -105}}
    # pitching_score > 0 means home pitching advantage
    result = _analyze_f5_pick(game, f5_odds, pitching_score=0.30)

    assert result["pick"] in ("f5_home", "f5_away")
    assert result["pick"] == "f5_home"
    assert result["confidence"] >= 7


def test_analyze_f5_pick_returns_none_when_weak_pitching_signal():
    from analysis import _analyze_f5_pick

    game = {
        "home_team_name": "Red Sox",
        "away_team_name": "Yankees",
        "projected_away_score": 4.0,
        "projected_home_score": 4.0,
    }
    f5_odds = {"consensus": {"home_ml": -110, "away_ml": -110, "total_line": 4.5}}
    result = _analyze_f5_pick(game, f5_odds, pitching_score=0.10)  # below 0.20 threshold
    assert result is None


def test_analyze_f5_pick_returns_none_when_no_f5_odds():
    from analysis import _analyze_f5_pick
    result = _analyze_f5_pick({}, {}, pitching_score=0.35)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_f5_picks.py::test_analyze_f5_pick_recommends_home_ml_when_home_pitching_edge -v
```
Expected: `FAILED` — `ImportError: cannot import name '_analyze_f5_pick'`

- [ ] **Step 3: Add `_analyze_f5_pick` to `analysis.py`**

Add after `_analyze_over_under` (after line 947):

```python
def _analyze_f5_pick(game: dict, f5_odds: dict, pitching_score: float) -> "dict | None":
    """
    Determine if there's an F5 (First 5 Innings) pick.
    Only fires when pitching_score is strongly one-sided (|score| >= 0.20).
    Returns pick dict or None.

    Pick format:
      {"pick": "f5_home" | "f5_away", "pick_team": str,
       "pick_type": "f5_ml",
       "confidence": int, "edge": str, "ml_odds": int}
    """
    if not f5_odds or not f5_odds.get("consensus"):
        return None

    consensus = f5_odds["consensus"]
    if not consensus.get("home_ml") or not consensus.get("away_ml"):
        return None

    # Only recommend F5 when pitching signal is strong
    if abs(pitching_score) < 0.20:
        return None

    if pitching_score > 0:
        pick = "f5_home"
        pick_team = game.get("home_team_name", "Home")
        ml_odds = consensus.get("home_ml")
        direction = "Home SP advantage"
    else:
        pick = "f5_away"
        pick_team = game.get("away_team_name", "Away")
        ml_odds = consensus.get("away_ml")
        direction = "Away SP advantage"

    # Confidence: based on pitching score magnitude
    score_abs = abs(pitching_score)
    if score_abs >= 0.40:
        conf = 9
    elif score_abs >= 0.30:
        conf = 8
    else:
        conf = 7

    edge = (f"F5 {direction} (pitching score {pitching_score:+.3f}) — "
            f"isolates SP quality, eliminates bullpen variance")

    return {
        "pick": pick,
        "pick_team": pick_team,
        "pick_type": "f5_ml",
        "confidence": conf,
        "ml_odds": ml_odds,
        "edge": edge,
        "f5_total_line": consensus.get("total_line"),
    }
```

- [ ] **Step 4: Wire into `analyze_game()` in `analysis.py`**

In `analyze_game()`, after the `ou_pick` line (after `ou_pick = _analyze_over_under(...)`), add:

```python
    # ── F5 pick (only when strong pitching edge) ──
    f5_odds = game.get("f5_odds", {})
    f5_pick = _analyze_f5_pick(game, f5_odds, pitching["score"])
```

In the `analysis` dict assembly (around line 828), add after `"ou_pick": ou_pick,`:

```python
        "f5_pick": f5_pick,
```

- [ ] **Step 5: Wire F5 pick approval into `risk_filter()` in `analysis.py`**

In `risk_filter()`, after the O/U pick evaluation block (after the `ou_dict` append), add:

```python
        # F5 ML pick evaluation
        f5 = a.get("f5_pick")
        if f5 and f5.get("confidence", 0) >= MIN_CONFIDENCE:
            f5_ml_odds = f5.get("ml_odds")
            ev_f5 = _calculate_ev(f5["confidence"] / 10 * 100, f5_ml_odds)

            if ev_f5 is not None and ev_f5 < MIN_EV:
                print(f"[EV GATE] F5 rejected: {f5['pick_team']} "
                      f"(conf {f5['confidence']}, EV {ev_f5:.4f} at {f5_ml_odds})")
            else:
                f5_dict = {
                    "type": "f5",
                    "game": a["game"],
                    "away_team": a["away_team"],
                    "home_team": a["home_team"],
                    "pick_team": f5["pick_team"],
                    "pick_type": f5["pick_type"],  # "f5_ml"
                    "confidence": f5["confidence"],
                    "win_probability": a["ml_win_probability"],
                    "edge_score": a["ml_edge_score"],
                    "projected_away_score": a["projected_away_score"],
                    "projected_home_score": a["projected_home_score"],
                    "edge_pitching": a["agents"]["pitching"]["edge"],
                    "edge_offense": a["agents"]["offense"]["edge"],
                    "edge_advanced": a["agents"]["advanced"]["edge"],
                    "edge_bullpen": a["agents"]["bullpen"]["edge"],
                    "edge_weather": a["agents"]["weather"]["edge"],
                    "edge_market": f5["edge"],
                    "notes": f"F5 ML | F5 total: {f5.get('f5_total_line', '?')} | {a.get('lineup_status', '')}",
                    "mlb_game_id": a["mlb_game_id"],
                    "game_time_utc": a.get("game_time_utc", ""),
                    "analysis": a,
                    "ml_odds": f5_ml_odds,
                    "ev_score": ev_f5,
                }
                approved.append(f5_dict)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_f5_picks.py -v
```
Expected: `7 passed`

- [ ] **Step 7: Commit**

```bash
git add analysis.py tests/test_f5_picks.py
git commit -m "feat: add F5 pick analysis and risk filter approval"
```

---

### Task 3: DB migration + engine wiring

**Files:**
- Modify: `database.py`
- Modify: `engine.py`
- Test: `tests/test_f5_picks.py`

**Background:** The `picks` table has `pick_type TEXT CHECK(pick_type IN ('moneyline','over','under'))`. SQLite doesn't support ALTER TABLE DROP CONSTRAINT. We handle this by recreating the picks table without the CHECK constraint in the migration block (careful: use `INSERT INTO ... SELECT` to preserve existing data), then the new f5 values will insert cleanly.

**Note:** SQLite does not enforce CHECK constraints added via `ALTER TABLE`, but the constraint in the CREATE TABLE statement blocks f5_ml inserts. Safe approach: in the migration block, check if `f5_ml` can be inserted; if it fails due to CHECK constraint, recreate the table.

Actually, simpler: use a raw `PRAGMA integrity_check` to detect and a table rename/recreate pattern. See implementation below.

- [ ] **Step 1: Write failing test**

Add to `tests/test_f5_picks.py`:

```python
import sqlite3
import database as _db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    _db.init_db()
    return db_path


def test_picks_table_accepts_f5_ml_pick_type(fresh_db):
    """picks table must accept f5_ml, f5_over, f5_under without CHECK constraint error."""
    conn = sqlite3.connect(fresh_db)
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    # Should not raise
    conn.execute("""
        INSERT INTO picks
        (game_id, pick_type, pick_team, confidence, win_probability, edge_score,
         projected_away_score, projected_home_score,
         edge_pitching, edge_offense, edge_advanced, edge_bullpen, edge_weather, edge_market,
         notes, ev_score, ml_odds, ou_odds, created_at, updated_at)
        VALUES (1,'f5_ml','Red Sox',8,62.0,0.15,3.2,4.1,'','','','','','','F5',0.05,-130,NULL,?,?)
    """, (now, now))
    conn.commit()
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_f5_picks.py::test_picks_table_accepts_f5_ml_pick_type -v
```
Expected: `FAILED` — `sqlite3.IntegrityError: CHECK constraint failed`

- [ ] **Step 3: Migrate the picks table in `database.py`**

In `init_db()`, replace the `CREATE TABLE IF NOT EXISTS picks` definition with one that removes the CHECK constraint:

```sql
    CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY,
        game_id INTEGER,
        pick_type TEXT,
        pick_team TEXT,
        confidence INTEGER CHECK(confidence BETWEEN 1 AND 10),
        win_probability REAL,
        edge_score REAL,
        projected_away_score REAL,
        projected_home_score REAL,
        edge_pitching TEXT,
        edge_offense TEXT,
        edge_advanced TEXT,
        edge_bullpen TEXT,
        edge_weather TEXT,
        edge_market TEXT,
        notes TEXT,
        ev_score REAL,
        ml_odds INTEGER,
        ou_odds INTEGER,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','won','lost','push','cancelled')),
        discord_sent INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY (game_id) REFERENCES games(id)
    );
```

For existing databases: the CREATE TABLE IF NOT EXISTS won't change the existing schema. We need to handle existing DBs that have the old CHECK. Add a migration block after the existing migration blocks in `init_db()`:

```python
    # Migrate: remove CHECK constraint from pick_type by recreating picks table
    # Test if f5_ml can be inserted; if not, recreate the table
    try:
        conn.execute("""
            INSERT INTO picks (game_id, pick_type, confidence, win_probability, edge_score,
                               projected_away_score, projected_home_score, created_at, updated_at)
            VALUES (-1, 'f5_ml', 7, 50.0, 0.1, 0, 0, 'test', 'test')
        """)
        conn.rollback()  # don't actually insert
    except sqlite3.IntegrityError:
        # CHECK constraint present — recreate table without it
        conn.execute("ALTER TABLE picks RENAME TO picks_old")
        conn.execute("""
            CREATE TABLE picks (
                id INTEGER PRIMARY KEY,
                game_id INTEGER,
                pick_type TEXT,
                pick_team TEXT,
                confidence INTEGER CHECK(confidence BETWEEN 1 AND 10),
                win_probability REAL,
                edge_score REAL,
                projected_away_score REAL,
                projected_home_score REAL,
                edge_pitching TEXT,
                edge_offense TEXT,
                edge_advanced TEXT,
                edge_bullpen TEXT,
                edge_weather TEXT,
                edge_market TEXT,
                notes TEXT,
                ev_score REAL,
                ml_odds INTEGER,
                ou_odds INTEGER,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending','won','lost','push','cancelled')),
                discord_sent INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (game_id) REFERENCES games(id)
            )
        """)
        conn.execute("INSERT INTO picks SELECT * FROM picks_old")
        conn.execute("DROP TABLE picks_old")
        conn.commit()
        print("[DB] Migrated picks table: removed pick_type CHECK constraint.")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_f5_picks.py::test_picks_table_accepts_f5_ml_pick_type -v
```
Expected: `PASSED`

- [ ] **Step 5: Wire F5 odds into `run_analysis()` in `engine.py`**

Add import at top of `engine.py`:

```python
from data_odds import fetch_odds, match_odds_to_game, match_f5_odds_to_game, fetch_f5_odds, implied_probability
```

In `run_analysis()`, after `odds_list = fetch_odds()` (around line 64), add:

```python
    f5_odds_list = fetch_f5_odds()
```

In the analysis loop (around line 69), before `analysis = analyze_game(g, odds_data)`, add:

```python
        f5_odds = match_f5_odds_to_game(
            f5_odds_list,
            g.get("home_team_name", ""),
            g.get("away_team_name", "")
        )
        g["f5_odds"] = f5_odds
```

Also do the same in `run_refresh()` (after `odds_list = fetch_odds()`):

```python
    f5_odds_list = fetch_f5_odds()
```

And in the refresh analysis loop:

```python
        g["f5_odds"] = match_f5_odds_to_game(
            f5_odds_list, g.get("home_team_name", ""), g.get("away_team_name", "")
        )
```

- [ ] **Step 6: Format F5 picks in `discord_bot.py`**

In `_format_pick_message()`, update the `pick_display` block to handle F5:

```python
    if pick_type == "moneyline":
        pick_display = f"{pick_label} ML"
    elif pick_type == "f5_ml":
        pick_display = f"{pick_label} F5 ML (First 5 Innings)"
    elif pick_type == "over":
        pick_display = f"OVER {pick.get('notes', '').replace('Total line: ', '')}"
    elif pick_type == "under":
        pick_display = f"UNDER {pick.get('notes', '').replace('Total line: ', '')}"
    else:
        pick_display = pick_label
```

- [ ] **Step 7: Commit**

```bash
git add database.py engine.py discord_bot.py tests/test_f5_picks.py
git commit -m "feat: wire F5 picks into engine, DB migration, Discord format"
```

---

### Task 4: F5 Grading in `run_results()`

**Files:**
- Modify: `engine.py`
- Test: `tests/test_f5_picks.py`

**Background:** Grade F5 picks by fetching the linescore from MLB API (`/game/{pk}/linescore`). Sum innings 1-5 for each team. If our F5 ML pick's team scored more in innings 1-5, it's a win.

- [ ] **Step 1: Write failing test**

Add to `tests/test_f5_picks.py`:

```python
def test_grade_f5_pick_from_linescore():
    """_grade_f5_pick returns 'won'/'lost'/'push' from inning scores."""
    from engine import _grade_f5_pick

    linescore = {
        "innings": [
            {"num": 1, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 2, "away": {"runs": 2}, "home": {"runs": 0}},
            {"num": 3, "away": {"runs": 0}, "home": {"runs": 0}},
            {"num": 4, "away": {"runs": 1}, "home": {"runs": 0}},
            {"num": 5, "away": {"runs": 0}, "home": {"runs": 2}},
            {"num": 6, "away": {"runs": 3}, "home": {"runs": 0}},  # innings 6+ ignored
        ]
    }
    # F5: away=3 runs (inn 2+4), home=3 runs (inn 1+5) — push
    assert _grade_f5_pick("f5_away", linescore) == "push"

    # Modify: home scores more in F5
    linescore["innings"][4]["home"]["runs"] = 3  # home gets 4 total in F5
    assert _grade_f5_pick("f5_home", linescore) == "won"
    assert _grade_f5_pick("f5_away", linescore) == "lost"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_f5_picks.py::test_grade_f5_pick_from_linescore -v
```
Expected: `FAILED` — `ImportError: cannot import name '_grade_f5_pick' from 'engine'`

- [ ] **Step 3: Add `_grade_f5_pick` and `_fetch_f5_linescore` to `engine.py`**

Add before `run_results()`:

```python
def _fetch_f5_linescore(mlb_game_id: int) -> dict:
    """Fetch linescore for a completed game. Returns {} on error."""
    try:
        url = f"https://statsapi.mlb.com/api/v1/game/{mlb_game_id}/linescore"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching linescore for game {mlb_game_id}: {e}")
        return {}


def _grade_f5_pick(pick_type: str, linescore: dict) -> str:
    """
    Grade an F5 ML pick.
    pick_type: 'f5_home' or 'f5_away'
    Returns 'won', 'lost', or 'push'.
    """
    innings = linescore.get("innings", [])
    away_f5 = sum(
        (inn.get("away", {}).get("runs") or 0)
        for inn in innings if 1 <= inn.get("num", 0) <= 5
    )
    home_f5 = sum(
        (inn.get("home", {}).get("runs") or 0)
        for inn in innings if 1 <= inn.get("num", 0) <= 5
    )

    if away_f5 == home_f5:
        return "push"
    if pick_type == "f5_home":
        return "won" if home_f5 > away_f5 else "lost"
    elif pick_type == "f5_away":
        return "won" if away_f5 > home_f5 else "lost"
    return "push"
```

- [ ] **Step 4: Wire F5 grading into `run_results()`**

In `run_results()`, in the pick grading section, extend the `elif pick["pick_type"] == "under":` block. Add after the `else: status = "push"` line (around line 492):

```python
        elif pick["pick_type"] == "f5_ml":
            # Fetch linescore to get F5 (innings 1-5) scores
            linescore = _fetch_f5_linescore(mlb_game_id)
            if linescore:
                # Determine pick direction from pick_team vs home/away team name
                home_name = result.get("home_team_name", "")
                if pick.get("pick_team") == home_name:
                    f5_direction = "f5_home"
                else:
                    f5_direction = "f5_away"
                status = _grade_f5_pick(f5_direction, linescore)
            else:
                status = "push"  # can't grade without linescore
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_f5_picks.py -v
```
Expected: all tests pass (one pre-existing failure in test_analysis_log.py unrelated to this plan)

- [ ] **Step 6: Run full test suite to verify no regressions**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
git add engine.py tests/test_f5_picks.py
git commit -m "feat: F5 pick grading using innings 1-5 linescore (Plan B complete)"
```
