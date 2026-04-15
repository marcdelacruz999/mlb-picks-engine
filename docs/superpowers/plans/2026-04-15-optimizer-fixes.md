# Optimizer Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two optimizer bugs — broken live agent signal scoring (garbage numbers from text columns) and reliability gaps (stale queue detection, zero-diff false success).

**Architecture:** All changes are in `optimizer.py` (query rewrite + two guard clauses) and `COMPLETED_IMPROVEMENTS.md` (one new entry). No schema changes, no new files, no new dependencies.

**Tech Stack:** Python 3.9, SQLite (via stdlib sqlite3), pytest, git CLI

---

## File Map

| File | What changes |
|------|-------------|
| `optimizer.py` | Rewrite `analyze_agent_signals()` to join `analysis_log`; add already-done guard in `select_improvement()`; add zero-diff guard in `main()` after `apply_via_claude()` |
| `COMPLETED_IMPROVEMENTS.md` | Append `batter_game_logs` entry so optimizer stops re-selecting it |
| `tests/test_optimizer_signals.py` | New test file — 3 tests for the fixed signal function |

**Do not touch:** `database.py`, `analysis.py`, `engine.py`, `config.py`, any other test file.

---

## Task 1: Register `batter_game_logs` as complete

The feature is fully implemented (table, functions, analysis signal, tests all exist) but the optimizer keeps re-selecting it every night because there's no `<!-- id: batter_game_logs -->` entry in COMPLETED_IMPROVEMENTS.md.

**Files:**
- Modify: `COMPLETED_IMPROVEMENTS.md`

- [ ] **Step 1: Append the entry**

Open `COMPLETED_IMPROVEMENTS.md` and append to the end:

```markdown
---

<!-- id: batter_game_logs -->
## Batter Game Logs & Hot/Cold Streak Signal
**Date:** 2026-04-15
**Commit:** feat: add batter game logs and hot/cold streak signal to offense agent
**Summary:** batter_game_logs table added to database.py with collect_batter_boxscores(), get_team_batter_hot_cold(), and per-team K-rate/rolling OPS helpers. Hot/cold streak signal integrated into score_offense() in analysis.py — bonus/penalty of ±0.04 when hot_count - cold_count >= 2. collect_batter_boxscores() called in engine.py --results run. 5 tests in tests/test_batter_logs.py covering table init, API parsing, hot/cold logic, and insufficient-data fallback.
```

- [ ] **Step 2: Verify optimizer would skip it**

```bash
python3 -c "
import re
from pathlib import Path
text = Path('COMPLETED_IMPROVEMENTS.md').read_text()
ids = set(re.findall(r'<!-- id: (\w+) -->', text))
print('batter_game_logs in completed:', 'batter_game_logs' in ids)
"
```

Expected output: `batter_game_logs in completed: True`

- [ ] **Step 3: Commit**

```bash
git add COMPLETED_IMPROVEMENTS.md
git commit -m "chore: mark batter_game_logs as complete — already implemented"
```

---

## Task 2: Fix `analyze_agent_signals()` — use real numeric scores

**Root cause:** `analyze_agent_signals()` queries `picks.edge_pitching` etc. (text narratives like `"Home SP (Paul Skenes, RHP) has clear pitching advantage | Home SP extended layoff (18d) — rust risk"`) and applies a regex `r'[+-]?\d+\.\d+'` to extract a number. The regex grabs the first decimal in the string — could be ERA, days, K-rate — producing meaningless differentials like `pitching: live=-2.757`.

**Fix:** Join `picks → games → analysis_log` on `games.mlb_game_id = analysis_log.mlb_game_id` and read real float scores directly.

**Verified join:** All 16/16 graded picks resolve. Sample: `score_pitching=0.467`, `score_bullpen=0.794`.

**Files:**
- Modify: `optimizer.py` (function `analyze_agent_signals`, lines ~485–566)
- Create: `tests/test_optimizer_signals.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_optimizer_signals.py`:

```python
"""Tests for analyze_agent_signals() using real numeric scores from analysis_log."""
import sqlite3
import tempfile
import os
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_test_db():
    """Create an in-memory-style temp DB with the tables optimizer needs."""
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    conn = sqlite3.connect(db.name)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE games (
            id INTEGER PRIMARY KEY,
            mlb_game_id INTEGER,
            game_date TEXT,
            status TEXT DEFAULT 'Final'
        );
        CREATE TABLE picks (
            id INTEGER PRIMARY KEY,
            game_id INTEGER,
            pick_type TEXT,
            pick_team TEXT,
            status TEXT,
            discord_sent INTEGER DEFAULT 1
        );
        CREATE TABLE analysis_log (
            id INTEGER PRIMARY KEY,
            mlb_game_id INTEGER,
            game_date TEXT,
            score_pitching REAL,
            score_offense REAL,
            score_bullpen REAL,
            score_advanced REAL,
            score_momentum REAL,
            score_weather REAL,
            score_market REAL
        );
    """)
    return db.name, conn


def _insert_game(conn, game_id, mlb_game_id, game_date):
    conn.execute(
        "INSERT INTO games VALUES (?,?,?,'Final')",
        (game_id, mlb_game_id, game_date)
    )


def _insert_pick(conn, pick_id, game_id, status):
    conn.execute(
        "INSERT INTO picks VALUES (?,?,'moneyline','Home',?,1)",
        (pick_id, game_id, status)
    )


def _insert_analysis(conn, mlb_game_id, game_date, scores):
    conn.execute(
        """INSERT INTO analysis_log
           (mlb_game_id, game_date, score_pitching, score_offense,
            score_bullpen, score_advanced, score_momentum, score_weather, score_market)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (mlb_game_id, game_date, scores["pitching"], scores["offense"],
         scores["bullpen"], scores["advanced"], scores.get("momentum", 0.0),
         scores.get("weather", 0.0), scores.get("market", 0.0))
    )


def test_differential_uses_analysis_log_scores(monkeypatch):
    """analyze_agent_signals should compute avg score on won vs lost from analysis_log."""
    db_name, conn = _make_test_db()
    today = date.today().isoformat()

    # Two won picks with high pitching scores, two lost with low
    for i, (status, pitch_score) in enumerate([
        ("won", 0.8), ("won", 0.6), ("lost", 0.1), ("lost", 0.2)
    ], start=1):
        _insert_game(conn, i, 1000 + i, today)
        _insert_pick(conn, i, i, status)
        _insert_analysis(conn, 1000 + i, today, {
            "pitching": pitch_score, "offense": 0.3,
            "bullpen": 0.3, "advanced": 0.1
        })
    conn.commit()

    import optimizer
    monkeypatch.setattr(optimizer, "DATABASE_PATH", db_name)
    monkeypatch.setattr(optimizer, "load_backtest_lift", lambda: {
        "pitching": 0.08, "offense": 0.04, "bullpen": 0.05,
        "advanced": -0.01, "momentum": None, "weather": None, "market": None
    })

    result = optimizer.analyze_agent_signals(days=60)
    conn.close()
    os.unlink(db_name)

    # avg_won_pitching = (0.8+0.6)/2 = 0.7; avg_lost = (0.1+0.2)/2 = 0.15; diff = 0.55
    assert "pitching" in result
    assert abs(result["pitching"]["live_differential"] - 0.55) < 0.001, (
        f"Expected ~0.55, got {result['pitching']['live_differential']}"
    )


def test_no_garbage_from_text_extraction(monkeypatch):
    """Live differential must not be in range ±3 — that was the bug symptom."""
    db_name, conn = _make_test_db()
    today = date.today().isoformat()

    for i, status in enumerate(["won"] * 8 + ["lost"] * 5, start=1):
        _insert_game(conn, i, 2000 + i, today)
        _insert_pick(conn, i, i, status)
        _insert_analysis(conn, 2000 + i, today, {
            "pitching": 0.4 if status == "won" else 0.2,
            "offense": 0.3, "bullpen": 0.25, "advanced": 0.05
        })
    conn.commit()

    import optimizer
    monkeypatch.setattr(optimizer, "DATABASE_PATH", db_name)
    monkeypatch.setattr(optimizer, "load_backtest_lift", lambda: {
        "pitching": 0.08, "offense": 0.04, "bullpen": 0.05,
        "advanced": -0.01, "momentum": None, "weather": None, "market": None
    })

    result = optimizer.analyze_agent_signals(days=60)
    conn.close()
    os.unlink(db_name)

    for agent, data in result.items():
        live = data["live_differential"]
        assert abs(live) < 2.0, (
            f"Agent '{agent}' live_differential={live:.3f} looks like garbage text extraction"
        )


def test_returns_zero_differential_when_no_graded_picks(monkeypatch):
    """With no graded picks, all differentials should be 0.0."""
    db_name, conn = _make_test_db()
    conn.commit()

    import optimizer
    monkeypatch.setattr(optimizer, "DATABASE_PATH", db_name)
    monkeypatch.setattr(optimizer, "load_backtest_lift", lambda: {
        "pitching": 0.08, "offense": 0.04, "bullpen": 0.05,
        "advanced": -0.01, "momentum": None, "weather": None, "market": None
    })

    result = optimizer.analyze_agent_signals(days=60)
    conn.close()
    os.unlink(db_name)

    assert result["pitching"]["live_differential"] == 0.0
    assert result["pitching"]["n_won"] == 0
    assert result["pitching"]["n_lost"] == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
pytest tests/test_optimizer_signals.py -v
```

Expected: All 3 tests FAIL (current implementation uses text extraction).

- [ ] **Step 3: Rewrite `analyze_agent_signals()` in optimizer.py**

Find the function at line ~485. Replace the entire function body with:

```python
def analyze_agent_signals(days=30):
    """
    Compare avg agent score on winning vs losing picks per agent.
    Reads real float scores from analysis_log (joined via games.mlb_game_id).
    Blends live signal with the 4,855-game backtest baseline.

    Blending weight:
      - < 20 live picks:  100% backtest
      - 20-99 picks:      backtest weighted, live signal slowly introduced
      - 100+ picks:       50/50 blend
      - 200+ picks:       live data takes precedence

    Returns dict: {agent: {differential, live_differential, backtest_lift,
                            blended_differential, n_won, n_lost, blend_weight_live}}
    """
    conn = get_db()
    since = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT p.status,
               al.score_pitching, al.score_offense, al.score_bullpen,
               al.score_advanced, al.score_momentum, al.score_weather, al.score_market
        FROM picks p
        JOIN games g ON p.game_id = g.id
        JOIN analysis_log al ON g.mlb_game_id = al.mlb_game_id
        WHERE g.game_date >= ? AND p.discord_sent = 1
          AND p.status IN ('won', 'lost')
    """, (since,)).fetchall()
    conn.close()

    backtest_lift = load_backtest_lift()
    n_graded = len(rows)

    # Blend weight for live signal: 0 at <20 picks, ramps to 0.5 at 100, 0.8 at 200
    if n_graded < 20:
        live_weight = 0.0
    elif n_graded < 100:
        live_weight = round((n_graded - 20) / 80 * 0.5, 3)
    elif n_graded < 200:
        live_weight = round(0.5 + (n_graded - 100) / 100 * 0.3, 3)
    else:
        live_weight = 0.8

    agent_col = {
        "pitching": "score_pitching",
        "offense":  "score_offense",
        "bullpen":  "score_bullpen",
        "advanced": "score_advanced",
        "momentum": "score_momentum",
        "weather":  "score_weather",
        "market":   "score_market",
    }
    result = {}

    for agent, col in agent_col.items():
        won_vals, lost_vals = [], []
        for r in rows:
            val = r[col]
            if val is None:
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            (won_vals if r["status"] == "won" else lost_vals).append(val)

        avg_won  = sum(won_vals)  / len(won_vals)  if won_vals  else 0.0
        avg_lost = sum(lost_vals) / len(lost_vals) if lost_vals else 0.0
        live_diff = round(avg_won - avg_lost, 4)

        bt_lift = backtest_lift.get(agent)  # None = untestable

        # Blended differential — if backtest is None (untestable), use live only
        if bt_lift is None:
            blended = live_diff if n_graded >= 20 else 0.0
        else:
            blended = round((1 - live_weight) * bt_lift + live_weight * live_diff, 4)

        result[agent] = {
            "live_differential":    live_diff,
            "backtest_lift":        bt_lift,
            "blended_differential": blended,
            "differential":         blended,   # alias used by select_improvement
            "n_won":  len(won_vals),
            "n_lost": len(lost_vals),
            "blend_weight_live":    live_weight,
        }

    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_optimizer_signals.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Confirm live differentials look sane**

```bash
python3 -c "
import optimizer
result = optimizer.analyze_agent_signals(days=30)
for agent, data in result.items():
    print(f'{agent}: live={data[\"live_differential\"]:+.4f} bt={data[\"backtest_lift\"]} blended={data[\"blended_differential\"]:+.4f} n={data[\"n_won\"]}W-{data[\"n_lost\"]}L')
"
```

Expected: All `live_differential` values in range `[-1.5, 1.5]` (agent scores are bounded floats, not ERA values).

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
pytest tests/ -v --tb=short
```

Expected: All previously-passing tests still pass. Note any pre-existing failures — do not fix them here.

- [ ] **Step 7: Commit**

```bash
git add optimizer.py tests/test_optimizer_signals.py
git commit -m "fix: analyze_agent_signals uses analysis_log scores not text edge extraction"
```

---

## Task 3: Add zero-diff guard after Claude dispatch

**Problem:** If Claude CLI returns exit code 0 but makes no file changes, `apply_via_claude()` returns `{"success": True}`. The caller then proceeds to `git_commit()` (silently fails — nothing to commit) and calls `mark_complete()` (writes to COMPLETED_IMPROVEMENTS.md without a real commit). The item gets falsely registered as done.

**Fix:** After `apply_via_claude()` returns success, check `git diff --name-only HEAD`. If empty, treat as no-op — don't commit, don't mark complete, set result to skipped.

**Files:**
- Modify: `optimizer.py` (the `elif imp_type == "claude":` block in `main()`, lines ~1570–1600)

- [ ] **Step 1: Find the exact line range to edit**

```bash
grep -n "imp_type == .claude.\|Dispatching to Claude\|Claude implementation failed\|apply_via_claude" /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/optimizer.py
```

The block to edit is the `elif imp_type == "claude":` branch in `main()`.

- [ ] **Step 2: Replace the `elif imp_type == "claude":` block**

Find this block (currently ~lines 1571–1600):

```python
    elif imp_type == "claude":
        print(f"  Dispatching to Claude Code CLI...")
        impl = apply_via_claude(improvement["task"])
        if impl.get("success"):
            tests_passed, test_output = run_tests()
            if tests_passed:
                git_commit(f"feat: optimizer: {improvement['name']}")
                diff = git_diff_stat()
                mark_complete(improvement["id"], improvement["name"], improvement["description"])
                result = {
                    "success": True,
                    "diff_stat": impl.get("diff_stat", diff),
                    "tests_passed": True,
                }
                print(f"  ✅ Claude implementation succeeded.")
            else:
                # Tests failed — revert Claude's changes
                git_revert()
                result = {
                    "success": False,
                    "error": "Tests failed after Claude implementation — reverted",
                    "test_output": test_output,
                }
                print(f"  ❌ Tests failed — reverted Claude's changes.")
        else:
            result = {"success": False, "error": impl.get("error", "Unknown error")}
            print(f"  ❌ Claude implementation failed: {impl.get('error')}")
```

Replace with:

```python
    elif imp_type == "claude":
        print(f"  Dispatching to Claude Code CLI...")
        impl = apply_via_claude(improvement["task"])
        if impl.get("success"):
            # Guard: verify Claude actually changed something
            changed_files = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True
            ).stdout.strip()
            if not changed_files:
                result = {"skipped": True, "reason": "Claude ran but made no file changes"}
                print(f"  ⚠️  Claude returned success but changed nothing — skipping.")
            else:
                tests_passed, test_output = run_tests()
                if tests_passed:
                    git_commit(f"feat: optimizer: {improvement['name']}")
                    diff = git_diff_stat()
                    mark_complete(improvement["id"], improvement["name"], improvement["description"])
                    result = {
                        "success": True,
                        "diff_stat": impl.get("diff_stat", diff),
                        "tests_passed": True,
                    }
                    print(f"  ✅ Claude implementation succeeded.")
                else:
                    # Tests failed — revert Claude's changes
                    git_revert()
                    result = {
                        "success": False,
                        "error": "Tests failed after Claude implementation — reverted",
                        "test_output": test_output,
                    }
                    print(f"  ❌ Tests failed — reverted Claude's changes.")
        else:
            result = {"success": False, "error": impl.get("error", "Unknown error")}
            print(f"  ❌ Claude implementation failed: {impl.get('error')}")
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: All previously-passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add optimizer.py
git commit -m "fix: optimizer zero-diff guard — skip mark_complete when Claude makes no changes"
```

---

## Task 4: Add already-done guard in `select_improvement()`

**Problem:** `get_completed_ids()` is called at the top of `select_improvement()` and the result is used to skip `api_error_handling` (Priority 1) — but the `code_queue` loop (Priority 4) does NOT check `completed`. It only calls `if imp_id not in completed` — wait, actually it does:

```python
for imp_id, imp_name, task in code_queue:
    if imp_id not in completed:
```

But `completed` comes from scanning `COMPLETED_IMPROVEMENTS.md` for `<!-- id: X -->` markers. The bug is that `batter_game_logs` was never appended to that file (fixed in Task 1). After Task 1 the guard works correctly.

**Verify the guard is sound:** After Task 1, the `get_completed_ids()` path correctly reads the file and the `code_queue` loop skips completed items. No code change needed here — Task 1 was the fix.

- [ ] **Step 1: Verify the guard works end-to-end**

```bash
python3 -c "
import optimizer
completed = optimizer.get_completed_ids()
print('Completed IDs:', completed)
print('batter_game_logs skipped:', 'batter_game_logs' in completed)
"
```

Expected: `batter_game_logs` appears in the set and `skipped: True`.

- [ ] **Step 2: Run a dry optimizer pass to confirm next queue item**

```bash
python3 optimizer.py 2>&1 | head -30
```

Expected: `Selected: [pitcher_vs_team]` (or `cooldown` if last optimizer commit was <7 days ago). `batter_game_logs` must NOT appear.

---

## Self-Review

**Spec coverage:**
- ✅ Bug A (text extraction → analysis_log join): Task 2
- ✅ Bug B1 (already-done detection): Task 1 + Task 4 verification
- ✅ Bug B2 (zero-diff false success): Task 3
- ✅ Bug B3 (mark batter_game_logs complete): Task 1

**Placeholder scan:** None found. All code blocks are complete and runnable.

**Type consistency:** `analyze_agent_signals()` return structure in Task 2 matches the existing callers — keys `differential`, `live_differential`, `backtest_lift`, `blended_differential`, `n_won`, `n_lost`, `blend_weight_live` are preserved exactly. `select_improvement()` uses `signal[agent]["differential"]` — matches.
