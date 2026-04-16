# Spec: Weather Storage in game_totals (Phase 1)

**Date:** 2026-04-16  
**Status:** Approved  
**Scope:** `data_mlb.py`, `database.py`

---

## Problem

`game_totals` has `temp_f`, `wind_mph`, `wind_dir` columns that are always NULL. `collect_game_totals()` hardcodes these to `None` (lines 1470–1472) because `venue_id` and `game_time_utc` were never extracted from the schedule API response. Without historical weather, the O/U bias tracker can't analyze weather effects on outcomes, and the 5% weather signal weight can never be calibrated.

---

## Solution

Three targeted changes:

1. **`data_mlb.py` — fix `collect_game_totals()`** — extract `venue_id` and `game_time_utc` from the already-fetched schedule API response, call `fetch_venue_weather()`, and write the result into each game record.

2. **`database.py` — add `update_game_total_weather()`** — a minimal UPDATE function for patching weather fields on existing rows.

3. **`data_mlb.py` — add `backfill_game_totals_weather()`** — iterates all `game_totals` rows where `temp_f IS NULL`, fetches game time from MLB API, calls `fetch_venue_weather()`, and saves results. One-time use, idempotent.

---

## Part 1: Fix `collect_game_totals()` in `data_mlb.py`

### What to extract from the schedule API response

The `/schedule?hydrate=linescore` response already contains `venue.id` and `gameDate` (UTC ISO string) per game. These are available in the `game` dict from the API response but are not currently being read.

Add these extractions alongside the existing team ID extractions (around line 1413):

```python
venue_id = game.get("venue", {}).get("id")
game_time_utc = game.get("gameDate", "")  # ISO 8601 UTC e.g. "2026-04-15T18:10:00Z"
```

### Weather fetch

After extracting `venue_id` and `game_time_utc`, call `fetch_venue_weather()`:

```python
weather = {}
if venue_id:
    try:
        weather = fetch_venue_weather(venue_id, game_date, game_time_utc) or {}
    except Exception:
        weather = {}
```

### Replace hardcoded Nones (lines 1470–1472)

```python
# Before:
"temp_f": None,
"wind_mph": None,
"wind_dir": None,

# After:
"temp_f": weather.get("temp_f"),
"wind_mph": weather.get("wind_mph"),
"wind_dir": weather.get("wind_dir"),
```

### Error handling

- If `venue_id` is missing: skip weather fetch, store `None` (same as today, no regression)
- If `fetch_venue_weather()` raises: catch exception, log warning, store `None`
- If weather dict missing a key: `.get()` returns `None` safely

---

## Part 2: `update_game_total_weather()` in `database.py`

New module-level function following the same pattern as nearby functions (uses `get_connection()`, closes connection).

Insert after `backfill_game_totals_abbr()` (after line 1730):

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
```

---

## Part 3: `backfill_game_totals_weather()` in `data_mlb.py`

New function that backfills weather for the 200 existing NULL rows.

**Game time lookup:** Use the MLB API schedule endpoint with `gamePk` to get `gameDate` (UTC). Endpoint: `/schedule?gamePks={pk}` returns `gameDate` in the game object.

**Rate limiting:** 1-second sleep between API calls to respect Open-Meteo's free tier.

```python
def backfill_game_totals_weather() -> int:
    """
    Backfill temp_f, wind_mph, wind_dir for game_totals rows where temp_f IS NULL.
    Fetches game time from MLB API, then weather from Open-Meteo historical data.
    Returns count of rows successfully updated.
    """
    import time
    rows = db.get_game_totals_missing_weather()  # returns list of (mlb_game_id, game_date)
    updated = 0
    for mlb_game_id, game_date in rows:
        try:
            # Get game time UTC from MLB API
            sched_url = f"{MLB_BASE}/schedule?gamePks={mlb_game_id}"
            resp = requests.get(sched_url, timeout=10)
            game_time_utc = ""
            if resp.status_code == 200:
                dates = resp.json().get("dates", [])
                if dates:
                    games = dates[0].get("games", [])
                    if games:
                        venue_id = games[0].get("venue", {}).get("id")
                        game_time_utc = games[0].get("gameDate", "")

            if not venue_id:
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

**Entry point:** Add `--backfill-weather` flag to `engine.py` OR run directly as:
```bash
python3 -c "from data_mlb import backfill_game_totals_weather; backfill_game_totals_weather()"
```

The simpler option (no engine.py change): run directly. Add to engine.py only if needed.

---

## Part 4: `get_game_totals_missing_weather()` in `database.py`

Helper to fetch rows needing backfill:

```python
def get_game_totals_missing_weather():
    """Return list of (mlb_game_id, game_date) for rows where temp_f IS NULL."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT mlb_game_id, game_date FROM game_totals WHERE temp_f IS NULL ORDER BY game_date"
    ).fetchall()
    conn.close()
    return rows
```

---

## Data Flow After Fix

```
collect_game_totals(date)              ← runs nightly at 11 PM
  → MLB /schedule?hydrate=linescore
    → extracts venue_id, game_time_utc per game
  → fetch_venue_weather(venue_id, date, game_time_utc)
    → Open-Meteo historical API → {temp_f, wind_mph, wind_dir}
  → store_game_totals([{..., temp_f: 64.2, wind_mph: 8.1, wind_dir: "SW"}])

backfill_game_totals_weather()         ← one-time run
  → get_game_totals_missing_weather() → 200 rows
  → for each: MLB /schedule?gamePks={pk} → venue_id, game_time_utc
  → fetch_venue_weather(...)
  → update_game_total_weather(mlb_game_id, temp_f, wind_mph, wind_dir)
```

---

## Testing

- **`collect_game_totals()` weather test:** Mock `fetch_venue_weather` to return `{"temp_f": 72.0, "wind_mph": 10.0, "wind_dir": "SW"}`. Assert the stored record contains those values.
- **Weather fetch failure test:** Mock `fetch_venue_weather` to raise `Exception`. Assert record stores `None` for weather fields (no crash).
- **Missing venue_id test:** Game dict with no `venue` key. Assert weather fields are `None`, no exception.
- **`update_game_total_weather()` test:** Insert a row, call function, assert fields updated.
- **`get_game_totals_missing_weather()` test:** Insert rows with and without temp_f. Assert only NULL rows returned.
- **`backfill_game_totals_weather()` test:** Mock MLB API + `fetch_venue_weather`. Assert rows updated, already-populated rows skipped.

---

## Out of Scope

- No changes to `analysis.py`, `engine.py` pick logic, or O/U model
- No changes to `score_weather()` scoring logic
- No `precip_chance` or `conditions` storage (only the 3 fields already in schema)
- Phase 2 (historical backfill of 2024/2025 backtest data) is a separate effort
