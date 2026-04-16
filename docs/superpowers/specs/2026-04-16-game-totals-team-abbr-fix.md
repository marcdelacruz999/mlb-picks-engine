# Spec: Fix `game_totals` Team Abbreviation Gap

**Date:** 2026-04-16  
**Status:** Approved  
**Scope:** `data_mlb.py`, `database.py`

---

## Problem

All 197 rows in `game_totals` have empty strings (`""`) for `home_team_abbr` and `away_team_abbr`. This means the O/U bias analysis in `calibrate.py` cannot break down performance by team or park — a key dimension for diagnosing model bias.

**Root cause:** `collect_game_totals()` in `data_mlb.py` queries the `/schedule?hydrate=linescore` MLB API endpoint, which only returns `team_id` (MLB API integer ID), not `abbreviation`. The line `.get("abbreviation", "")` always defaults to empty string because the field is absent from that endpoint's response.

---

## Solution: Approach A — Join against local `teams` table

The `teams` table in `mlb_picks.db` already has `mlb_id → abbreviation` for all 30 teams. `collect_game_totals()` already has `home_team_id` and `away_team_id` at the point of record construction. The fix is to look up abbreviations from the `teams` table using those IDs.

This approach uses data that already exists locally, avoids hardcoding, and stays in sync if a franchise ever changes abbreviation.

---

## Changes

### 1. `database.py` — Add `get_team_abbr_by_mlb_id()`

A new helper function that accepts an MLB API team ID and returns the abbreviation string (or `""` if not found).

```python
def get_team_abbr_by_mlb_id(self, mlb_id: int) -> str:
    row = self._conn.execute(
        "SELECT abbreviation FROM teams WHERE mlb_id = ?", (mlb_id,)
    ).fetchone()
    return row[0] if row else ""
```

### 2. `data_mlb.py` — Fix `collect_game_totals()`

Replace the broken `.get("abbreviation", "")` calls with lookups via the new helper:

```python
# Before (broken):
away_abbr = away_info.get("abbreviation", "")
home_abbr = home_info.get("abbreviation", "")

# After (fixed):
away_abbr = _db.get_team_abbr_by_mlb_id(away_team_id) if away_team_id else ""
home_abbr = _db.get_team_abbr_by_mlb_id(home_team_id) if home_team_id else ""
```

### 3. `database.py` — Add `backfill_game_totals_abbr()`

A one-time backfill function that updates all existing rows with empty abbreviations by joining `game_totals` to `teams` on `mlb_id`:

```python
def backfill_game_totals_abbr(self) -> int:
    """
    Backfill home_team_abbr and away_team_abbr for all game_totals rows
    where the abbreviation is currently empty. Joins against the teams table.
    Returns count of rows updated.
    """
    self._conn.execute("""
        UPDATE game_totals
        SET home_team_abbr = (
            SELECT abbreviation FROM teams WHERE mlb_id = game_totals.home_team_id
        )
        WHERE (home_team_abbr IS NULL OR home_team_abbr = '')
          AND home_team_id IS NOT NULL
    """)
    self._conn.execute("""
        UPDATE game_totals
        SET away_team_abbr = (
            SELECT abbreviation FROM teams WHERE mlb_id = game_totals.away_team_id
        )
        WHERE (away_team_abbr IS NULL OR away_team_abbr = '')
          AND away_team_id IS NOT NULL
    """)
    self._conn.commit()
    row = self._conn.execute(
        "SELECT COUNT(*) FROM game_totals WHERE home_team_abbr != '' AND away_team_abbr != ''"
    ).fetchone()
    return row[0] if row else 0
```

### 4. `database.py` — Call `backfill_game_totals_abbr()` from `init_db()`

Add a single call at the end of `init_db()` so the backfill runs automatically on next startup, then becomes a no-op (WHERE clause filters out already-populated rows):

```python
self.backfill_game_totals_abbr()
```

---

## Data Flow After Fix

```
collect_game_totals(date)
  → MLB /schedule API → gets home_team_id, away_team_id
  → db.get_team_abbr_by_mlb_id(home_team_id) → "LAD"
  → db.get_team_abbr_by_mlb_id(away_team_id) → "SF"
  → store_game_totals([{home_team_abbr: "LAD", away_team_abbr: "SF", ...}])
```

On first run after deploy:
```
init_db()
  → backfill_game_totals_abbr()
  → UPDATE 197 rows with correct abbreviations
  → subsequent calls: no-ops (WHERE clause filters populated rows)
```

---

## Error Handling

- If `mlb_id` not found in `teams` table: returns `""` (same as current behavior, no regression)
- Backfill UPDATE is a no-op for rows already populated (idempotent)
- No changes to pick analysis logic or O/U model

---

## Testing

- Verify `get_team_abbr_by_mlb_id(143)` returns `"PHI"` (or correct abbr for that ID)
- After backfill: `SELECT COUNT(*) FROM game_totals WHERE home_team_abbr = ''` should return 0
- Run existing test suite — no tests should break (no analysis logic touched)
- `collect_game_totals()` for a date with known games should produce non-empty abbreviations

---

## Out of Scope

- No changes to `analysis.py`, `engine.py`, `calibrate.py`, or any pick logic
- No schema migration needed (columns already exist)
- No changes to `update_game_total_projection()` — the analysis path already passes abbr correctly when available
