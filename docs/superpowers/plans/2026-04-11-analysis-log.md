# Analysis Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log all 15 daily game analyses to a new `analysis_log` table so we can measure model accuracy independently from the pick filter (confidence/edge threshold).

**Architecture:** Add `analysis_log` table to SQLite. After `analyze_game()` runs for all games in `run_analysis()`, save every game's ML prediction and O/U prediction. When `--results` runs at 11pm, grade `analysis_log` entries alongside `picks`. Extend `--status` to show both pick accuracy and model accuracy side by side.

**Tech Stack:** Python 3.9, SQLite (via existing `database.py` patterns), no new dependencies.

---

## File Map

| File | Change |
|------|--------|
| `database.py` | Add `analysis_log` table + `save_analysis_log()` + `get_today_analysis_log()` + `update_analysis_log_status()` + extend `get_roi_summary()` |
| `engine.py` | Save all analyses after step 4; grade analysis_log in `run_results()`; extend `_print_snapshot()` |
| `tests/test_analysis_log.py` | New test file |

---

### Task 1: Add `analysis_log` table to the schema

**Files:**
- Modify: `database.py:167-183` (after `daily_results` table, before indexes)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_log.py
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db

def test_analysis_log_table_exists(tmp_db):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_log'"
    ).fetchone()
    conn.close()
    assert row is not None, "analysis_log table should exist after init_db()"
```

Add the `tmp_db` fixture at the top of the file:

```python
import pytest

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DATABASE_PATH", db_path)
    db.init_db()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_analysis_log.py::test_analysis_log_table_exists -v
```

Expected: FAIL — `analysis_log table should exist after init_db()`

- [ ] **Step 3: Add table to `database.py`**

In `database.py`, add after the `daily_results` table (around line 178, before the `CREATE INDEX` lines):

```python
    CREATE TABLE IF NOT EXISTS analysis_log (
        id INTEGER PRIMARY KEY,
        game_date TEXT,
        mlb_game_id INTEGER,
        game TEXT,
        away_team TEXT,
        home_team TEXT,
        away_pitcher TEXT,
        home_pitcher TEXT,
        composite_score REAL,
        ml_pick_team TEXT,
        ml_win_probability REAL,
        ml_confidence INTEGER,
        ml_status TEXT DEFAULT 'pending' CHECK(ml_status IN ('pending','correct','incorrect','push')),
        ou_pick TEXT,
        ou_line REAL,
        ou_confidence INTEGER,
        ou_status TEXT DEFAULT 'pending' CHECK(ou_status IN ('pending','correct','incorrect','push','none')),
        actual_away_score INTEGER,
        actual_home_score INTEGER,
        actual_total INTEGER,
        created_at TEXT,
        updated_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_analysis_log_date ON analysis_log(game_date);
    CREATE INDEX IF NOT EXISTS idx_analysis_log_game ON analysis_log(mlb_game_id);
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_analysis_log.py::test_analysis_log_table_exists -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_analysis_log.py
git commit -m "feat: add analysis_log table to schema"
```

---

### Task 2: Add `save_analysis_log()` to database.py

**Files:**
- Modify: `database.py` (after `save_pick()`)
- Test: `tests/test_analysis_log.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_analysis_log.py`:

```python
def test_save_analysis_log(tmp_db):
    # First upsert a game so mlb_game_id exists
    entry = {
        "game_date": "2026-04-11",
        "mlb_game_id": 999001,
        "game": "Away Team @ Home Team",
        "away_team": "Away Team",
        "home_team": "Home Team",
        "away_pitcher": "Pitcher A",
        "home_pitcher": "Pitcher B",
        "composite_score": 0.123,
        "ml_pick_team": "Home Team",
        "ml_win_probability": 58.5,
        "ml_confidence": 6,
        "ou_pick": "under",
        "ou_line": 8.5,
        "ou_confidence": 7,
    }
    row_id = db.save_analysis_log(entry)
    assert row_id > 0

    conn = db.get_connection()
    row = conn.execute("SELECT * FROM analysis_log WHERE id=?", (row_id,)).fetchone()
    conn.close()
    assert row["mlb_game_id"] == 999001
    assert row["ml_pick_team"] == "Home Team"
    assert row["ou_pick"] == "under"
    assert row["ml_status"] == "pending"
    assert row["ou_status"] == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_analysis_log.py::test_save_analysis_log -v
```

Expected: FAIL — `AttributeError: module 'database' has no attribute 'save_analysis_log'`

- [ ] **Step 3: Implement `save_analysis_log()` in `database.py`**

Add after `mark_pick_sent()` (around line 250):

```python
def save_analysis_log(entry: dict) -> int:
    """Log one game's full analysis. Called for every game each daily run."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    ou_status = "none" if not entry.get("ou_pick") else "pending"
    c = conn.execute("""
        INSERT INTO analysis_log
        (game_date, mlb_game_id, game, away_team, home_team,
         away_pitcher, home_pitcher, composite_score,
         ml_pick_team, ml_win_probability, ml_confidence,
         ou_pick, ou_line, ou_confidence,
         ml_status, ou_status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        entry["game_date"], entry["mlb_game_id"], entry["game"],
        entry["away_team"], entry["home_team"],
        entry.get("away_pitcher", "TBD"), entry.get("home_pitcher", "TBD"),
        entry.get("composite_score", 0.0),
        entry["ml_pick_team"], entry["ml_win_probability"], entry["ml_confidence"],
        entry.get("ou_pick"), entry.get("ou_line"), entry.get("ou_confidence"),
        "pending", ou_status, now, now
    ))
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return row_id
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_analysis_log.py::test_save_analysis_log -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_analysis_log.py
git commit -m "feat: add save_analysis_log() to database"
```

---

### Task 3: Add `get_today_analysis_log()` and `update_analysis_log_result()` to database.py

**Files:**
- Modify: `database.py`
- Test: `tests/test_analysis_log.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_analysis_log.py`:

```python
def test_get_today_analysis_log(tmp_db):
    entry = {
        "game_date": "2026-04-11",
        "mlb_game_id": 999002,
        "game": "A @ B",
        "away_team": "A", "home_team": "B",
        "away_pitcher": "P1", "home_pitcher": "P2",
        "composite_score": 0.05,
        "ml_pick_team": "B",
        "ml_win_probability": 54.0,
        "ml_confidence": 4,
        "ou_pick": None, "ou_line": None, "ou_confidence": None,
    }
    db.save_analysis_log(entry)
    rows = db.get_today_analysis_log()
    assert len(rows) == 1
    assert rows[0]["mlb_game_id"] == 999002


def test_update_analysis_log_result(tmp_db):
    entry = {
        "game_date": "2026-04-11",
        "mlb_game_id": 999003,
        "game": "C @ D",
        "away_team": "C", "home_team": "D",
        "away_pitcher": "P3", "home_pitcher": "P4",
        "composite_score": 0.20,
        "ml_pick_team": "D",
        "ml_win_probability": 61.0,
        "ml_confidence": 7,
        "ou_pick": "over", "ou_line": 8.0, "ou_confidence": 8,
    }
    row_id = db.save_analysis_log(entry)
    db.update_analysis_log_result(row_id, ml_status="correct", ou_status="incorrect",
                                   actual_away=3, actual_home=5, actual_total=8)
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM analysis_log WHERE id=?", (row_id,)).fetchone()
    conn.close()
    assert row["ml_status"] == "correct"
    assert row["ou_status"] == "incorrect"
    assert row["actual_away_score"] == 3
    assert row["actual_home_score"] == 5
    assert row["actual_total"] == 8
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_analysis_log.py::test_get_today_analysis_log tests/test_analysis_log.py::test_update_analysis_log_result -v
```

Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement both functions in `database.py`**

Add after `save_analysis_log()`:

```python
def get_today_analysis_log() -> list:
    """Return all analysis_log entries for today."""
    conn = get_connection()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM analysis_log WHERE game_date=? ORDER BY ml_confidence DESC",
        (today,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_analysis_log_result(log_id: int, ml_status: str, ou_status: str,
                                actual_away: int, actual_home: int, actual_total: int):
    """Grade an analysis_log entry with final score."""
    conn = get_connection()
    conn.execute("""
        UPDATE analysis_log
        SET ml_status=?, ou_status=?,
            actual_away_score=?, actual_home_score=?, actual_total=?,
            updated_at=?
        WHERE id=?
    """, (ml_status, ou_status, actual_away, actual_home, actual_total,
          datetime.utcnow().isoformat(), log_id))
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_analysis_log.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_analysis_log.py
git commit -m "feat: add get/update functions for analysis_log"
```

---

### Task 4: Save all analyses to `analysis_log` in `engine.py`

**Files:**
- Modify: `engine.py` — `run_analysis()`, after the analysis loop (step 4), before saving picks

- [ ] **Step 1: Write the failing test**

Add to `tests/test_analysis_log.py`:

```python
def test_analysis_log_saved_for_all_games(tmp_db, monkeypatch):
    """run_analysis() in dry_run mode should still save analysis_log entries."""
    import engine

    fake_games = [
        {"mlb_game_id": 1, "away_team_name": "A", "home_team_name": "B",
         "game_time_utc": "", "away_pitcher_name": "P1", "home_pitcher_name": "P2",
         "home_team_abbr": "NYY", "away_team_abbr": "BOS",
         "home_lineup_confirmed": False, "away_lineup_confirmed": False},
        {"mlb_game_id": 2, "away_team_name": "C", "home_team_name": "D",
         "game_time_utc": "", "away_pitcher_name": "P3", "home_pitcher_name": "P4",
         "home_team_abbr": "LAD", "away_team_abbr": "SF",
         "home_lineup_confirmed": False, "away_lineup_confirmed": False},
    ]

    fake_analysis = {
        "game": "A @ B", "away_team": "A", "home_team": "B",
        "away_pitcher": "P1", "home_pitcher": "P2",
        "mlb_game_id": 1, "game_time_utc": "",
        "composite_score": 0.1, "ml_pick_team": "B",
        "ml_win_probability": 55.0, "ml_confidence": 5,
        "ml_pick_side": "home", "ml_edge_score": 0.10,
        "projected_away_score": 3.5, "projected_home_score": 4.2,
        "lineup_status": "", "lineups_confirmed": False,
        "ou_pick": {"pick": "under", "line": 8.5, "confidence": 6, "edge": ""},
        "agents": {k: {"score": 0, "edge": ""} for k in
                   ["pitching","offense","bullpen","advanced","momentum","weather","market"]},
    }

    monkeypatch.setattr(engine, "collect_game_data", lambda: fake_games)
    monkeypatch.setattr(engine, "fetch_odds", lambda: [])
    monkeypatch.setattr(engine, "match_odds_to_game", lambda *a: {})
    monkeypatch.setattr(engine, "analyze_game", lambda *a: {**fake_analysis, "mlb_game_id": fake_games[0]["mlb_game_id"]})
    monkeypatch.setattr(engine, "fetch_all_teams", lambda: None)

    engine.run_analysis(dry_run=True)

    rows = db.get_today_analysis_log()
    assert len(rows) >= 1, "analysis_log should have entries after run_analysis"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_analysis_log.py::test_analysis_log_saved_for_all_games -v
```

Expected: FAIL — 0 rows in analysis_log

- [ ] **Step 3: Add analysis logging to `engine.py`**

In `run_analysis()`, after the analysis loop (after `approved = risk_filter(analyses)`, around line 80), add before the `# ── Print Analysis Board ──` block:

```python
    # ── Log all game analyses to DB (skip in dry_run) ──
    if not dry_run:
        today = date.today().isoformat()
        for a in analyses:
            ou = a.get("ou_pick") or {}
            db.save_analysis_log({
                "game_date": today,
                "mlb_game_id": a["mlb_game_id"],
                "game": a["game"],
                "away_team": a["away_team"],
                "home_team": a["home_team"],
                "away_pitcher": a.get("away_pitcher", "TBD"),
                "home_pitcher": a.get("home_pitcher", "TBD"),
                "composite_score": a["composite_score"],
                "ml_pick_team": a["ml_pick_team"],
                "ml_win_probability": a["ml_win_probability"],
                "ml_confidence": a["ml_confidence"],
                "ou_pick": ou.get("pick"),
                "ou_line": ou.get("line"),
                "ou_confidence": ou.get("confidence"),
            })
        print(f"[DB] Logged {len(analyses)} game analyses to analysis_log.")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_analysis_log.py::test_analysis_log_saved_for_all_games -v
```

Expected: PASS

- [ ] **Step 5: Run the full engine in dry_run and confirm logging**

```bash
python3 engine.py 2>&1 | grep "analysis_log"
```

Expected: `[DB] Logged 15 game analyses to analysis_log.`

- [ ] **Step 6: Commit**

```bash
git add engine.py tests/test_analysis_log.py
git commit -m "feat: log all game analyses to analysis_log on each run"
```

---

### Task 5: Grade `analysis_log` entries in `run_results()`

**Files:**
- Modify: `engine.py` — `run_results()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_analysis_log.py`:

```python
def test_run_results_grades_analysis_log(tmp_db, monkeypatch):
    import engine
    from datetime import date as dt

    # Insert a fake analysis_log entry
    log_id = db.save_analysis_log({
        "game_date": dt.today().isoformat(),
        "mlb_game_id": 77001,
        "game": "Away @ Home",
        "away_team": "Away", "home_team": "Home",
        "away_pitcher": "P1", "home_pitcher": "P2",
        "composite_score": 0.15,
        "ml_pick_team": "Home",
        "ml_win_probability": 58.0,
        "ml_confidence": 6,
        "ou_pick": "over", "ou_line": 8.0, "ou_confidence": 7,
    })

    fake_final = [{
        "mlb_game_id": 77001,
        "away_team_name": "Away", "home_team_name": "Home",
        "status": "Final", "away_score": 3, "home_score": 5,
        "game_date": dt.today().isoformat(),
    }]

    monkeypatch.setattr(engine, "fetch_todays_games", lambda: fake_final)
    monkeypatch.setattr(db, "get_today_picks", lambda: [])  # no picks, just log

    engine.run_results()

    conn = db.get_connection()
    row = conn.execute("SELECT * FROM analysis_log WHERE id=?", (log_id,)).fetchone()
    conn.close()
    # Home won 5-3 → ml correct. Total=8 = line 8.0 → push
    assert row["ml_status"] == "correct"
    assert row["ou_status"] == "push"
    assert row["actual_total"] == 8
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_analysis_log.py::test_run_results_grades_analysis_log -v
```

Expected: FAIL — ml_status still "pending"

- [ ] **Step 3: Add grading logic to `run_results()` in `engine.py`**

In `run_results()`, after the existing picks grading loop (after `conn.close()` at the end of the picks loop), add:

```python
    # ── Grade analysis_log entries ──
    log_entries = db.get_today_analysis_log()
    log_correct = 0
    log_incorrect = 0
    log_ou_correct = 0
    log_ou_incorrect = 0

    for entry in log_entries:
        if entry["ml_status"] != "pending":
            continue

        mlb_game_id = entry["mlb_game_id"]
        result = next((fg for fg in final_games if fg["mlb_game_id"] == mlb_game_id), None)
        if not result:
            continue

        away_score = result.get("away_score", 0) or 0
        home_score = result.get("home_score", 0) or 0
        total_runs = away_score + home_score

        # Grade ML prediction
        home_won = home_score > away_score
        away_won = away_score > home_score
        if entry["ml_pick_team"] == result.get("home_team_name"):
            ml_status = "correct" if home_won else "incorrect"
        elif entry["ml_pick_team"] == result.get("away_team_name"):
            ml_status = "correct" if away_won else "incorrect"
        else:
            ml_status = "push"

        # Grade O/U prediction
        ou_status = "none"
        if entry.get("ou_pick") and entry.get("ou_line"):
            ou_line = float(entry["ou_line"])
            if entry["ou_pick"] == "over":
                ou_status = "correct" if total_runs > ou_line else ("push" if total_runs == ou_line else "incorrect")
            elif entry["ou_pick"] == "under":
                ou_status = "correct" if total_runs < ou_line else ("push" if total_runs == ou_line else "incorrect")

        db.update_analysis_log_result(
            entry["id"], ml_status=ml_status, ou_status=ou_status,
            actual_away=away_score, actual_home=home_score, actual_total=total_runs
        )

        if ml_status == "correct":
            log_correct += 1
        elif ml_status == "incorrect":
            log_incorrect += 1
        if ou_status == "correct":
            log_ou_correct += 1
        elif ou_status == "incorrect":
            log_ou_incorrect += 1

    log_total = log_correct + log_incorrect
    print(f"\n  Model Accuracy (all {len(log_entries)} games):")
    print(f"  ML: {log_correct}W {log_incorrect}L  ({round(log_correct/log_total*100,1) if log_total else 0}%)")
    print(f"  O/U: {log_ou_correct}W {log_ou_incorrect}L")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_analysis_log.py::test_run_results_grades_analysis_log -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_analysis_log.py
git commit -m "feat: grade analysis_log entries in run_results"
```

---

### Task 6: Extend `--status` snapshot to show model accuracy

**Files:**
- Modify: `database.py` — add `get_model_accuracy_summary()`
- Modify: `engine.py` — `_print_snapshot()`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_analysis_log.py`:

```python
def test_get_model_accuracy_summary(tmp_db):
    from datetime import date as dt
    today = dt.today().isoformat()

    for i, (ml_s, ou_s) in enumerate([
        ("correct", "correct"),
        ("correct", "incorrect"),
        ("incorrect", "none"),
        ("correct", "correct"),
    ]):
        log_id = db.save_analysis_log({
            "game_date": today,
            "mlb_game_id": 88000 + i,
            "game": f"A{i} @ B{i}",
            "away_team": f"A{i}", "home_team": f"B{i}",
            "away_pitcher": "P", "home_pitcher": "P",
            "composite_score": 0.1,
            "ml_pick_team": f"B{i}",
            "ml_win_probability": 55.0,
            "ml_confidence": 5,
            "ou_pick": "over" if ou_s != "none" else None,
            "ou_line": 8.0 if ou_s != "none" else None,
            "ou_confidence": 6 if ou_s != "none" else None,
        })
        db.update_analysis_log_result(log_id, ml_status=ml_s, ou_status=ou_s,
                                       actual_away=3, actual_home=5, actual_total=8)

    summary = db.get_model_accuracy_summary(30)
    assert summary["ml_correct"] == 3
    assert summary["ml_incorrect"] == 1
    assert summary["ml_total"] == 4
    assert summary["ml_accuracy"] == 75.0
    assert summary["ou_correct"] == 2
    assert summary["ou_incorrect"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_analysis_log.py::test_get_model_accuracy_summary -v
```

Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement `get_model_accuracy_summary()` in `database.py`**

Add after `get_roi_summary()`:

```python
def get_model_accuracy_summary(days: int = 30) -> dict:
    """Model accuracy across all logged games (not just sent picks) over last N days."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT ml_status, ou_status, COUNT(*) as cnt
        FROM analysis_log
        WHERE game_date >= date('now', ?)
        AND ml_status IN ('correct','incorrect','push')
        GROUP BY ml_status, ou_status
    """, (f"-{days} days",)).fetchall()
    conn.close()

    ml_correct = ml_incorrect = ou_correct = ou_incorrect = 0
    for r in rows:
        if r["ml_status"] == "correct":
            ml_correct += r["cnt"]
        elif r["ml_status"] == "incorrect":
            ml_incorrect += r["cnt"]
        if r["ou_status"] == "correct":
            ou_correct += r["cnt"]
        elif r["ou_status"] == "incorrect":
            ou_incorrect += r["cnt"]

    ml_total = ml_correct + ml_incorrect
    ou_total = ou_correct + ou_incorrect
    return {
        "ml_correct": ml_correct,
        "ml_incorrect": ml_incorrect,
        "ml_total": ml_total,
        "ml_accuracy": round(ml_correct / ml_total * 100, 1) if ml_total else 0.0,
        "ou_correct": ou_correct,
        "ou_incorrect": ou_incorrect,
        "ou_total": ou_total,
        "ou_accuracy": round(ou_correct / ou_total * 100, 1) if ou_total else 0.0,
    }
```

- [ ] **Step 4: Update `_print_snapshot()` in `engine.py`**

Replace the current `_print_snapshot()`:

```python
def _print_snapshot():
    """Print pick accuracy + model accuracy tracking snapshot."""
    pick_summary = db.get_roi_summary(30)
    model_summary = db.get_model_accuracy_summary(30)

    print("\n" + "-" * 40)
    print("  TRACKING SNAPSHOT (Last 30 Days)")
    print("-" * 40)
    print(f"  PICKS SENT:  {pick_summary['won']}W - {pick_summary['lost']}L - {pick_summary['push']}P  "
          f"({pick_summary['win_rate']}% win rate)  [{pick_summary['total']} graded]")
    print(f"  MODEL ML:    {model_summary['ml_correct']}W - {model_summary['ml_incorrect']}L  "
          f"({model_summary['ml_accuracy']}% accuracy)  [{model_summary['ml_total']} games]")
    print(f"  MODEL O/U:   {model_summary['ou_correct']}W - {model_summary['ou_incorrect']}L  "
          f"({model_summary['ou_accuracy']}% accuracy)  [{model_summary['ou_total']} games]")
```

- [ ] **Step 5: Run all tests**

```bash
python3 -m pytest tests/test_analysis_log.py -v
```

Expected: All tests PASS

- [ ] **Step 6: Run full engine and verify snapshot output**

```bash
python3 engine.py 2>&1 | grep -A 5 "TRACKING SNAPSHOT"
```

Expected output format:
```
  TRACKING SNAPSHOT (Last 30 Days)
----------------------------------------
  PICKS SENT:  0W - 0L - 0P  (0.0% win rate)  [0 graded]
  MODEL ML:    0W - 0L  (0.0% accuracy)  [0 games]
  MODEL O/U:   0W - 0L  (0.0% accuracy)  [0 games]
```

- [ ] **Step 7: Commit**

```bash
git add database.py engine.py tests/test_analysis_log.py
git commit -m "feat: add model accuracy to --status snapshot"
```

---

## Self-Review

**Spec coverage:**
- ✅ Log all 15 games daily → Task 4
- ✅ Grade ML and O/U predictions against final scores → Task 5
- ✅ Show pick accuracy vs model accuracy in --status → Task 6
- ✅ analysis_log table isolated from picks table → Task 1

**Placeholder scan:** None found.

**Type consistency:** `update_analysis_log_result(log_id, ml_status, ou_status, actual_away, actual_home, actual_total)` used consistently across Tasks 3, 5, 6. `get_model_accuracy_summary(days)` defined in Task 6 step 3, called in Task 6 step 4. All consistent.
