# Weather Storage in game_totals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store actual game-time weather (temp, wind speed, wind direction) in `game_totals` for every completed game, enabling O/U bias analysis by weather condition.

**Architecture:** Four changes — two new DB helper functions (`update_game_total_weather`, `get_game_totals_missing_weather`), fix `collect_game_totals()` to extract venue/time from the API response it already fetches and call `fetch_venue_weather()`, and add `backfill_game_totals_weather()` to patch the 200 existing NULL rows using MLB API + Open-Meteo historical data.

**Tech Stack:** Python 3.9, SQLite (`mlb_picks.db`), Open-Meteo API (already integrated via `fetch_venue_weather()`), MLB Stats API, pytest, `unittest.mock`

---

## Codebase Facts (read before touching anything)

- `database.py` uses **module-level functions** with `get_connection()` per call — NOT a class
- `import database as db` in `data_mlb.py` — all DB calls use `db.` prefix
- `fetch_venue_weather(venue_id, game_date, game_time_utc)` already exists in `data_mlb.py` at line 1264
- `collect_game_totals()` is in `data_mlb.py` lines 1390–1476; hardcoded `None` weather at lines 1470–1472
- `MLB_BASE` constant is already defined in `data_mlb.py` — use it for API URLs
- `requests` is already imported in `data_mlb.py`
- New DB tests use `monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))` then `database.init_db()` — see `tests/test_database.py` for examples
- Python 3.9 — no `float | None` union syntax, use `Optional[float]`
- Run tests with `python3 -m pytest` (not `python`)

---

## File Map

| File | Change |
|------|--------|
| `database.py` | Add `update_game_total_weather()` after `backfill_game_totals_abbr()` (~line 1731); add `get_game_totals_missing_weather()` after that |
| `data_mlb.py` | Fix lines 1470–1472 in `collect_game_totals()`; add `venue_id`/`game_time_utc` extraction; add `backfill_game_totals_weather()` before `__main__` block |
| `tests/test_database.py` | Add tests for `update_game_total_weather` and `get_game_totals_missing_weather` |
| `tests/test_data_mlb_weather.py` | Create new file for `collect_game_totals` weather tests and `backfill_game_totals_weather` tests |

---

## Task 1: Add `update_game_total_weather()` and `get_game_totals_missing_weather()` to `database.py`

**Files:**
- Modify: `database.py` (insert after `backfill_game_totals_abbr()` which ends ~line 1730)
- Test: `tests/test_database.py` (add to existing file)

- [ ] **Step 1: Write failing tests — add to `tests/test_database.py`**

Read the existing `tests/test_database.py` first to confirm the `monkeypatch`/`tmp_path` pattern, then add at the bottom:

```python
def test_update_game_total_weather(monkeypatch, tmp_path):
    """update_game_total_weather writes temp_f, wind_mph, wind_dir to correct row."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.DATABASE_PATH", db_path)
    import database
    database.init_db()

    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date, home_team_abbr, away_team_abbr)
        VALUES (888001, '2026-04-15', 'DET', 'KC')
    """)
    conn.commit()
    conn.close()

    database.update_game_total_weather(888001, 64.8, 8.9, "SW")

    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 888001"
    ).fetchone()
    conn.close()
    assert row[0] == 64.8
    assert row[1] == 8.9
    assert row[2] == "SW"


def test_update_game_total_weather_none_values(monkeypatch, tmp_path):
    """update_game_total_weather accepts None values without error."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.DATABASE_PATH", db_path)
    import database
    database.init_db()

    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date, home_team_abbr, away_team_abbr)
        VALUES (888002, '2026-04-15', 'DET', 'KC')
    """)
    conn.commit()
    conn.close()

    database.update_game_total_weather(888002, None, None, None)

    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 888002"
    ).fetchone()
    conn.close()
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None


def test_get_game_totals_missing_weather_returns_null_rows(monkeypatch, tmp_path):
    """get_game_totals_missing_weather returns only rows where temp_f IS NULL."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.DATABASE_PATH", db_path)
    import database
    database.init_db()

    conn = database.get_connection()
    # Row with NULL temp_f — should be returned
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date)
        VALUES (888003, '2026-04-14')
    """)
    # Row with populated temp_f — should NOT be returned
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date, temp_f, wind_mph, wind_dir)
        VALUES (888004, '2026-04-14', 72.0, 5.0, 'N')
    """)
    conn.commit()
    conn.close()

    rows = database.get_game_totals_missing_weather()
    game_ids = [r[0] for r in rows]
    assert 888003 in game_ids
    assert 888004 not in game_ids


def test_get_game_totals_missing_weather_returns_tuple_of_id_and_date(monkeypatch, tmp_path):
    """Each row returned is (mlb_game_id, game_date)."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.DATABASE_PATH", db_path)
    import database
    database.init_db()

    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date)
        VALUES (888005, '2026-04-13')
    """)
    conn.commit()
    conn.close()

    rows = database.get_game_totals_missing_weather()
    match = [r for r in rows if r[0] == 888005]
    assert len(match) == 1
    assert match[0][1] == '2026-04-13'
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_database.py::test_update_game_total_weather tests/test_database.py::test_update_game_total_weather_none_values tests/test_database.py::test_get_game_totals_missing_weather_returns_null_rows tests/test_database.py::test_get_game_totals_missing_weather_returns_tuple_of_id_and_date -v
```

Expected: `AttributeError: module 'database' has no attribute 'update_game_total_weather'`

- [ ] **Step 3: Add both functions to `database.py`**

Find `backfill_game_totals_abbr()` which ends around line 1730. Insert these two functions immediately after it:

```python
def update_game_total_weather(mlb_game_id: int, temp_f, wind_mph, wind_dir) -> None:
    """Update weather fields for a single game_totals row."""
    conn = get_connection()
    conn.execute(
        "UPDATE game_totals SET temp_f=?, wind_mph=?, wind_dir=? WHERE mlb_game_id=?",
        (temp_f, wind_mph, wind_dir, mlb_game_id)
    )
    conn.commit()
    conn.close()


def get_game_totals_missing_weather():
    """Return list of (mlb_game_id, game_date) for rows where temp_f IS NULL."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT mlb_game_id, game_date FROM game_totals WHERE temp_f IS NULL ORDER BY game_date"
    ).fetchall()
    conn.close()
    return rows
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python3 -m pytest tests/test_database.py -v 2>&1 | tail -20
```

Expected: All tests PASSED (11 total — 7 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add update_game_total_weather and get_game_totals_missing_weather to database.py"
```

---

## Task 2: Fix `collect_game_totals()` to fetch and store weather

**Files:**
- Modify: `data_mlb.py` lines ~1413–1472
- Test: `tests/test_data_mlb_weather.py` (create new file)

- [ ] **Step 1: Write failing tests — create `tests/test_data_mlb_weather.py`**

```python
from unittest.mock import patch, MagicMock
import pytest


FAKE_SCHEDULE_RESPONSE = {
    "dates": [{
        "games": [{
            "gamePk": 999001,
            "gameDate": "2026-04-15T17:10:00Z",
            "status": {"abstractGameState": "Final"},
            "venue": {"id": 2394},
            "teams": {
                "away": {"team": {"id": 118}, "score": 2},
                "home": {"team": {"id": 116}, "score": 5},
            },
            "linescore": {
                "currentInning": 9,
                "innings": []
            }
        }]
    }]
}

FAKE_WEATHER = {
    "temp_f": 64.8,
    "wind_mph": 8.9,
    "wind_dir": "SW",
    "precip_chance": 47,
    "conditions": "Drizzle",
}


def test_collect_game_totals_stores_weather(monkeypatch, tmp_path):
    """collect_game_totals writes temp_f, wind_mph, wind_dir into stored records."""
    import database
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_db()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = FAKE_SCHEDULE_RESPONSE

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather", return_value=FAKE_WEATHER) as mock_weather, \
         patch("data_mlb.db.store_game_totals") as mock_store:

        from data_mlb import collect_game_totals
        collect_game_totals("2026-04-15")

        assert mock_weather.called
        call_args = mock_store.call_args[0][0]
        assert len(call_args) > 0
        record = call_args[0]
        assert record["temp_f"] == 64.8
        assert record["wind_mph"] == 8.9
        assert record["wind_dir"] == "SW"


def test_collect_game_totals_weather_fetch_failure_stores_none(monkeypatch, tmp_path):
    """If fetch_venue_weather raises, weather fields stored as None (no crash)."""
    import database
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_db()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = FAKE_SCHEDULE_RESPONSE

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather", side_effect=Exception("API down")), \
         patch("data_mlb.db.store_game_totals") as mock_store:

        from data_mlb import collect_game_totals
        collect_game_totals("2026-04-15")

        call_args = mock_store.call_args[0][0]
        record = call_args[0]
        assert record["temp_f"] is None
        assert record["wind_mph"] is None
        assert record["wind_dir"] is None


def test_collect_game_totals_missing_venue_skips_weather(monkeypatch, tmp_path):
    """If venue.id is missing from API response, weather fields are None."""
    import database
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_db()

    no_venue_response = {
        "dates": [{
            "games": [{
                "gamePk": 999002,
                "gameDate": "2026-04-15T17:10:00Z",
                "status": {"abstractGameState": "Final"},
                # No "venue" key
                "teams": {
                    "away": {"team": {"id": 118}, "score": 2},
                    "home": {"team": {"id": 116}, "score": 5},
                },
                "linescore": {"currentInning": 9, "innings": []}
            }]
        }]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = no_venue_response

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather") as mock_weather, \
         patch("data_mlb.db.store_game_totals") as mock_store:

        from data_mlb import collect_game_totals
        collect_game_totals("2026-04-15")

        assert not mock_weather.called
        call_args = mock_store.call_args[0][0]
        record = call_args[0]
        assert record["temp_f"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/test_data_mlb_weather.py -v
```

Expected: Tests fail because `collect_game_totals` doesn't call `fetch_venue_weather` yet.

- [ ] **Step 3: Read current lines 1390–1476 of `data_mlb.py`**

Before editing, read the function to understand exact current structure:

```bash
python3 -c "
with open('data_mlb.py') as f:
    lines = f.readlines()
for i, line in enumerate(lines[1389:1476], start=1390):
    print(f'{i}: {line}', end='')
"
```

- [ ] **Step 4: Add venue_id and game_time_utc extraction**

After the existing team ID extractions (around lines 1418–1421 where `away_team_id` and `home_team_id` are assigned), add:

```python
venue_id = game.get("venue", {}).get("id")
game_time_utc = game.get("gameDate", "")
```

- [ ] **Step 5: Add weather fetch before the record dict**

After extracting `venue_id` and `game_time_utc`, add the weather fetch block:

```python
weather = {}
if venue_id:
    try:
        weather = fetch_venue_weather(venue_id, game_date, game_time_utc) or {}
    except Exception as e:
        print(f"[WEATHER] Failed to fetch weather for game {game_pk}: {e}")
        weather = {}
```

- [ ] **Step 6: Replace hardcoded None weather lines (1470–1472)**

Find these three lines:
```python
"temp_f": None,
"wind_mph": None,
"wind_dir": None,
```

Replace with:
```python
"temp_f": weather.get("temp_f"),
"wind_mph": weather.get("wind_mph"),
"wind_dir": weather.get("wind_dir"),
```

- [ ] **Step 7: Run weather tests to confirm they pass**

```bash
python3 -m pytest tests/test_data_mlb_weather.py -v
```

Expected: 3 PASSED

- [ ] **Step 8: Run full test suite to confirm no regressions**

```bash
python3 -m pytest tests/ --tb=short 2>&1 | tail -10
```

Expected: 216 passed (213 existing + 3 new)

- [ ] **Step 9: Commit**

```bash
git add data_mlb.py tests/test_data_mlb_weather.py
git commit -m "feat: collect_game_totals now fetches and stores weather from Open-Meteo"
```

---

## Task 3: Add `backfill_game_totals_weather()` to `data_mlb.py`

**Files:**
- Modify: `data_mlb.py` (add function before end of file)
- Test: `tests/test_data_mlb_weather.py` (add tests)

- [ ] **Step 1: Write failing tests — add to `tests/test_data_mlb_weather.py`**

```python
def test_backfill_game_totals_weather_updates_null_rows(monkeypatch, tmp_path):
    """backfill_game_totals_weather fetches weather for NULL rows and saves them."""
    import database
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_db()

    # Insert a row with NULL weather
    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date)
        VALUES (777001, '2026-04-14')
    """)
    conn.commit()
    conn.close()

    fake_sched = {
        "dates": [{
            "games": [{
                "gamePk": 777001,
                "gameDate": "2026-04-14T17:05:00Z",
                "venue": {"id": 2394},
            }]
        }]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_sched

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.fetch_venue_weather", return_value=FAKE_WEATHER), \
         patch("data_mlb.time.sleep"):  # skip sleep in tests

        from data_mlb import backfill_game_totals_weather
        count = backfill_game_totals_weather()

    assert count == 1
    conn = database.get_connection()
    row = conn.execute(
        "SELECT temp_f, wind_mph, wind_dir FROM game_totals WHERE mlb_game_id = 777001"
    ).fetchone()
    conn.close()
    assert row[0] == 64.8
    assert row[1] == 8.9
    assert row[2] == "SW"


def test_backfill_game_totals_weather_skips_populated_rows(monkeypatch, tmp_path):
    """backfill_game_totals_weather does not touch rows that already have temp_f."""
    import database
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_db()

    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date, temp_f, wind_mph, wind_dir)
        VALUES (777002, '2026-04-14', 55.0, 3.0, 'N')
    """)
    conn.commit()
    conn.close()

    with patch("data_mlb.requests.get") as mock_get, \
         patch("data_mlb.fetch_venue_weather") as mock_weather:

        from data_mlb import backfill_game_totals_weather
        count = backfill_game_totals_weather()

    assert count == 0
    assert not mock_get.called
    assert not mock_weather.called


def test_backfill_game_totals_weather_handles_api_failure(monkeypatch, tmp_path):
    """backfill_game_totals_weather continues on per-game failure, returns 0."""
    import database
    monkeypatch.setattr("database.DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_db()

    conn = database.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO game_totals (mlb_game_id, game_date)
        VALUES (777003, '2026-04-14')
    """)
    conn.commit()
    conn.close()

    mock_resp = MagicMock()
    mock_resp.status_code = 500

    with patch("data_mlb.requests.get", return_value=mock_resp), \
         patch("data_mlb.time.sleep"):

        from data_mlb import backfill_game_totals_weather
        count = backfill_game_totals_weather()

    assert count == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/test_data_mlb_weather.py::test_backfill_game_totals_weather_updates_null_rows tests/test_data_mlb_weather.py::test_backfill_game_totals_weather_skips_populated_rows tests/test_data_mlb_weather.py::test_backfill_game_totals_weather_handles_api_failure -v
```

Expected: `ImportError` or `AttributeError` — function doesn't exist yet.

- [ ] **Step 3: Add `backfill_game_totals_weather()` to `data_mlb.py`**

Find the end of `data_mlb.py` (the `if __name__ == "__main__":` block or end of file). Add the function before it. Note: `import time` goes at the top of the function to avoid module-level import changes.

```python
def backfill_game_totals_weather() -> int:
    """
    Backfill temp_f, wind_mph, wind_dir for game_totals rows where temp_f IS NULL.
    Fetches game time from MLB API, then weather from Open-Meteo historical data.
    Idempotent — skips rows already populated. Rate-limited to 1 req/sec.
    Returns count of rows successfully updated.
    """
    import time
    rows = db.get_game_totals_missing_weather()
    if not rows:
        print("[WEATHER BACKFILL] No rows to update.")
        return 0

    updated = 0
    for mlb_game_id, game_date in rows:
        try:
            sched_url = f"{MLB_BASE}/schedule?gamePks={mlb_game_id}"
            resp = requests.get(sched_url, timeout=10)
            if resp.status_code != 200:
                print(f"[WEATHER BACKFILL] Schedule API error {resp.status_code} for game {mlb_game_id}")
                time.sleep(1.0)
                continue

            dates = resp.json().get("dates", [])
            if not dates or not dates[0].get("games"):
                print(f"[WEATHER BACKFILL] No game data for {mlb_game_id}")
                time.sleep(1.0)
                continue

            game_info = dates[0]["games"][0]
            venue_id = game_info.get("venue", {}).get("id")
            game_time_utc = game_info.get("gameDate", "")

            if not venue_id:
                print(f"[WEATHER BACKFILL] No venue_id for game {mlb_game_id}")
                time.sleep(1.0)
                continue

            weather = fetch_venue_weather(venue_id, game_date, game_time_utc) or {}
            if weather.get("temp_f") is not None:
                db.update_game_total_weather(
                    mlb_game_id,
                    weather.get("temp_f"),
                    weather.get("wind_mph"),
                    weather.get("wind_dir"),
                )
                updated += 1
            time.sleep(1.0)

        except Exception as e:
            print(f"[WEATHER BACKFILL] Failed for game {mlb_game_id}: {e}")
            continue

    print(f"[WEATHER BACKFILL] Updated {updated}/{len(rows)} rows")
    return updated
```

- [ ] **Step 4: Run all weather tests**

```bash
python3 -m pytest tests/test_data_mlb_weather.py -v
```

Expected: 6 PASSED

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ --tb=short 2>&1 | tail -10
```

Expected: 219 passed

- [ ] **Step 6: Commit**

```bash
git add data_mlb.py tests/test_data_mlb_weather.py
git commit -m "feat: add backfill_game_totals_weather() to populate historical weather data"
```

---

## Task 4: Run backfill against real DB + final verification

**Files:** None (runtime only)

- [ ] **Step 1: Run the backfill against the real database**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -c "
from data_mlb import backfill_game_totals_weather
backfill_game_totals_weather()
"
```

Expected output: `[WEATHER BACKFILL] Updated N/200 rows` where N is ideally close to 200. Some games may not have venue data and will be skipped — that's acceptable.

Note: This will take ~3–4 minutes (200 rows × 1 sec sleep). Let it run.

- [ ] **Step 2: Verify real DB coverage**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('mlb_picks.db')
total = conn.execute('SELECT COUNT(*) FROM game_totals').fetchone()[0]
filled = conn.execute('SELECT COUNT(*) FROM game_totals WHERE temp_f IS NOT NULL').fetchone()[0]
sample = conn.execute('SELECT game_date, home_team_abbr, away_team_abbr, temp_f, wind_mph, wind_dir FROM game_totals WHERE temp_f IS NOT NULL ORDER BY game_date DESC LIMIT 5').fetchall()
conn.close()
print(f'Weather coverage: {filled}/{total}')
print('Recent sample:')
for r in sample:
    print(f'  {r[0]}  {r[2]} @ {r[1]}  {r[3]}°F  {r[4]}mph {r[5]}')
"
```

Expected: Weather coverage 150+/200 with real values like `64.8°F 8.9mph SW`.

- [ ] **Step 3: Run full test suite one final time**

```bash
python3 -m pytest tests/ --tb=short 2>&1 | tail -10
```

Expected: 219 passed

- [ ] **Step 4: Commit if any loose files**

```bash
git status
# If clean, nothing to do.
```
