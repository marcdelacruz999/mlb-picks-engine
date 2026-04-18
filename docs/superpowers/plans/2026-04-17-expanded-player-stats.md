# Expanded Player & Team Stats Collection Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand per-game stat collection to capture pitch count, ground/fly ball splits, stolen bases, plate appearances, inherited runners, and team pitching — feeding richer signals into the pick engine.

**Architecture:** Extend existing DB tables with new columns (ALTER TABLE + migration guard), expand `collect_boxscores()` and `collect_batter_boxscores()` to write new fields, add new DB query functions for the new signals, wire signals into `analysis.py` agents (pitching fatigue, bullpen quality, offense K-rate). No new tables needed — all additions are columns on existing tables.

**Tech Stack:** Python 3.9, SQLite via `database.py`, MLB Stats API `/game/{pk}/boxscore`, existing `collect_boxscores()` / `collect_batter_boxscores()` patterns.

---

## Files Changed

| File | Change |
|------|--------|
| `database.py` | Add migration for new columns; expand `collect_batter_boxscores()`; expand `collect_boxscores()` pitcher/team writes; add 4 new query functions |
| `data_mlb.py` | Expand `collect_boxscores()` pitcher dict + team_pitching dict |
| `analysis.py` | Wire `pitch_count` into pitcher rest/fatigue; wire `gb_pct` into pitching agent; wire `inherited_runners_scored` into bullpen agent; wire `stolen_bases` into offense agent |
| `engine.py` | No changes needed — `--collect` already calls all three collectors |
| `tests/test_database.py` | Tests for new columns and query functions |

---

## Task 1: DB Migration — New Columns

**Files:**
- Modify: `database.py`

Add new columns to three existing tables. All use the safe `OperationalError` migration guard.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_database.py — add at bottom of file

def test_pitcher_game_logs_new_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pitcher_game_logs)").fetchall()]
    assert "pitch_count" in cols
    assert "batters_faced" in cols
    assert "ground_outs" in cols
    assert "fly_outs" in cols
    assert "inherited_runners" in cols
    assert "inherited_runners_scored" in cols
    conn.close()

def test_batter_game_logs_new_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(batter_game_logs)").fetchall()]
    assert "runs" in cols
    assert "stolen_bases" in cols
    assert "hit_by_pitch" in cols
    assert "plate_appearances" in cols
    conn.close()

def test_team_game_logs_pitching_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(team_game_logs)").fetchall()]
    assert "pitching_strikeouts" in cols
    assert "pitching_walks" in cols
    assert "pitching_hits_allowed" in cols
    assert "pitching_earned_runs" in cols
    assert "pitching_home_runs_allowed" in cols
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_database.py::test_pitcher_game_logs_new_columns tests/test_database.py::test_batter_game_logs_new_columns tests/test_database.py::test_team_game_logs_pitching_columns -v
```
Expected: 3 FAIL — columns not found.

- [ ] **Step 3: Add migrations to `init_db()` in `database.py`**

Find the `init_db()` function (search for `def init_db`). At the end of the function, before the final `conn.close()`, add:

```python
    # ── Expand pitcher_game_logs ──
    for col, typedef in [
        ("pitch_count", "INTEGER"),
        ("batters_faced", "INTEGER"),
        ("ground_outs", "INTEGER"),
        ("fly_outs", "INTEGER"),
        ("inherited_runners", "INTEGER"),
        ("inherited_runners_scored", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE pitcher_game_logs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass

    # ── Expand batter_game_logs ──
    for col, typedef in [
        ("runs", "INTEGER"),
        ("stolen_bases", "INTEGER"),
        ("hit_by_pitch", "INTEGER"),
        ("plate_appearances", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE batter_game_logs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass

    # ── Expand team_game_logs with opponent pitching stats ──
    for col, typedef in [
        ("pitching_strikeouts", "INTEGER"),
        ("pitching_walks", "INTEGER"),
        ("pitching_hits_allowed", "INTEGER"),
        ("pitching_earned_runs", "INTEGER"),
        ("pitching_home_runs_allowed", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE team_game_logs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_database.py::test_pitcher_game_logs_new_columns tests/test_database.py::test_batter_game_logs_new_columns tests/test_database.py::test_team_game_logs_pitching_columns -v
```
Expected: 3 PASS.

- [ ] **Step 5: Apply migration to live DB**

```bash
python3 -c "import database; database.init_db(); print('Migration applied')"
```

Verify:
```bash
sqlite3 ~/Projects/Claude/Projects/Shenron/mlb-picks-engine/mlb_picks.db "PRAGMA table_info(pitcher_game_logs);" | grep -E "pitch_count|batters_faced|ground_outs|fly_outs|inherited"
sqlite3 ~/Projects/Claude/Projects/Shenron/mlb-picks-engine/mlb_picks.db "PRAGMA table_info(batter_game_logs);" | grep -E "runs|stolen|hit_by|plate_app"
sqlite3 ~/Projects/Claude/Projects/Shenron/mlb-picks-engine/mlb_picks.db "PRAGMA table_info(team_game_logs);" | grep "pitching_"
```
Expected: all 15 new columns present.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add pitch_count, gb/fb, inherited runners, stolen bases, team pitching columns"
```

---

## Task 2: Expand `collect_boxscores()` in `data_mlb.py`

**Files:**
- Modify: `data_mlb.py:641-696` (the pitcher_logs.append and team_logs.append blocks)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database.py

def test_collect_boxscores_returns_new_pitcher_fields(monkeypatch):
    """collect_boxscores pitcher dicts include new fields."""
    import data_mlb

    fake_schedule = {"dates": [{"games": [{"gamePk": 999, "status": {"abstractGameState": "Final"}}]}]}
    fake_boxscore = {
        "teams": {
            "away": {
                "team": {"id": 1},
                "pitchers": [100],
                "players": {
                    "ID100": {
                        "person": {"fullName": "Test Pitcher"},
                        "stats": {
                            "pitching": {
                                "inningsPitched": "6.0",
                                "earnedRuns": 2,
                                "strikeOuts": 7,
                                "baseOnBalls": 1,
                                "hits": 5,
                                "homeRuns": 0,
                                "numberOfPitches": 94,
                                "battersFaced": 22,
                                "groundOuts": 8,
                                "airOuts": 5,
                                "inheritedRunners": 0,
                                "inheritedRunnersScored": 0,
                            }
                        },
                    }
                },
            },
            "home": {"team": {"id": 2}, "pitchers": [], "players": {}, "teamStats": {"batting": {}, "pitching": {}}},
        }
    }

    import unittest.mock as mock
    def fake_get(url, timeout=15):
        r = mock.MagicMock()
        r.raise_for_status = lambda: None
        if "schedule" in url:
            r.json.return_value = fake_schedule
        else:
            r.json.return_value = fake_boxscore
        return r

    monkeypatch.setattr(data_mlb.requests, "get", fake_get)
    monkeypatch.setattr(data_mlb.time, "sleep", lambda x: None)

    result = data_mlb.collect_boxscores("2026-04-17")
    pitcher = result["pitcher_logs"][0]
    assert pitcher["pitch_count"] == 94
    assert pitcher["batters_faced"] == 22
    assert pitcher["ground_outs"] == 8
    assert pitcher["fly_outs"] == 5
    assert pitcher["inherited_runners"] == 0
    assert pitcher["inherited_runners_scored"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_database.py::test_collect_boxscores_returns_new_pitcher_fields -v
```
Expected: FAIL — KeyError on new fields.

- [ ] **Step 3: Expand pitcher dict in `collect_boxscores()`**

In `data_mlb.py`, find `pitcher_logs.append({` (around line 670). Replace the entire `pitcher_logs.append({...})` block with:

```python
                pitcher_logs.append({
                    "mlb_game_id": game_pk,
                    "game_date": game_date,
                    "pitcher_id": pid,
                    "pitcher_name": player.get("person", {}).get("fullName", "Unknown"),
                    "team_id": team_id,
                    "is_starter": (idx == 0),
                    "opponent_team_id": home_team_id if side == "away" else away_team_id,
                    "innings_pitched": ip,
                    "earned_runs": pstats.get("earnedRuns", 0) or 0,
                    "strikeouts": pstats.get("strikeOuts", 0) or 0,
                    "walks": pstats.get("baseOnBalls", 0) or 0,
                    "hits": pstats.get("hits", 0) or 0,
                    "home_runs": pstats.get("homeRuns", 0) or 0,
                    "pitch_count": pstats.get("numberOfPitches", 0) or 0,
                    "batters_faced": pstats.get("battersFaced", 0) or 0,
                    "ground_outs": pstats.get("groundOuts", 0) or 0,
                    "fly_outs": pstats.get("airOuts", 0) or 0,
                    "inherited_runners": pstats.get("inheritedRunners", 0) or 0,
                    "inherited_runners_scored": pstats.get("inheritedRunnersScored", 0) or 0,
                })
```

Also expand `team_logs.append({...})` to include team pitching stats. Find the block for `bat = team_data.get("teamStats", {}).get("batting", {})` and replace the entire `team_logs.append({...})`:

```python
            bat = team_data.get("teamStats", {}).get("batting", {})
            pit = team_data.get("teamStats", {}).get("pitching", {})
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
                "pitching_strikeouts": pit.get("strikeOuts", 0) or 0,
                "pitching_walks": pit.get("baseOnBalls", 0) or 0,
                "pitching_hits_allowed": pit.get("hits", 0) or 0,
                "pitching_earned_runs": pit.get("earnedRuns", 0) or 0,
                "pitching_home_runs_allowed": pit.get("homeRuns", 0) or 0,
            })
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_database.py::test_collect_boxscores_returns_new_pitcher_fields -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add data_mlb.py tests/test_database.py
git commit -m "feat: expand collect_boxscores with pitch_count, gb/fb, inherited runners, team pitching"
```

---

## Task 3: Expand DB Write in `database.py` — Pitcher & Team Logs

**Files:**
- Modify: `database.py` — `store_pitcher_game_logs()` and `store_team_game_logs()`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_database.py

def test_store_pitcher_logs_writes_new_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    pitcher_logs = [{
        "mlb_game_id": 999, "game_date": "2026-04-17",
        "pitcher_id": 100, "pitcher_name": "Test P", "team_id": 1,
        "is_starter": 1, "opponent_team_id": 2,
        "innings_pitched": 6.0, "earned_runs": 2, "strikeouts": 7,
        "walks": 1, "hits": 5, "home_runs": 0,
        "pitch_count": 94, "batters_faced": 22,
        "ground_outs": 8, "fly_outs": 5,
        "inherited_runners": 0, "inherited_runners_scored": 0,
    }]
    database.store_pitcher_game_logs(pitcher_logs)
    conn = database.get_connection()
    row = conn.execute("SELECT * FROM pitcher_game_logs WHERE pitcher_id=100").fetchone()
    conn.close()
    assert row["pitch_count"] == 94
    assert row["batters_faced"] == 22
    assert row["ground_outs"] == 8
    assert row["fly_outs"] == 5

def test_store_team_logs_writes_pitching_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    team_logs = [{
        "mlb_game_id": 999, "game_date": "2026-04-17",
        "team_id": 1, "is_away": 0,
        "runs": 5, "hits": 9, "home_runs": 1,
        "strikeouts": 8, "walks": 3, "at_bats": 33, "left_on_base": 6,
        "pitching_strikeouts": 11, "pitching_walks": 2,
        "pitching_hits_allowed": 7, "pitching_earned_runs": 3,
        "pitching_home_runs_allowed": 1,
    }]
    database.store_team_game_logs(team_logs)
    conn = database.get_connection()
    row = conn.execute("SELECT * FROM team_game_logs WHERE team_id=1").fetchone()
    conn.close()
    assert row["pitching_strikeouts"] == 11
    assert row["pitching_earned_runs"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_database.py::test_store_pitcher_logs_writes_new_fields tests/test_database.py::test_store_team_logs_writes_pitching_fields -v
```
Expected: 2 FAIL.

- [ ] **Step 3: Find and update `store_pitcher_game_logs()` in `database.py`**

Search for `def store_pitcher_game_logs`. Update the INSERT to include new columns:

```python
def store_pitcher_game_logs(pitcher_logs: list) -> int:
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    inserted = 0
    for log in pitcher_logs:
        try:
            c = conn.execute("""
                INSERT OR IGNORE INTO pitcher_game_logs
                (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id,
                 is_starter, opponent_team_id, innings_pitched, earned_runs,
                 strikeouts, walks, hits, home_runs,
                 pitch_count, batters_faced, ground_outs, fly_outs,
                 inherited_runners, inherited_runners_scored, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                log["mlb_game_id"], log["game_date"], log["pitcher_id"],
                log.get("pitcher_name", "Unknown"), log["team_id"],
                int(log.get("is_starter", 0)), log.get("opponent_team_id"),
                log["innings_pitched"], log.get("earned_runs", 0),
                log.get("strikeouts", 0), log.get("walks", 0),
                log.get("hits", 0), log.get("home_runs", 0),
                log.get("pitch_count", 0), log.get("batters_faced", 0),
                log.get("ground_outs", 0), log.get("fly_outs", 0),
                log.get("inherited_runners", 0), log.get("inherited_runners_scored", 0),
                now,
            ))
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except sqlite3.DatabaseError as e:
            print(f"[DB] store_pitcher_game_logs error: {e}")
    conn.commit()
    conn.close()
    return inserted
```

Also update `store_team_game_logs()` similarly:

```python
def store_team_game_logs(team_logs: list) -> int:
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    inserted = 0
    for log in team_logs:
        try:
            c = conn.execute("""
                INSERT OR IGNORE INTO team_game_logs
                (mlb_game_id, game_date, team_id, is_away,
                 runs, hits, home_runs, strikeouts, walks, at_bats, left_on_base,
                 pitching_strikeouts, pitching_walks, pitching_hits_allowed,
                 pitching_earned_runs, pitching_home_runs_allowed, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                log["mlb_game_id"], log["game_date"], log["team_id"],
                int(log.get("is_away", 0)),
                log.get("runs", 0), log.get("hits", 0), log.get("home_runs", 0),
                log.get("strikeouts", 0), log.get("walks", 0),
                log.get("at_bats", 0), log.get("left_on_base", 0),
                log.get("pitching_strikeouts", 0), log.get("pitching_walks", 0),
                log.get("pitching_hits_allowed", 0), log.get("pitching_earned_runs", 0),
                log.get("pitching_home_runs_allowed", 0),
                now,
            ))
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except sqlite3.DatabaseError as e:
            print(f"[DB] store_team_game_logs error: {e}")
    conn.commit()
    conn.close()
    return inserted
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_database.py::test_store_pitcher_logs_writes_new_fields tests/test_database.py::test_store_team_logs_writes_pitching_fields -v
```
Expected: 2 PASS.

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: existing 3 pre-existing failures only, all others PASS.

- [ ] **Step 6: Commit**

```bash
git add database.py
git commit -m "feat: expand store_pitcher_game_logs and store_team_game_logs with new stat fields"
```

---

## Task 4: Expand `collect_batter_boxscores()` in `database.py`

**Files:**
- Modify: `database.py:1231-1329` (`collect_batter_boxscores`)

- [ ] **Step 1: Write failing test**

```python
# tests/test_database.py

def test_collect_batter_boxscores_stores_new_fields(tmp_path, monkeypatch):
    import requests as req
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()

    fake_schedule = {"dates": [{"games": [{"gamePk": 999, "status": {"abstractGameState": "Final"}}]}]}
    fake_boxscore = {
        "teams": {
            "away": {
                "team": {"id": 1},
                "batters": [200],
                "players": {
                    "ID200": {
                        "person": {"fullName": "Test Batter"},
                        "stats": {
                            "batting": {
                                "atBats": 4, "hits": 2, "doubles": 1, "triples": 0,
                                "homeRuns": 0, "rbi": 1, "baseOnBalls": 1,
                                "strikeOuts": 1, "runs": 1, "stolenBases": 1,
                                "hitByPitch": 0, "plateAppearances": 5,
                            },
                            "pitching": {},
                        },
                    }
                },
            },
            "home": {"team": {"id": 2}, "batters": [], "players": {}},
        }
    }

    import unittest.mock as mock
    def fake_get(url, timeout=15):
        r = mock.MagicMock()
        r.raise_for_status = lambda: None
        r.json.return_value = fake_schedule if "schedule" in url else fake_boxscore
        return r

    monkeypatch.setattr(req, "get", fake_get)

    database.collect_batter_boxscores("2026-04-17")
    conn = database.get_connection()
    row = conn.execute("SELECT * FROM batter_game_logs WHERE batter_id=200").fetchone()
    conn.close()
    assert row["runs"] == 1
    assert row["stolen_bases"] == 1
    assert row["plate_appearances"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_database.py::test_collect_batter_boxscores_stores_new_fields -v
```
Expected: FAIL.

- [ ] **Step 3: Update `collect_batter_boxscores()` in `database.py`**

Find the `conn.execute("""INSERT OR IGNORE INTO batter_game_logs` block. Replace the entire INSERT statement and its values with:

```python
                    conn.execute("""
                        INSERT OR IGNORE INTO batter_game_logs
                        (mlb_game_id, game_date, batter_id, batter_name, team_id,
                         at_bats, hits, doubles, triples, home_runs, rbi, walks,
                         strikeouts, runs, stolen_bases, hit_by_pitch,
                         plate_appearances, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        game_pk, game_date, bid,
                        player.get("person", {}).get("fullName", "Unknown"),
                        team_id,
                        at_bats,
                        bstats.get("hits", 0) or 0,
                        bstats.get("doubles", 0) or 0,
                        bstats.get("triples", 0) or 0,
                        bstats.get("homeRuns", 0) or 0,
                        bstats.get("rbi", 0) or 0,
                        walks,
                        bstats.get("strikeOuts", 0) or 0,
                        bstats.get("runs", 0) or 0,
                        bstats.get("stolenBases", 0) or 0,
                        bstats.get("hitByPitch", 0) or 0,
                        bstats.get("plateAppearances", 0) or 0,
                        now,
                    ))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_database.py::test_collect_batter_boxscores_stores_new_fields -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py
git commit -m "feat: expand collect_batter_boxscores with runs, stolen_bases, hit_by_pitch, plate_appearances"
```

---

## Task 5: New Query Functions in `database.py`

Add four new query functions that analysis.py agents will consume.

**Files:**
- Modify: `database.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_database.py

def test_get_pitcher_pitch_count_rolling(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    now = datetime.now().isoformat()
    # Insert 3 starts over last 15 days
    for i, (gid, pc) in enumerate([(1, 95), (2, 102), (3, 88)]):
        conn.execute("""
            INSERT INTO pitcher_game_logs
            (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id,
             is_starter, innings_pitched, earned_runs, strikeouts, walks,
             hits, home_runs, pitch_count, created_at)
            VALUES (?,date('now', ?||' days'),999,'P',1,1,6.0,2,7,2,5,0,?,?)
        """, (gid, str(-(i+1)), pc, now))
    conn.commit()
    conn.close()

    result = database.get_pitcher_pitch_count_rolling(999, days=21)
    assert result is not None
    assert result["starts"] == 3
    assert result["avg_pitch_count"] == round((95 + 102 + 88) / 3, 1)
    assert result["last_pitch_count"] == 88

def test_get_pitcher_gb_fb_rate(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id,
         is_starter, innings_pitched, earned_runs, strikeouts, walks,
         hits, home_runs, ground_outs, fly_outs, created_at)
        VALUES (1,date('now','-1 days'),999,'P',1,1,6.0,2,7,2,5,0,12,5,?)
    """, (now,))
    conn.commit()
    conn.close()

    result = database.get_pitcher_gb_fb_rate(999, days=21)
    assert result is not None
    assert result["ground_outs"] == 12
    assert result["fly_outs"] == 5
    assert result["gb_pct"] == round(12 / (12 + 5), 3)

def test_get_bullpen_inherited_runner_rate(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    now = datetime.now().isoformat()
    # 3 relievers: inherited 5 total, scored 2
    for pid, ir, irs in [(1, 2, 1), (2, 2, 1), (3, 1, 0)]:
        conn.execute("""
            INSERT INTO pitcher_game_logs
            (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id,
             is_starter, innings_pitched, earned_runs, strikeouts, walks,
             hits, home_runs, inherited_runners, inherited_runners_scored, created_at)
            VALUES (?,date('now','-1 days'),?,'R',1,0,1.0,0,1,0,1,0,?,?,?)
        """, (pid, pid, ir, irs, now))
    conn.commit()
    conn.close()

    result = database.get_bullpen_inherited_runner_rate(1, days=7)
    assert result is not None
    assert result["inherited_runners"] == 5
    assert result["inherited_runners_scored"] == 2
    assert result["strand_rate"] == round(1 - 2/5, 3)

def test_get_team_stolen_base_rate(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    conn = database.get_connection()
    now = datetime.now().isoformat()
    for i in range(5):
        conn.execute("""
            INSERT INTO batter_game_logs
            (mlb_game_id, game_date, batter_id, batter_name, team_id,
             at_bats, hits, doubles, triples, home_runs, rbi, walks,
             strikeouts, stolen_bases, created_at)
            VALUES (?,date('now','-'||?||' days'),?,'B',1,4,1,0,0,0,0,0,0,?,?)
        """, (i+1, i+1, i+100, 1 if i < 3 else 0, now))
    conn.commit()
    conn.close()

    result = database.get_team_stolen_base_rate(1, days=14)
    assert result is not None
    assert result["stolen_bases"] == 3
    assert result["games"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_database.py::test_get_pitcher_pitch_count_rolling tests/test_database.py::test_get_pitcher_gb_fb_rate tests/test_database.py::test_get_bullpen_inherited_runner_rate tests/test_database.py::test_get_team_stolen_base_rate -v
```
Expected: 4 FAIL — functions not defined.

- [ ] **Step 3: Add the four query functions to `database.py`**

Add after `get_pitcher_rolling_stats_adjusted()` (around line 1040):

```python
def get_pitcher_pitch_count_rolling(pitcher_id: int, days: int = 21,
                                    as_of_date: str = None) -> "dict | None":
    """
    Rolling pitch count stats for a starter over last N days.
    Returns {"starts", "avg_pitch_count", "last_pitch_count"} or None.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT pitch_count, game_date
        FROM pitcher_game_logs
        WHERE pitcher_id=? AND is_starter=1 AND game_date > ? AND pitch_count > 0
        ORDER BY game_date DESC
    """, (pitcher_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    counts = [r["pitch_count"] for r in rows]
    return {
        "starts": len(counts),
        "avg_pitch_count": round(sum(counts) / len(counts), 1),
        "last_pitch_count": counts[0],
    }


def get_pitcher_gb_fb_rate(pitcher_id: int, days: int = 21,
                           as_of_date: str = None) -> "dict | None":
    """
    Ground ball / fly ball rates for a pitcher over last N days.
    Returns {"ground_outs", "fly_outs", "gb_pct"} or None.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(ground_outs) as go, SUM(fly_outs) as fo
        FROM pitcher_game_logs
        WHERE pitcher_id=? AND game_date > ? AND (ground_outs > 0 OR fly_outs > 0)
    """, (pitcher_id, cutoff.isoformat())).fetchone()
    conn.close()
    if not row or not row["go"] and not row["fo"]:
        return None
    go = row["go"] or 0
    fo = row["fo"] or 0
    total = go + fo
    return {
        "ground_outs": go,
        "fly_outs": fo,
        "gb_pct": round(go / total, 3) if total > 0 else None,
    }


def get_bullpen_inherited_runner_rate(team_id: int, days: int = 7,
                                      as_of_date: str = None) -> "dict | None":
    """
    Bullpen inherited runner strand rate for a team over last N days.
    Returns {"inherited_runners", "inherited_runners_scored", "strand_rate"} or None.
    Strand rate = 1 - (scored / inherited). Higher = better bullpen.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(inherited_runners) as ir, SUM(inherited_runners_scored) as irs
        FROM pitcher_game_logs
        WHERE team_id=? AND is_starter=0 AND game_date > ? AND inherited_runners > 0
    """, (team_id, cutoff.isoformat())).fetchone()
    conn.close()
    if not row or not row["ir"]:
        return None
    ir = row["ir"]
    irs = row["irs"] or 0
    return {
        "inherited_runners": ir,
        "inherited_runners_scored": irs,
        "strand_rate": round(1 - irs / ir, 3),
    }


def get_team_stolen_base_rate(team_id: int, days: int = 14,
                              as_of_date: str = None) -> "dict | None":
    """
    Team stolen base total and games played over last N days.
    Returns {"stolen_bases", "games", "sb_per_game"} or None.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(stolen_bases) as sb, COUNT(DISTINCT game_date) as games
        FROM batter_game_logs
        WHERE team_id=? AND game_date > ?
    """, (team_id, cutoff.isoformat())).fetchone()
    conn.close()
    if not row or not row["games"]:
        return None
    sb = row["sb"] or 0
    games = row["games"]
    return {
        "stolen_bases": sb,
        "games": games,
        "sb_per_game": round(sb / games, 2),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_database.py::test_get_pitcher_pitch_count_rolling tests/test_database.py::test_get_pitcher_gb_fb_rate tests/test_database.py::test_get_bullpen_inherited_runner_rate tests/test_database.py::test_get_team_stolen_base_rate -v
```
Expected: 4 PASS.

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: pre-existing 3 failures only.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add pitch count, GB/FB, inherited runner, stolen base query functions"
```

---

## Task 6: Wire New Signals into `analysis.py`

Wire the four new query functions into the agents that benefit most. Keep changes minimal — one signal per agent, additive only (no score rewrites).

**Files:**
- Modify: `analysis.py`

**Signal → Agent mapping:**
- `get_pitcher_pitch_count_rolling()` → Pitching agent (fatigue supplement to IP)
- `get_pitcher_gb_fb_rate()` → Pitching agent (park factor interaction: GB pitchers hurt by turf/COL, helped by GB-heavy parks)
- `get_bullpen_inherited_runner_rate()` → Bullpen agent (strand rate = true quality signal)
- `get_team_stolen_base_rate()` → Offense agent (speed dimension, extra bases)

- [ ] **Step 1: Wire pitch count into pitching agent**

In `analysis.py`, find `score_pitching()`. After the existing rest/fatigue penalty block (search for `rest_days` and the penalty application), add:

```python
    # Pitch count fatigue — supplement to IP-based rest
    # High recent pitch counts signal arm fatigue even within normal rest windows
    for side, pid, direction in [
        ("home", game.get("home_starter_id"), 1),
        ("away", game.get("away_starter_id"), -1),
    ]:
        if not pid:
            continue
        pc_data = _analysis_db.get_pitcher_pitch_count_rolling(pid, days=21)
        if pc_data and pc_data["last_pitch_count"] >= 105:
            # High pitch count last start — apply small fatigue signal
            fatigue_adj = -0.04 * direction
            score = max(-1.0, min(1.0, score + fatigue_adj))
            edges.append(f"{'Home' if side == 'home' else 'Away'} SP high pitch load ({pc_data['last_pitch_count']}p last start)")
```

- [ ] **Step 2: Wire GB/FB rate into pitching agent**

In the same `score_pitching()`, after the pitch count block, add:

```python
    # GB/FB tendency × park factor interaction
    # GB pitchers are helped in GB-friendly parks, hurt in fly-ball/HR parks
    park_factor = PARK_FACTORS.get(_analysis_db.get_team_abbr_by_mlb_id(
        game.get("home_team_mlb_id", 0)), 1.0)
    for side, pid, direction in [
        ("home", game.get("home_starter_id"), 1),
        ("away", game.get("away_starter_id"), -1),
    ]:
        if not pid:
            continue
        gb_data = _analysis_db.get_pitcher_gb_fb_rate(pid, days=21)
        if not gb_data or gb_data["gb_pct"] is None:
            continue
        gb_pct = gb_data["gb_pct"]
        if gb_pct >= 0.55 and park_factor >= 1.10:
            # GB pitcher in hitter's park — slight negative (more GB = more singles in tight infield)
            score = max(-1.0, min(1.0, score - 0.03 * direction))
        elif gb_pct <= 0.35 and park_factor <= 0.93:
            # Fly ball pitcher in pitcher's park — slight positive
            score = max(-1.0, min(1.0, score + 0.03 * direction))
```

- [ ] **Step 3: Wire inherited runner strand rate into bullpen agent**

In `analysis.py`, find `score_bullpen()`. After the existing ERA/WHIP/fatigue logic, add:

```python
    # Inherited runner strand rate — true clutch bullpen quality
    for side, team_id, direction in [
        ("home", game.get("home_team_mlb_id"), 1),
        ("away", game.get("away_team_mlb_id"), -1),
    ]:
        if not team_id:
            continue
        ir_data = _analysis_db.get_bullpen_inherited_runner_rate(team_id, days=14)
        if not ir_data or ir_data["inherited_runners"] < 3:
            continue  # need minimum sample
        if ir_data["strand_rate"] >= 0.80:
            score = max(-1.0, min(1.0, score + 0.05 * direction))
            edges.append(f"{'Home' if side == 'home' else 'Away'} bullpen elite strand rate ({ir_data['strand_rate']:.0%})")
        elif ir_data["strand_rate"] <= 0.50:
            score = max(-1.0, min(1.0, score - 0.05 * direction))
            edges.append(f"{'Home' if side == 'home' else 'Away'} bullpen poor strand rate ({ir_data['strand_rate']:.0%})")
```

- [ ] **Step 4: Wire stolen base rate into offense agent**

In `analysis.py`, find `score_offense()`. After the existing hot/cold batter block, add:

```python
    # Stolen base speed dimension — extra bases create pressure, disrupt pitching rhythm
    for side, team_id, direction in [
        ("home", game.get("home_team_mlb_id"), 1),
        ("away", game.get("away_team_mlb_id"), -1),
    ]:
        if not team_id:
            continue
        sb_data = _analysis_db.get_team_stolen_base_rate(team_id, days=14)
        if not sb_data or sb_data["games"] < 5:
            continue
        if sb_data["sb_per_game"] >= 1.5:
            score = max(-1.0, min(1.0, score + 0.04 * direction))
```

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -25
```
Expected: pre-existing 3 failures only.

- [ ] **Step 6: Smoke test with live data**

```bash
python3 engine.py --game Cubs --test 2>&1 | grep -E "pitch load|strand rate|GB|stolen|Confidence|Pick"
```
Expected: no crash; may or may not show new edge strings depending on data availability.

- [ ] **Step 7: Commit**

```bash
git add analysis.py
git commit -m "feat: wire pitch count, GB/FB, inherited runners, stolen base signals into agents"
```

---

## Task 7: Backfill Today's Games with New Fields

Run `--collect` for today to populate the new columns for all 15 today games.

- [ ] **Step 1: Run collect**

```bash
python3 engine.py --collect 2026-04-17 2>&1 | grep -v NotOpenSSL | grep -v warnings
```
Expected output includes:
```
[DATA] collect_boxscores 2026-04-17: 118 pitcher lines, 30 team logs
[DB] Stored 118 pitcher lines, 30 team logs.
[DATA] collect_game_totals 2026-04-17: 15 final games
[DB] collect_batter_boxscores 2026-04-17: 304 rows inserted
```
Note: INSERT OR IGNORE means existing rows won't update. New columns will be NULL for today's already-inserted rows.

- [ ] **Step 2: Check NULL coverage**

```bash
sqlite3 ~/Projects/Claude/Projects/Shenron/mlb-picks-engine/mlb_picks.db "
SELECT COUNT(*) as total,
       SUM(CASE WHEN pitch_count IS NULL THEN 1 ELSE 0 END) as null_pitch_count,
       SUM(CASE WHEN pitch_count > 0 THEN 1 ELSE 0 END) as has_pitch_count
FROM pitcher_game_logs WHERE game_date='2026-04-17';
"
```

Since today's rows were already inserted with INSERT OR IGNORE, the new fields will be NULL. That's expected — tomorrow's collect will populate fully. **No backfill needed** — historical rows are fine as NULL; queries already guard with `AND pitch_count > 0`.

- [ ] **Step 3: Verify schema live**

```bash
sqlite3 ~/Projects/Claude/Projects/Shenron/mlb-picks-engine/mlb_picks.db "PRAGMA table_info(pitcher_game_logs);" | grep -c "."
```
Expected: 21 columns (was 15, added 6).

- [ ] **Step 4: Commit and push**

```bash
git add -A
git commit -m "chore: verify expanded stat collection live on 2026-04-17"
git push
```

---

## Self-Review

**Spec coverage:**
- ✅ Pitch count → `pitch_count` column + `get_pitcher_pitch_count_rolling()` + wired to pitching agent
- ✅ GB/FB splits → `ground_outs`/`fly_outs` + `get_pitcher_gb_fb_rate()` + wired to pitching agent
- ✅ Stolen bases + PA → `stolen_bases`, `plate_appearances`, `runs`, `hit_by_pitch` + `get_team_stolen_base_rate()` + wired to offense agent
- ✅ Inherited runners → `inherited_runners`/`inherited_runners_scored` + `get_bullpen_inherited_runner_rate()` + wired to bullpen agent
- ✅ Team pitching per game → `pitching_strikeouts`, `pitching_walks`, `pitching_hits_allowed`, `pitching_earned_runs`, `pitching_home_runs_allowed` stored in `team_game_logs` (available for future queries)
- ✅ DB migrations are safe (OperationalError guard)
- ✅ INSERT OR IGNORE preserved on all inserts (no data loss)
- ✅ All signals are additive — no rewrites to existing scoring logic
- ✅ Tests for every new function and DB write

**Type consistency:** All new DB functions return `dict | None`, matching existing patterns (`get_pitcher_rolling_stats`, `get_batter_rolling_ops`). All `_analysis_db` calls match function signatures defined in Task 5.

**No placeholders found.**
