# MLB Picks Engine — Testing Reference

## Run Tests

```bash
python3 -m pytest tests/ -v --tb=short
```

Currently: 123 passing, 3 pre-existing failures (do not fix unless explicitly tasked).

## Pre-existing Failures (ignore)

1. `tests/test_analysis_log.py::test_run_results_grades_analysis_log` — fixture issue, unrelated to picks logic
2. `tests/test_analysis_log.py::test_get_today_analysis_log` — hardcoded date "2026-04-11", fails after date rollover
3. `tests/test_rolling_stats.py::test_collect_boxscores_returns_pitcher_and_team_logs` — mock parsing issue

## Mock Patch Target

If a function is imported at module level in `analysis.py`:
```python
# CORRECT
@patch("analysis.fetch_lineup_batting")

# WRONG — won't intercept the already-imported reference
@patch("data_mlb.fetch_lineup_batting")
```

Same rule applies to any function imported with `from data_mlb import ...` in analysis.py.

## DB in Tests

- Use `pytest` fixtures with a temporary SQLite DB — never write to `mlb_picks.db` in tests
- Migration pattern: `except sqlite3.OperationalError: pass` (not bare `except Exception`)

## Optimizer: Test Gate

Optimizer runs `pytest` after every code change. On failure → `git_revert()` is called automatically.
Tests must pass before any change is committed or marked complete.

## DB Test Pattern

```python
monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
database.init_db()
conn = database.get_connection()  # reads DB_PATH, not DATABASE_PATH
```
