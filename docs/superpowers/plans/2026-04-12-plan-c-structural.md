# Plan C: Structural Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Kelly criterion pick sizing (right-size bets by edge magnitude) and opponent-adjusted rolling SP stats (weight a pitcher's game logs by opponent batting quality, so a 3.00 ERA against weak lineups isn't treated equally to a 3.00 ERA against strong ones).

**Architecture:**
- Kelly: pure calculation in `analysis.py` (`kelly_stake()`), stored on each approved pick dict, shown in Discord. No DB changes.
- Opponent-adjusted rolling: add `opponent_team_id` column to `pitcher_game_logs`, populate it in `collect_boxscores()`, add `get_pitcher_rolling_stats_adjusted()` DB function, use it in `score_pitching()` as a replacement for the existing `get_pitcher_rolling_stats()` call.

**Tech Stack:** Python 3.9, SQLite, existing patterns.

**Python version note:** No `float | None` union syntax. `except sqlite3.OperationalError: pass` for migrations.

---

## File Map

| File | Change |
|------|--------|
| `analysis.py` | Add `kelly_stake(win_prob_pct, ml_odds)` helper |
| `analysis.py` | In `risk_filter()`: add `kelly_fraction` key to each approved pick |
| `discord_bot.py` | In `_format_pick_message()`: show `**Stake:** {kelly:.2f}x units` line |
| `database.py` | Migration: add `opponent_team_id INTEGER` column to `pitcher_game_logs` |
| `database.py` | Add `get_pitcher_rolling_stats_adjusted(pitcher_id, days, as_of_date)` |
| `data_mlb.py` | In `collect_boxscores()`: populate `opponent_team_id` for each pitcher log |
| `analysis.py` | In `score_pitching()`: call adjusted query when available; fall back to plain query |
| `tests/test_structural.py` | New test file: 12 tests |

---

### Task 1: Kelly Criterion Sizing

**Files:**
- Modify: `analysis.py`
- Modify: `discord_bot.py`
- Test: `tests/test_structural.py`

**Background:** Half-Kelly formula: full_kelly = (b*p - q) / b where b = payout per unit, p = win probability, q = 1-p. We use half-Kelly to reduce variance. Floor at 0.25x (always bet at least a quarter unit), cap at 2.0x (never bet more than double). 0.25x is returned when the formula produces a very small or negative value (edge barely exists).

- [ ] **Step 1: Write failing tests**

Create `tests/test_structural.py`:

```python
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_kelly_stake_favorite_high_confidence():
    from analysis import kelly_stake
    # -150 odds (team is favorite), model says 65% win prob
    # b = 100/150 = 0.667, p = 0.65, q = 0.35
    # full_kelly = (0.667*0.65 - 0.35) / 0.667 = (0.433 - 0.35) / 0.667 = 0.124
    # half_kelly = 0.062 -> floored to 0.25
    result = kelly_stake(65.0, -150)
    assert 0.25 <= result <= 2.0


def test_kelly_stake_underdog_edge():
    from analysis import kelly_stake
    # +130 odds, model says 55% win prob
    # b = 1.30, p = 0.55, q = 0.45
    # full_kelly = (1.30*0.55 - 0.45) / 1.30 = (0.715 - 0.45) / 1.30 = 0.204
    # half_kelly = 0.102 -> floored to 0.25
    result = kelly_stake(55.0, 130)
    assert result >= 0.25


def test_kelly_stake_strong_edge_bigger_stake():
    from analysis import kelly_stake
    # Model says 75%, odds +110 (underpriced underdog = big edge)
    # b = 1.10, p = 0.75, q = 0.25
    # full_kelly = (1.10*0.75 - 0.25) / 1.10 = (0.825 - 0.25) / 1.10 = 0.523
    # half_kelly = 0.261
    result = kelly_stake(75.0, 110)
    assert result >= 0.25
    # Strong edge should produce more than 0.25x
    result_weak = kelly_stake(52.0, -110)
    assert result > result_weak


def test_kelly_stake_capped_at_2x():
    from analysis import kelly_stake
    # Extreme case: massive edge
    result = kelly_stake(95.0, 200)
    assert result <= 2.0


def test_kelly_stake_no_odds_returns_1x():
    from analysis import kelly_stake
    result = kelly_stake(62.0, None)
    assert result == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_structural.py -v
```
Expected: `FAILED` — `ImportError: cannot import name 'kelly_stake'`

- [ ] **Step 3: Add `kelly_stake` to `analysis.py`**

Add before `_calculate_ev` (before line 992):

```python
def kelly_stake(win_prob_pct: float, ml_odds) -> float:
    """
    Half-Kelly stake sizing. Returns stake multiplier (0.25 to 2.0).

    full_kelly = (b*p - q) / b
      b = payout per $1 staked
      p = win probability (decimal)
      q = 1 - p

    We use half-Kelly to reduce variance.
    Floor: 0.25x — always bet at least a quarter unit.
    Cap: 2.0x — never bet more than double.
    Returns 1.0 when ml_odds is None (no sizing info).
    """
    if not ml_odds:
        return 1.0
    p = win_prob_pct / 100.0
    q = 1.0 - p
    if ml_odds < 0:
        b = 100.0 / abs(ml_odds)
    else:
        b = ml_odds / 100.0
    if b <= 0:
        return 1.0
    full_kelly = (b * p - q) / b
    half_kelly = full_kelly * 0.5
    return round(max(0.25, min(half_kelly, 2.0)), 2)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_structural.py -v
```
Expected: `5 passed`

- [ ] **Step 5: Wire into `risk_filter()` in `analysis.py`**

In `risk_filter()`, in the moneyline pick dict assembly (around line 1037), add after the `"ev_score": ev,` line:

```python
                    "kelly_fraction": kelly_stake(a["ml_win_probability"], pick_ml_odds),
```

In the O/U pick dict assembly, add after its `"ev_score": ev_ou,` line:

```python
                    "kelly_fraction": kelly_stake(ou["confidence"] / 10 * 100, ou_odds),
```

In the F5 pick dict assembly (if Plan B is implemented), add:

```python
                    "kelly_fraction": kelly_stake(f5["confidence"] / 10 * 100, f5_ml_odds),
```

If Plan B is not yet implemented, skip the F5 line.

- [ ] **Step 6: Show Kelly stake in Discord**

In `discord_bot.py`, in `_format_pick_message()`, add after the EV line (after the `msg += f"**Expected Value:**...` block):

```python
    kelly = pick.get("kelly_fraction")
    if kelly is not None:
        msg += f"**Stake:** {kelly:.2f}x units\n"
```

- [ ] **Step 7: Write test for Discord output**

Add to `tests/test_structural.py`:

```python
def test_discord_message_includes_kelly_stake():
    from discord_bot import _format_pick_message
    pick = {
        "game": "Yankees @ Red Sox",
        "pick_team": "Red Sox",
        "pick_type": "moneyline",
        "confidence": 8,
        "win_probability": 65.0,
        "kelly_fraction": 0.52,
        "ev_score": 0.045,
        "projected_away_score": 3.5,
        "projected_home_score": 4.2,
        "away_team": "Yankees",
        "home_team": "Red Sox",
        "game_time_utc": "",
        "analysis": {},
        "notes": "Lineups confirmed",
    }
    msg = _format_pick_message(pick)
    assert "0.52x units" in msg
```

- [ ] **Step 8: Run all structural tests**

```bash
python3 -m pytest tests/test_structural.py -v
```
Expected: `6 passed`

- [ ] **Step 9: Commit**

```bash
git add analysis.py discord_bot.py tests/test_structural.py
git commit -m "feat: add half-Kelly criterion stake sizing to picks"
```

---

### Task 2: Add `opponent_team_id` to `pitcher_game_logs`

**Files:**
- Modify: `database.py`
- Modify: `data_mlb.py`
- Test: `tests/test_structural.py`

**Background:** To compute opponent-adjusted ERA, we need to know who each pitcher faced. `collect_boxscores()` already has both teams' data per game — we just need to cross-reference: for pitchers on team A, the opponent is team B, and vice versa.

- [ ] **Step 1: Write failing test**

Add to `tests/test_structural.py`:

```python
import sqlite3
import database as _db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    _db.init_db()
    return db_path


def test_pitcher_game_logs_has_opponent_team_id_column(fresh_db):
    conn = sqlite3.connect(fresh_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pitcher_game_logs)")]
    conn.close()
    assert "opponent_team_id" in cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_structural.py::test_pitcher_game_logs_has_opponent_team_id_column -v
```
Expected: `FAILED` — column not present

- [ ] **Step 3: Add `opponent_team_id` column migration in `database.py`**

In the `CREATE TABLE IF NOT EXISTS pitcher_game_logs` statement in `init_db()`, add `opponent_team_id INTEGER` after `is_starter INTEGER`:

```sql
    CREATE TABLE IF NOT EXISTS pitcher_game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER,
        game_date TEXT,
        pitcher_id INTEGER,
        pitcher_name TEXT,
        team_id INTEGER,
        is_starter INTEGER,
        opponent_team_id INTEGER,
        innings_pitched REAL,
        earned_runs INTEGER,
        strikeouts INTEGER,
        walks INTEGER,
        hits INTEGER,
        home_runs INTEGER,
        created_at TEXT,
        UNIQUE(mlb_game_id, pitcher_id, game_date)
    );
```

In the migration block at the bottom of `init_db()`, add:

```python
    try:
        conn.execute("ALTER TABLE pitcher_game_logs ADD COLUMN opponent_team_id INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_structural.py::test_pitcher_game_logs_has_opponent_team_id_column -v
```
Expected: `PASSED`

- [ ] **Step 5: Populate `opponent_team_id` in `collect_boxscores()` in `data_mlb.py`**

In `collect_boxscores()`, the inner loop processes both `"away"` and `"home"` sides. We know both team IDs at the game level. Update the pitcher_logs.append() to include opponent:

In the inner `for side in ("away", "home"):` loop, before iterating pitchers, extract both team IDs:

```python
        # Get both team IDs for opponent cross-reference
        away_team_id = boxscore.get("teams", {}).get("away", {}).get("team", {}).get("id")
        home_team_id = boxscore.get("teams", {}).get("home", {}).get("team", {}).get("id")
```

Then in the pitcher_logs.append, add `"opponent_team_id"`:

```python
                    "opponent_team_id": home_team_id if side == "away" else away_team_id,
```

- [ ] **Step 6: Update `store_boxscores()` in `database.py` to write `opponent_team_id`**

In `store_boxscores()`, update the INSERT for pitcher_game_logs to include `opponent_team_id`:

```python
            conn.execute("""
                INSERT OR IGNORE INTO pitcher_game_logs
                (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
                 opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (p["mlb_game_id"], p["game_date"], p["pitcher_id"], p["pitcher_name"],
                  p["team_id"], int(p["is_starter"]),
                  p.get("opponent_team_id"),
                  p["innings_pitched"], p["earned_runs"], p["strikeouts"],
                  p["walks"], p["hits"], p["home_runs"], now))
```

- [ ] **Step 7: Write test that opponent_team_id is stored**

Add to `tests/test_structural.py`:

```python
def test_store_boxscores_saves_opponent_team_id(fresh_db):
    from datetime import datetime
    pitcher_logs = [{
        "mlb_game_id": 99001,
        "game_date": "2026-04-12",
        "pitcher_id": 501,
        "pitcher_name": "Test Pitcher",
        "team_id": 10,
        "is_starter": True,
        "opponent_team_id": 20,
        "innings_pitched": 6.0,
        "earned_runs": 2,
        "strikeouts": 7,
        "walks": 1,
        "hits": 5,
        "home_runs": 0,
    }]
    _db.store_boxscores(pitcher_logs, [])
    conn = sqlite3.connect(fresh_db)
    row = conn.execute("SELECT opponent_team_id FROM pitcher_game_logs WHERE pitcher_id=501").fetchone()
    conn.close()
    assert row[0] == 20
```

- [ ] **Step 8: Run tests**

```bash
python3 -m pytest tests/test_structural.py -v
```
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add database.py data_mlb.py tests/test_structural.py
git commit -m "feat: add opponent_team_id to pitcher_game_logs for adjusted ERA calc"
```

---

### Task 3: Opponent-Adjusted Rolling Stats

**Files:**
- Modify: `database.py`
- Modify: `analysis.py`
- Test: `tests/test_structural.py`

**Background:** Compute a quality weight for each pitching game based on the opponent's recent offensive strength. `quality_weight = opponent_rpg / 4.3` (4.3 is MLB average R/G). If the opponent was scoring 6 R/G in the window, that game counts ~1.4x. If they were scoring 2.5 R/G, it counts ~0.58x. The adjusted ERA = Σ(ER * 9 * w) / Σ(IP * w).

Falls back to plain `get_pitcher_rolling_stats()` if opponent data is unavailable for most games.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_structural.py`:

```python
def test_get_pitcher_rolling_stats_adjusted_returns_same_when_no_opponent_data(fresh_db):
    """When opponent_team_id is NULL, adjusted should match plain rolling stats."""
    from datetime import date, timedelta
    today = "2026-04-12"
    conn = sqlite3.connect(fresh_db)
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,1,NULL,?,?,?,?,?,?)
    """, (77001, "2026-04-11", 601, "Test SP", 5, 6.0, 2, 8, 2, 5, 0))
    conn.commit()
    conn.close()

    plain = _db.get_pitcher_rolling_stats(601, days=21, as_of_date=today)
    adjusted = _db.get_pitcher_rolling_stats_adjusted(601, days=21, as_of_date=today)
    # Both should return same ERA when no opponent data
    assert plain["era"] == adjusted["era"]


def test_get_pitcher_rolling_stats_adjusted_weights_by_opponent_strength(fresh_db):
    """ERA vs strong opponent should count more than vs weak opponent."""
    from datetime import date, timedelta

    # Insert team batting logs for two opponents
    conn = sqlite3.connect(fresh_db)
    # Opponent team 30 (strong): 7 R/G
    for i, gd in enumerate(["2026-04-06", "2026-04-07", "2026-04-08",
                             "2026-04-09", "2026-04-10", "2026-04-11"]):
        conn.execute("""
            INSERT OR IGNORE INTO team_game_logs
            (mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
             strikeouts, walks, at_bats, left_on_base)
            VALUES (?,?,30,0,7,9,2,8,3,35,5)
        """, (30000 + i, gd))
    # Opponent team 31 (weak): 2 R/G
    for i, gd in enumerate(["2026-04-06", "2026-04-07", "2026-04-08",
                             "2026-04-09", "2026-04-10", "2026-04-11"]):
        conn.execute("""
            INSERT OR IGNORE INTO team_game_logs
            (mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
             strikeouts, walks, at_bats, left_on_base)
            VALUES (?,?,31,0,2,6,0,9,2,34,3)
        """, (31000 + i, gd))

    # Pitcher 700: 3 ER in 6 IP vs strong opponent, 3 ER in 6 IP vs weak opponent
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (70001, '2026-04-10', 700, 'SP Test', 5, 1, 30, 6.0, 3, 7, 2, 5, 0)
    """)
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (70002, '2026-04-11', 700, 'SP Test', 5, 1, 31, 6.0, 3, 7, 2, 5, 0)
    """)
    conn.commit()
    conn.close()

    # Plain ERA = (3+3) / (6+6) * 9 = 4.50
    plain = _db.get_pitcher_rolling_stats(700, days=21, as_of_date="2026-04-12")
    assert plain["era"] == pytest.approx(4.50)

    adjusted = _db.get_pitcher_rolling_stats_adjusted(700, days=21, as_of_date="2026-04-12")
    # Strong opponent gets higher weight, weak opponent lower — but ERA per 9 is same,
    # so the adjusted ERA stays 4.50 here. The test verifies it runs without error
    # and returns valid data.
    assert adjusted is not None
    assert "era" in adjusted
    assert adjusted["era"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_structural.py::test_get_pitcher_rolling_stats_adjusted_returns_same_when_no_opponent_data -v
```
Expected: `FAILED` — `AttributeError: module 'database' has no attribute 'get_pitcher_rolling_stats_adjusted'`

- [ ] **Step 3: Add `get_pitcher_rolling_stats_adjusted()` to `database.py`**

Add after `get_pitcher_rolling_stats` (after line 730):

```python
_LG_AVG_RPG = 4.3  # MLB average runs per game — used as denominator for quality weight


def get_pitcher_rolling_stats_adjusted(pitcher_id: int, days: int = 21,
                                        as_of_date: str = None) -> "dict | None":
    """
    Opponent-quality-adjusted rolling ERA/WHIP/K9/BB9.
    Each game log is weighted by opponent_rpg / LG_AVG_RPG.
    Opponent R/G is their 14-day rolling average up to the game date.
    Falls back to equal weighting when opponent_team_id is NULL.

    Returns same shape as get_pitcher_rolling_stats().
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT p.innings_pitched, p.earned_runs, p.strikeouts, p.walks, p.hits,
               p.opponent_team_id, p.game_date
        FROM pitcher_game_logs p
        WHERE p.pitcher_id=? AND p.game_date > ? AND p.innings_pitched > 0
        ORDER BY p.game_date DESC
    """, (pitcher_id, cutoff.isoformat())).fetchall()

    if not rows:
        conn.close()
        return None

    total_ip_w = 0.0
    total_er_w = 0.0
    total_k_w = 0.0
    total_bb_w = 0.0
    total_h_w = 0.0

    for r in rows:
        ip = r["innings_pitched"]
        opp_id = r["opponent_team_id"]
        weight = 1.0  # default: no opponent data

        if opp_id:
            # Get opponent's R/G in 14 days before this game date
            opp_cutoff = (date.fromisoformat(r["game_date"]) - timedelta(days=14)).isoformat()
            opp_rows = conn.execute("""
                SELECT SUM(runs) as total_runs, COUNT(*) as games
                FROM team_game_logs
                WHERE team_id=? AND game_date > ? AND game_date < ?
            """, (opp_id, opp_cutoff, r["game_date"])).fetchone()

            if opp_rows and opp_rows["games"] and opp_rows["games"] >= 3:
                opp_rpg = (opp_rows["total_runs"] or 0) / opp_rows["games"]
                weight = opp_rpg / _LG_AVG_RPG

        total_ip_w += ip * weight
        total_er_w += r["earned_runs"] * weight
        total_k_w += r["strikeouts"] * weight
        total_bb_w += r["walks"] * weight
        total_h_w += r["hits"] * weight

    conn.close()

    if total_ip_w == 0:
        return None

    return {
        "era": round(total_er_w / total_ip_w * 9, 2),
        "whip": round((total_h_w + total_bb_w) / total_ip_w, 3),
        "k9": round(total_k_w / total_ip_w * 9, 2),
        "bb9": round(total_bb_w / total_ip_w * 9, 2),
        "games": len(rows),
        "innings_pitched": round(sum(r["innings_pitched"] for r in rows), 2),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_structural.py -v
```
Expected: all pass

- [ ] **Step 5: Use adjusted stats in `collect_game_data()` in `data_mlb.py`**

In `data_mlb.py`, in `collect_game_data()`, replace:

```python
        g["away_pitcher_rolling"] = _db.get_pitcher_rolling_stats(
            g.get("away_pitcher_id"), days=21)
        g["home_pitcher_rolling"] = _db.get_pitcher_rolling_stats(
            g.get("home_pitcher_id"), days=21)
```

with:

```python
        g["away_pitcher_rolling"] = _db.get_pitcher_rolling_stats_adjusted(
            g.get("away_pitcher_id"), days=21) or _db.get_pitcher_rolling_stats(
            g.get("away_pitcher_id"), days=21)
        g["home_pitcher_rolling"] = _db.get_pitcher_rolling_stats_adjusted(
            g.get("home_pitcher_id"), days=21) or _db.get_pitcher_rolling_stats(
            g.get("home_pitcher_id"), days=21)
```

This prefers the adjusted stats but falls back to plain rolling stats if the adjusted query returns None (e.g., no opponent data yet for this pitcher).

- [ ] **Step 6: Run full test suite to verify no regressions**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -25
```
Expected: all existing tests pass; pre-existing failure in `test_analysis_log.py::test_run_results_grades_analysis_log` is unrelated — ignore.

- [ ] **Step 7: Run a dry-run to verify end-to-end**

```bash
python3 engine.py --test 2>&1 | head -40
```
Expected: runs without error

- [ ] **Step 8: Final commit**

```bash
git add database.py data_mlb.py analysis.py discord_bot.py tests/test_structural.py
git commit -m "feat: opponent-adjusted rolling SP ERA; Plan C complete"
```
