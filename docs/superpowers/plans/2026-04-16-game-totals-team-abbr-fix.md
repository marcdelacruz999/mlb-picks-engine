# game_totals Team Abbreviation Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `home_team_abbr` and `away_team_abbr` in all `game_totals` rows (197 existing + all future) by looking up abbreviations from the local `teams` table using `home_team_id`/`away_team_id`.

**Architecture:** Add a `get_team_abbr_by_mlb_id()` helper to `database.py`, fix the two broken `.get("abbreviation", "")` calls in `collect_game_totals()` to use the helper, and add a `backfill_game_totals_abbr()` function that auto-runs from `init_db()` as a one-time idempotent backfill.

**Tech Stack:** Python 3.9, SQLite (`mlb_picks.db`), pytest

---

## File Map

| File | Change |
|------|--------|
| `database.py` | Add `get_team_abbr_by_mlb_id()` helper (~line 1545, before `store_game_totals`); add `backfill_game_totals_abbr()` after `update_game_total_projection` (~line 1661+); call backfill at end of `init_db()` (line 470) |
| `data_mlb.py` | Fix lines 1421–1422 in `collect_game_totals()` to use `get_team_abbr_by_mlb_id()` |
| `tests/test_database.py` | Create new file — tests for `get_team_abbr_by_mlb_id()` and `backfill_game_totals_abbr()` |

---

## Task 1: Add `get_team_abbr_by_mlb_id()` to `database.py`

**Files:**
- Modify: `database.py` (insert before line 1546 where `store_game_totals` starts)
- Test: `tests/test_database.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_database.py`:

```python
import sqlite3
import pytest
from database import Database


@pytest.fixture
def db(tmp_path):
    """In-memory DB with teams table populated."""
    db_path = str(tmp_path / "test.db")
    d = Database(db_path)
    d.init_db()
    # Insert two known teams
    d._conn.execute(
        "INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (143, 'Philadelphia Phillies', 'PHI')"
    )
    d._conn.execute(
        "INSERT OR IGNORE INTO teams (mlb_id, name, abbreviation) VALUES (119, 'Los Angeles Dodgers', 'LAD')"
    )
    d._conn.commit()
    return d


def test_get_team_abbr_known_id(db):
    assert db.get_team_abbr_by_mlb_id(143) == "PHI"


def test_get_team_abbr_another_known_id(db):
    assert db.get_team_abbr_by_mlb_id(119) == "LAD"


def test_get_team_abbr_unknown_id_returns_empty(db):
    assert db.get_team_abbr_by_mlb_id(9999) == ""


def test_get_team_abbr_none_id_returns_empty(db):
    assert db.get_team_abbr_by_mlb_id(None) == ""
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python -m pytest tests/test_database.py -v
```

Expected: `AttributeError: 'Database' object has no attribute 'get_team_abbr_by_mlb_id'`

- [ ] **Step 3: Add `get_team_abbr_by_mlb_id()` to `database.py`**

In `database.py`, insert this method just before the `store_game_totals` definition (before line 1546). Follow the existing method pattern in the class:

```python
def get_team_abbr_by_mlb_id(self, mlb_id) -> str:
    """Return team abbreviation for the given MLB API team ID, or '' if not found."""
    if mlb_id is None:
        return ""
    row = self._conn.execute(
        "SELECT abbreviation FROM teams WHERE mlb_id = ?", (mlb_id,)
    ).fetchone()
    return row[0] if row and row[0] else ""
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_database.py::test_get_team_abbr_known_id tests/test_database.py::test_get_team_abbr_another_known_id tests/test_database.py::test_get_team_abbr_unknown_id_returns_empty tests/test_database.py::test_get_team_abbr_none_id_returns_empty -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_database.py database.py
git commit -m "feat: add get_team_abbr_by_mlb_id() helper to database.py"
```

---

## Task 2: Fix `collect_game_totals()` in `data_mlb.py`

**Files:**
- Modify: `data_mlb.py` lines 1421–1422

- [ ] **Step 1: Open `data_mlb.py` and locate the broken lines**

Lines 1421–1422 currently read:
```python
away_abbr = away_info.get("abbreviation", "")
home_abbr = home_info.get("abbreviation", "")
```

These are inside `collect_game_totals()`. The variable `_db` (or the database instance) is already in scope — confirm what name the database instance uses in that function by checking lines 1390–1420.

- [ ] **Step 2: Replace the broken abbreviation lookups**

Replace lines 1421–1422 with:

```python
away_abbr = _db.get_team_abbr_by_mlb_id(away_team_id)
home_abbr = _db.get_team_abbr_by_mlb_id(home_team_id)
```

> Note: If the database instance in `collect_game_totals()` is named something other than `_db` (e.g. `db` or `database`), use that name instead. Check lines 1390–1410 for the variable name.

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: All previously passing tests still pass. The 3 pre-existing failures (documented in `docs/testing.md`) are acceptable.

- [ ] **Step 4: Commit**

```bash
git add data_mlb.py
git commit -m "fix: collect_game_totals uses teams table for abbr lookup instead of missing API field"
```

---

## Task 3: Add `backfill_game_totals_abbr()` and wire into `init_db()`

**Files:**
- Modify: `database.py` (add method after `update_game_total_projection` ~line 1661+; call from `init_db()` at line 470)
- Test: `tests/test_database.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_database.py`:

```python
def test_backfill_game_totals_abbr_populates_empty_rows(db):
    """Rows with empty abbr strings get populated from teams table."""
    # Insert a game_totals row with empty abbr but valid team IDs
    db._conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (999001, '2026-04-15', 143, 119, '', '')
    """)
    db._conn.commit()

    count = db.backfill_game_totals_abbr()

    row = db._conn.execute(
        "SELECT home_team_abbr, away_team_abbr FROM game_totals WHERE mlb_game_id = 999001"
    ).fetchone()
    assert row[0] == "PHI"
    assert row[1] == "LAD"
    assert count >= 1


def test_backfill_game_totals_abbr_skips_populated_rows(db):
    """Rows already having abbreviations are not touched."""
    db._conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (999002, '2026-04-15', 143, 119, 'PHI', 'LAD')
    """)
    db._conn.commit()

    db.backfill_game_totals_abbr()

    row = db._conn.execute(
        "SELECT home_team_abbr, away_team_abbr FROM game_totals WHERE mlb_game_id = 999002"
    ).fetchone()
    assert row[0] == "PHI"
    assert row[1] == "LAD"


def test_backfill_game_totals_abbr_handles_null_abbr(db):
    """Rows with NULL abbr (not empty string) also get populated."""
    db._conn.execute("""
        INSERT OR IGNORE INTO game_totals
            (mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr)
        VALUES (999003, '2026-04-15', 143, 119, NULL, NULL)
    """)
    db._conn.commit()

    db.backfill_game_totals_abbr()

    row = db._conn.execute(
        "SELECT home_team_abbr, away_team_abbr FROM game_totals WHERE mlb_game_id = 999003"
    ).fetchone()
    assert row[0] == "PHI"
    assert row[1] == "LAD"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_database.py::test_backfill_game_totals_abbr_populates_empty_rows tests/test_database.py::test_backfill_game_totals_abbr_skips_populated_rows tests/test_database.py::test_backfill_game_totals_abbr_handles_null_abbr -v
```

Expected: `AttributeError: 'Database' object has no attribute 'backfill_game_totals_abbr'`

- [ ] **Step 3: Add `backfill_game_totals_abbr()` to `database.py`**

Insert this method after `update_game_total_projection()` (after ~line 1661):

```python
def backfill_game_totals_abbr(self) -> int:
    """
    Backfill home_team_abbr and away_team_abbr for all game_totals rows
    where the abbreviation is currently empty or NULL. Joins against the
    teams table using mlb_id. Idempotent — safe to call repeatedly.
    Returns count of rows now having both abbreviations populated.
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

- [ ] **Step 4: Run backfill tests to confirm they pass**

```bash
python -m pytest tests/test_database.py -v
```

Expected: All tests PASSED

- [ ] **Step 5: Wire `backfill_game_totals_abbr()` into `init_db()`**

In `database.py`, find line 470 (the last line of `init_db()`, which currently reads `print("[DB] Database initialized.")`).

Add the backfill call just before that print:

```python
    self.backfill_game_totals_abbr()
    print("[DB] Database initialized.")
```

- [ ] **Step 6: Run the full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: All previously passing tests still pass.

- [ ] **Step 7: Verify the backfill works against the real DB**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -c "
from database import Database
db = Database('mlb_picks.db')
count = db.backfill_game_totals_abbr()
print(f'Rows with abbr populated: {count}')

import sqlite3
conn = sqlite3.connect('mlb_picks.db')
empty = conn.execute(\"SELECT COUNT(*) FROM game_totals WHERE home_team_abbr = '' OR home_team_abbr IS NULL\").fetchone()[0]
sample = conn.execute('SELECT mlb_game_id, game_date, home_team_abbr, away_team_abbr FROM game_totals LIMIT 5').fetchall()
print(f'Rows still empty: {empty}')
print('Sample rows:')
for r in sample:
    print(r)
conn.close()
"
```

Expected output:
```
Rows with abbr populated: 197
Rows still empty: 0
Sample rows:
(...)  # each row shows real abbr like LAD, SF, NYY etc
```

- [ ] **Step 8: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: backfill game_totals team abbreviations from teams table on init_db"
```

---

## Task 4: Final verification

- [ ] **Step 1: Run the full test suite one last time**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: All previously passing tests still pass. New `test_database.py` tests all PASSED.

- [ ] **Step 2: Confirm real DB state**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('mlb_picks.db')
total = conn.execute('SELECT COUNT(*) FROM game_totals').fetchone()[0]
with_abbr = conn.execute(\"SELECT COUNT(*) FROM game_totals WHERE home_team_abbr != '' AND away_team_abbr != ''\").fetchone()[0]
print(f'Total game_totals rows: {total}')
print(f'Rows with both abbreviations: {with_abbr}')
print(f'Coverage: {with_abbr}/{total}')
conn.close()
"
```

Expected:
```
Total game_totals rows: 197
Rows with both abbreviations: 197
Coverage: 197/197
```

- [ ] **Step 3: Final commit if any loose files**

```bash
git status
# If clean, nothing to do. If anything unstaged:
git add -A
git commit -m "chore: final cleanup for game_totals abbr fix"
```
