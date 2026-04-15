# Optimizer Fixes Design
**Date:** 2026-04-15
**Scope:** Fix two classes of bugs in optimizer.py — broken live agent signal scoring and optimizer reliability gaps.

---

## Background

The nightly optimizer report showed alarming live agent divergences:
- `pitching: live=-2.757` vs backtest `+0.080`
- `bullpen: live=-3.210` vs backtest `+0.050`
- `advanced: live=+5.302` vs backtest `-0.009`

Investigation revealed two root causes, plus a queue management bug that caused a failed nightly run.

---

## Bug A: `analyze_agent_signals` extracts garbage numbers from text columns

### Root Cause

`picks.edge_pitching`, `picks.edge_bullpen`, etc. store **human-readable narrative strings**, e.g.:

```
"Home SP (Paul Skenes, RHP) has clear pitching advantage | Home SP extended layoff (18d) — rust risk"
```

`analyze_agent_signals()` applies `re.search(r'[+-]?\d+\.\d+', raw)` to extract a numeric value. With only 16 graded picks, the first decimal found in each string (ERA values, K-rates, days as `18.0`, xwOBA deltas) produces meaningless differentials — hence `live=-2.757` for pitching.

### Fix

Rewrite `analyze_agent_signals()` to join `picks → games → analysis_log` on `games.mlb_game_id = analysis_log.mlb_game_id` and use the real float scores already stored there:

| Column in `analysis_log` | Agent |
|--------------------------|-------|
| `score_pitching`         | pitching |
| `score_offense`          | offense |
| `score_bullpen`          | bullpen |
| `score_advanced`         | advanced |
| `score_momentum`         | momentum |
| `score_weather`          | weather |
| `score_market`           | market |

The differential formula stays the same: `avg_score_on_won_picks - avg_score_on_lost_picks`. Only the data source changes. Drop all regex extraction logic.

**Verified:** 3-way join resolves all 16/16 graded picks. Sample scores are real floats: `score_pitching=0.467`, `score_bullpen=0.794`, etc.

**No schema changes required.**

---

## Bug B: Optimizer reliability — three issues

### B1: Queue item already-done detection

**Problem:** The optimizer selected `batter_game_logs` even though it was already implemented (table, functions, analysis signal, tests all exist). `code_queue` selection logic only checks COMPLETED_IMPROVEMENTS.md via `mark_complete()` after a successful run — it doesn't check upfront whether an item is already registered.

**Fix:** In `select_improvement()`, before iterating the `code_queue`, scan `COMPLETED_IMPROVEMENTS.md` for each item's `<!-- id: X -->` marker. If found, skip it. This is a simple `f"<!-- id: {imp_id} -->"` string search on the file contents.

### B2: Zero-diff treated as success

**Problem:** If Claude CLI returns exit code 0 but makes no file changes, `apply_via_claude()` returns `{"success": True}` and the caller proceeds to commit and mark complete. The commit silently fails (nothing to commit), leaving the queue item unresolved.

**Fix:** After `apply_via_claude()` returns success, run `git diff --name-only HEAD` and check if it's non-empty. If empty (no files changed), return `{"success": False, "error": "Claude ran but made no changes"}` — treated as a skip, not a failure. Log clearly so the Discord report reflects it.

### B3: Mark `batter_game_logs` complete

**Problem:** The feature is fully implemented across database.py, analysis.py, engine.py, and tests/test_batter_logs.py — but COMPLETED_IMPROVEMENTS.md has no `<!-- id: batter_game_logs -->` entry, so it will be re-selected every night.

**Fix:** Append the entry to COMPLETED_IMPROVEMENTS.md now, with accurate commit reference and summary.

---

## What is NOT changing

- No schema changes to `picks` or `analysis_log`
- No changes to how edge strings are generated or stored
- No changes to the blending formula or backtest lift loading
- No changes to the Claude CLI dispatch flow beyond the zero-diff check
- No changes to weight/threshold tuning logic

---

## Files Changed

| File | Change |
|------|--------|
| `optimizer.py` | Rewrite `analyze_agent_signals()` to join analysis_log; add already-done check in `select_improvement()`; add zero-diff guard in main dispatch |
| `COMPLETED_IMPROVEMENTS.md` | Add `batter_game_logs` entry |

---

## Testing

- `analyze_agent_signals()` should return reasonable differentials (small numbers, not ±3) with 16 graded picks
- Manually confirm `batter_game_logs` no longer appears as a queue candidate
- Run full test suite (`pytest tests/ -v`) — no regressions expected since query logic is internal to optimizer
