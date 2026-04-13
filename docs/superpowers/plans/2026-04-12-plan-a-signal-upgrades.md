# Plan A: Signal Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add home/away SP ERA splits, surface top-reliever ERA from existing game logs, and track line movement to filter picks when sharp money disagrees.

**Architecture:** Three independent signal improvements, all additive — no breaking changes to existing pick flow. Home/away splits enrich `pitcher_stats` dicts already attached to game objects. Bullpen key-reliever ERA comes from data already in `pitcher_game_logs`. Line movement adds one new DB table (`opening_lines`) and two new DB functions; the refresh loop gains one warning block.

**Tech Stack:** Python 3.9, SQLite (sqlite3), MLB Stats API (free), existing `database.py` + `data_mlb.py` + `analysis.py` + `engine.py` pattern.

**Python version note:** No `float | None` union syntax — use `"float | None"` string annotations or `Optional[float]`. Use `except sqlite3.OperationalError: pass` for migration ALTER TABLEs.

---

## File Map

| File | Change |
|------|--------|
| `data_mlb.py` | Add `fetch_pitcher_home_away_splits(pitcher_id)` |
| `data_mlb.py` | Call it in `collect_game_data()` → attach as `g["away_pitcher_splits"]`, `g["home_pitcher_splits"]` |
| `analysis.py` | In `score_pitching()`: use split ERA/WHIP when available instead of season ERA/WHIP |
| `database.py` | Add `opening_lines` table in `init_db()` migration block |
| `database.py` | Add `save_opening_lines(mlb_game_id, game_date, consensus)` with INSERT OR IGNORE |
| `database.py` | Add `get_opening_lines(mlb_game_id, game_date)` → dict |
| `database.py` | Add `get_bullpen_top_relievers(team_id, days=7)` → list of top 3 relievers by IP with ERA |
| `analysis.py` | In `score_bullpen()`: call `get_bullpen_top_relievers()` and add to edge description |
| `engine.py` | In `run_analysis()`: after odds fetch, save opening lines per game (INSERT OR IGNORE) |
| `engine.py` | In `run_refresh()`: compare current odds vs opening lines; append line movement warning to update messages |
| `tests/test_signal_upgrades.py` | New test file: 12 tests covering all three improvements |

---

### Task 1: Home/Away SP Splits — data fetch

**Files:**
- Modify: `data_mlb.py`
- Test: `tests/test_signal_upgrades.py`

**Background:** MLB Stats API returns home/away split ERA/WHIP via `/people/{id}/stats?stats=statSplits&group=pitching&season=2026`. The response has `stats[0].splits[]` where each split has `split.code == "H"` (home) or `"A"` (away). We add a session-level cache like `_player_stat_cache` to avoid re-fetching.

- [ ] **Step 1: Write the failing test**

Create `tests/test_signal_upgrades.py`:

```python
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch


def _make_splits_response(pitcher_id, home_era, away_era, home_whip, away_whip):
    return {"people": [{
        "id": pitcher_id,
        "stats": [{
            "splits": [
                {"split": {"code": "H"}, "stat": {
                    "era": str(home_era), "whip": str(home_whip),
                    "strikeoutsPer9Inn": "9.0", "walksPer9Inn": "2.5"
                }},
                {"split": {"code": "A"}, "stat": {
                    "era": str(away_era), "whip": str(away_whip),
                    "strikeoutsPer9Inn": "8.0", "walksPer9Inn": "3.0"
                }},
            ]
        }]
    }]}


def test_fetch_pitcher_home_away_splits_returns_both_splits():
    import data_mlb
    # Clear cache
    data_mlb._pitcher_split_cache.clear()

    fake = _make_splits_response(123, home_era=2.85, away_era=4.10,
                                 home_whip=1.05, away_whip=1.28)
    with patch("data_mlb.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = fake
        result = data_mlb.fetch_pitcher_home_away_splits(123)

    assert result["home_era"] == pytest.approx(2.85)
    assert result["away_era"] == pytest.approx(4.10)
    assert result["home_whip"] == pytest.approx(1.05)
    assert result["away_whip"] == pytest.approx(1.28)


def test_fetch_pitcher_home_away_splits_caches():
    import data_mlb
    data_mlb._pitcher_split_cache.clear()

    fake = _make_splits_response(456, 3.0, 4.0, 1.1, 1.3)
    with patch("data_mlb.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = fake
        data_mlb.fetch_pitcher_home_away_splits(456)
        data_mlb.fetch_pitcher_home_away_splits(456)  # second call — should not call API again
        assert mock_get.call_count == 1


def test_fetch_pitcher_home_away_splits_returns_empty_on_error():
    import data_mlb
    data_mlb._pitcher_split_cache.clear()
    with patch("data_mlb.requests.get", side_effect=Exception("timeout")):
        result = data_mlb.fetch_pitcher_home_away_splits(999)
    assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_signal_upgrades.py::test_fetch_pitcher_home_away_splits_returns_both_splits -v
```
Expected: `FAILED` — `AttributeError: module 'data_mlb' has no attribute '_pitcher_split_cache'`

- [ ] **Step 3: Implement `fetch_pitcher_home_away_splits` in `data_mlb.py`**

Add after the existing `_player_stat_cache` declaration (around line 18):

```python
_pitcher_split_cache: dict = {}
```

Add the function after `fetch_pitcher_stats` (after line 344):

```python
def fetch_pitcher_home_away_splits(pitcher_id: int, season: int = None) -> dict:
    """
    Fetch home/away ERA/WHIP/K9/BB9 splits for a starter.
    Returns {"home_era", "home_whip", "home_k9", "home_bb9",
             "away_era", "away_whip", "away_k9", "away_bb9"}
    or {} on failure/no data.
    Cached per session.
    """
    if not pitcher_id:
        return {}
    if pitcher_id in _pitcher_split_cache:
        return _pitcher_split_cache[pitcher_id]

    _season = season or SEASON_YEAR
    url = (
        f"{MLB_BASE}/people/{pitcher_id}"
        f"?hydrate=stats(group=[pitching],type=[statSplits],season={_season})"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching splits for pitcher {pitcher_id}: {e}")
        _pitcher_split_cache[pitcher_id] = {}
        return {}

    person = data.get("people", [{}])[0]
    result = {}
    for sg in person.get("stats", []):
        for split in sg.get("splits", []):
            code = split.get("split", {}).get("code", "")
            s = split.get("stat", {})
            if code == "H":
                result["home_era"] = _safe_float(s.get("era"))
                result["home_whip"] = _safe_float(s.get("whip"))
                result["home_k9"] = _safe_float(s.get("strikeoutsPer9Inn"))
                result["home_bb9"] = _safe_float(s.get("walksPer9Inn"))
            elif code == "A":
                result["away_era"] = _safe_float(s.get("era"))
                result["away_whip"] = _safe_float(s.get("whip"))
                result["away_k9"] = _safe_float(s.get("strikeoutsPer9Inn"))
                result["away_bb9"] = _safe_float(s.get("walksPer9Inn"))

    _pitcher_split_cache[pitcher_id] = result
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_signal_upgrades.py::test_fetch_pitcher_home_away_splits_returns_both_splits tests/test_signal_upgrades.py::test_fetch_pitcher_home_away_splits_caches tests/test_signal_upgrades.py::test_fetch_pitcher_home_away_splits_returns_empty_on_error -v
```
Expected: `3 passed`

- [ ] **Step 5: Wire into `collect_game_data()` in `data_mlb.py`**

In `collect_game_data()`, after the rest-day lines (around line 913), add:

```python
        # Home/away pitcher splits (use away_era for away SP pitching away,
        # home_era for home SP pitching home)
        g["away_pitcher_splits"] = fetch_pitcher_home_away_splits(g.get("away_pitcher_id"))
        g["home_pitcher_splits"] = fetch_pitcher_home_away_splits(g.get("home_pitcher_id"))
```

- [ ] **Step 6: Use splits in `score_pitching()` in `analysis.py`**

In `score_pitching()`, find where `away_era` and `home_era` are computed from `_blend()` (around line 36-44). Replace those 8 lines with:

```python
    # Prefer home/away split ERA if available (away SP pitching away, home SP pitching home)
    away_splits = game.get("away_pitcher_splits") or {}
    home_splits = game.get("home_pitcher_splits") or {}

    # Season ERA for blend base — use venue-specific split if available
    away_era_season = away_splits.get("away_era") or _safe(away_p.get("era"))
    home_era_season = home_splits.get("home_era") or _safe(home_p.get("era"))
    away_whip_season = away_splits.get("away_whip") or _safe(away_p.get("whip"))
    home_whip_season = home_splits.get("home_whip") or _safe(home_p.get("whip"))

    # K9/BB9: use split if available, else season
    away_k9_season = away_splits.get("away_k9") or _safe(away_p.get("k_per_9"))
    home_k9_season = home_splits.get("home_k9") or _safe(home_p.get("k_per_9"))
    away_bb9_season = away_splits.get("away_bb9") or _safe(away_p.get("bb_per_9"))
    home_bb9_season = home_splits.get("home_bb9") or _safe(home_p.get("bb_per_9"))

    away_era  = _blend(away_era_season,  away_rolling.get("era"),  away_g)
    away_whip = _blend(away_whip_season, away_rolling.get("whip"), away_g)
    away_k9   = _blend(away_k9_season,   away_rolling.get("k9"),   away_g)
    away_bb9  = _blend(away_bb9_season,  away_rolling.get("bb9"),  away_g)

    home_era  = _blend(home_era_season,  home_rolling.get("era"),  home_g)
    home_whip = _blend(home_whip_season, home_rolling.get("whip"), home_g)
    home_k9   = _blend(home_k9_season,   home_rolling.get("k9"),   home_g)
    home_bb9  = _blend(home_bb9_season,  home_rolling.get("bb9"),  home_g)
```

Also update the note at the end of `score_pitching()` to mention splits (around line 131):

```python
    # Note when splits or rolling are active
    notes_parts = []
    if away_splits.get("away_era") or home_splits.get("home_era"):
        notes_parts.append("venue splits")
    if away_g >= 5 or home_g >= 5:
        notes_parts.append(f"rolling: {away_g}gs away, {home_g}gs home")
    if notes_parts:
        edge += f" [{', '.join(notes_parts)}]"
```

Replace the existing:
```python
    if away_g >= 5 or home_g >= 5:
        edge += f" [rolling: {away_g}gs away, {home_g}gs home]"
```

- [ ] **Step 7: Write test for split usage in scoring**

Add to `tests/test_signal_upgrades.py`:

```python
def test_score_pitching_uses_away_split_for_away_sp():
    """Away SP pitching away should use away_era split, not season ERA."""
    from analysis import score_pitching

    game = {
        "away_pitcher_stats": {
            "era": 5.50, "whip": 1.50, "k_per_9": 7.0, "bb_per_9": 3.5,
            "k_bb_ratio": 2.0, "throws": "R", "days_rest": 5
        },
        "home_pitcher_stats": {
            "era": 5.50, "whip": 1.50, "k_per_9": 7.0, "bb_per_9": 3.5,
            "k_bb_ratio": 2.0, "throws": "R", "days_rest": 5
        },
        # Away SP has much better away ERA (good road pitcher)
        "away_pitcher_splits": {"away_era": 2.50, "away_whip": 1.00,
                                  "away_k9": 9.0, "away_bb9": 2.0},
        "home_pitcher_splits": {},
        "away_pitcher_rolling": None,
        "home_pitcher_rolling": None,
        "away_batting": {"strikeouts": 1000, "at_bats": 4500},
        "home_batting": {"strikeouts": 1000, "at_bats": 4500},
    }
    result = score_pitching(game)
    # Away SP 2.50 ERA (split) vs home SP 5.50 ERA (season) = away advantage
    assert result["score"] < 0.0, "Away SP's better road ERA should give away edge"
```

- [ ] **Step 8: Run test to verify it passes**

```bash
python3 -m pytest tests/test_signal_upgrades.py::test_score_pitching_uses_away_split_for_away_sp -v
```
Expected: `PASSED`

- [ ] **Step 9: Commit**

```bash
git add data_mlb.py analysis.py tests/test_signal_upgrades.py
git commit -m "feat: add home/away SP ERA splits to pitching agent"
```

---

### Task 2: Bullpen Key Reliever ERA from Existing Game Logs

**Files:**
- Modify: `database.py`
- Modify: `analysis.py`
- Test: `tests/test_signal_upgrades.py`

**Background:** `pitcher_game_logs` already stores all reliever appearances (is_starter=0). This task surfaces the top 3 relievers by innings pitched over the last 7 days, computes their combined ERA, and appends it to the bullpen edge description.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_signal_upgrades.py`:

```python
import sqlite3
import database as _db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    _db.init_db()
    return db_path


def _insert_reliever(db_path, pitcher_id, team_id, game_date, ip, er, k):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,0,?,?,?,0,0,0)
    """, (pitcher_id * 100, game_date, pitcher_id, f"Reliever {pitcher_id}",
          team_id, ip, er, k))
    conn.commit()
    conn.close()


def test_get_bullpen_top_relievers_returns_top_3_by_ip(fresh_db):
    today = "2026-04-12"
    # 4 relievers for team 10; top 3 by IP should be returned
    _insert_reliever(fresh_db, 1, 10, "2026-04-11", ip=2.0, er=0, k=3)  # 2 IP
    _insert_reliever(fresh_db, 2, 10, "2026-04-11", ip=1.0, er=1, k=1)  # 1 IP
    _insert_reliever(fresh_db, 3, 10, "2026-04-11", ip=3.0, er=0, k=4)  # 3 IP
    _insert_reliever(fresh_db, 4, 10, "2026-04-10", ip=1.5, er=2, k=2)  # 1.5 IP

    result = _db.get_bullpen_top_relievers(10, days=7, as_of_date=today)
    assert len(result) == 3
    # Top 3 by IP: pitcher 3 (3.0), pitcher 1 (2.0), pitcher 4 (1.5)
    total_ips = [r["total_ip"] for r in result]
    assert total_ips[0] >= total_ips[1] >= total_ips[2]


def test_get_bullpen_top_relievers_returns_empty_when_no_data(fresh_db):
    result = _db.get_bullpen_top_relievers(99, days=7, as_of_date="2026-04-12")
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_signal_upgrades.py::test_get_bullpen_top_relievers_returns_top_3_by_ip -v
```
Expected: `FAILED` — `AttributeError: module 'database' has no attribute 'get_bullpen_top_relievers'`

- [ ] **Step 3: Implement `get_bullpen_top_relievers` in `database.py`**

Add after `get_team_bullpen_rolling` (after line 798):

```python
def get_bullpen_top_relievers(team_id: int, days: int = 7,
                               as_of_date: str = None) -> list:
    """
    Return top 3 relievers (by total IP) for a team over the last N days.
    Each entry: {"pitcher_id", "pitcher_name", "total_ip", "era"}.
    Returns [] if no data.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT pitcher_id, pitcher_name,
               SUM(innings_pitched) as total_ip,
               SUM(earned_runs) as total_er
        FROM pitcher_game_logs
        WHERE team_id=? AND is_starter=0 AND game_date > ? AND innings_pitched > 0
        GROUP BY pitcher_id, pitcher_name
        ORDER BY total_ip DESC
        LIMIT 3
    """, (team_id, cutoff.isoformat())).fetchall()
    conn.close()
    result = []
    for r in rows:
        ip = r["total_ip"] or 0.0
        era = round(r["total_er"] / ip * 9, 2) if ip > 0 else 0.0
        result.append({
            "pitcher_id": r["pitcher_id"],
            "pitcher_name": r["pitcher_name"],
            "total_ip": round(ip, 1),
            "era": era,
        })
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_signal_upgrades.py::test_get_bullpen_top_relievers_returns_top_3_by_ip tests/test_signal_upgrades.py::test_get_bullpen_top_relievers_returns_empty_when_no_data -v
```
Expected: `2 passed`

- [ ] **Step 5: Wire into `score_bullpen()` in `analysis.py`**

Add import at top of `analysis.py` — after the existing imports, add:

```python
import database as _analysis_db
```

In `score_bullpen()`, after the fatigue notes block and before the `return` (around line 332), add:

```python
    # ── Key reliever ERA ──
    home_key_rel = _analysis_db.get_bullpen_top_relievers(
        game.get("home_team_mlb_id"), days=7)
    away_key_rel = _analysis_db.get_bullpen_top_relievers(
        game.get("away_team_mlb_id"), days=7)

    key_rel_notes = []
    if home_key_rel:
        names = ", ".join(r["pitcher_name"].split()[-1] for r in home_key_rel)
        era = round(sum(r["era"] * r["total_ip"] for r in home_key_rel) /
                    max(sum(r["total_ip"] for r in home_key_rel), 0.1), 2)
        key_rel_notes.append(f"Home top pen (7d): {names} — {era:.2f} ERA")
    if away_key_rel:
        names = ", ".join(r["pitcher_name"].split()[-1] for r in away_key_rel)
        era = round(sum(r["era"] * r["total_ip"] for r in away_key_rel) /
                    max(sum(r["total_ip"] for r in away_key_rel), 0.1), 2)
        key_rel_notes.append(f"Away top pen (7d): {names} — {era:.2f} ERA")

    if key_rel_notes:
        edge += " | " + " | ".join(key_rel_notes)
```

Note: `game.get("home_team_mlb_id")` is the mlb team ID — this is already set in the game dict from `collect_game_data()`.

- [ ] **Step 6: Write test for bullpen edge with key reliever info**

Add to `tests/test_signal_upgrades.py`:

```python
def test_score_bullpen_includes_key_reliever_info(fresh_db, monkeypatch):
    """score_bullpen edge should include key reliever ERA when data exists."""
    import analysis
    from datetime import date, timedelta

    # Insert a reliever for team 10
    _insert_reliever(fresh_db, 11, 10, (date.today() - timedelta(days=2)).isoformat(),
                     ip=2.0, er=1, k=3)

    game = {
        "home_pitching": {"era": 4.0, "whip": 1.3, "k_per_9": 8.0,
                          "saves": 3, "save_opportunities": 4, "holds": 2, "blown_saves": 1},
        "away_pitching": {"era": 4.0, "whip": 1.3, "k_per_9": 8.0,
                          "saves": 3, "save_opportunities": 4, "holds": 2, "blown_saves": 1},
        "home_bullpen_rolling": None,
        "away_bullpen_rolling": None,
        "home_bullpen_usage": {"ip_last_3": 0.0, "ip_last_5": 0.0},
        "away_bullpen_usage": {"ip_last_3": 0.0, "ip_last_5": 0.0},
        "home_team_mlb_id": 10,
        "away_team_mlb_id": 99,  # no data for away
    }
    result = analysis.score_bullpen(game)
    assert "Home top pen" in result["edge"]
```

- [ ] **Step 7: Run test to verify it passes**

```bash
python3 -m pytest tests/test_signal_upgrades.py::test_score_bullpen_includes_key_reliever_info -v
```
Expected: `PASSED`

- [ ] **Step 8: Commit**

```bash
git add database.py analysis.py tests/test_signal_upgrades.py
git commit -m "feat: surface top-reliever ERA from game logs in bullpen agent"
```

---

### Task 3: Line Movement Tracking

**Files:**
- Modify: `database.py`
- Modify: `engine.py`
- Test: `tests/test_signal_upgrades.py`

**Background:** Store opening odds (first capture of the day) in a new `opening_lines` table. In `run_refresh()`, compare current lines to opening. If the line moved against the pick by a meaningful amount, add a warning to the update message. This helps filter picks where sharp money has moved against us.

Movement thresholds:
- ML: implied probability for our pick dropped ≥5pp since opening → warn
- Total: line moved ≥0.5 runs against our O/U pick → warn

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_signal_upgrades.py`:

```python
def test_opening_lines_table_exists(fresh_db):
    conn = sqlite3.connect(fresh_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(opening_lines)")]
    conn.close()
    assert "mlb_game_id" in cols
    assert "home_ml" in cols
    assert "away_ml" in cols
    assert "total_line" in cols


def test_save_opening_lines_inserts_once(fresh_db):
    consensus = {"home_ml": -130, "away_ml": 110, "total_line": 8.5,
                 "over_price": -110, "under_price": -110}
    _db.save_opening_lines(12345, "2026-04-12", consensus)
    _db.save_opening_lines(12345, "2026-04-12", consensus)  # second call — no duplicate

    conn = sqlite3.connect(fresh_db)
    count = conn.execute("SELECT COUNT(*) FROM opening_lines WHERE mlb_game_id=12345").fetchone()[0]
    conn.close()
    assert count == 1


def test_get_opening_lines_returns_saved_values(fresh_db):
    consensus = {"home_ml": -140, "away_ml": 120, "total_line": 9.0,
                 "over_price": -115, "under_price": -105}
    _db.save_opening_lines(99999, "2026-04-12", consensus)
    result = _db.get_opening_lines(99999, "2026-04-12")
    assert result["home_ml"] == -140
    assert result["total_line"] == 9.0


def test_get_opening_lines_returns_none_when_missing(fresh_db):
    result = _db.get_opening_lines(0, "2026-04-12")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_signal_upgrades.py::test_opening_lines_table_exists tests/test_signal_upgrades.py::test_save_opening_lines_inserts_once -v
```
Expected: `FAILED` — table doesn't exist yet

- [ ] **Step 3: Add `opening_lines` table and DB functions in `database.py`**

In `init_db()`, add inside the `executescript` (after the `team_game_logs` table, before the indexes):

```sql
    CREATE TABLE IF NOT EXISTS opening_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_date TEXT NOT NULL,
        mlb_game_id INTEGER NOT NULL,
        home_ml INTEGER,
        away_ml INTEGER,
        total_line REAL,
        over_price INTEGER,
        under_price INTEGER,
        captured_at TEXT NOT NULL,
        UNIQUE(game_date, mlb_game_id)
    );
```

After `get_team_bullpen_rolling` (or after `get_bullpen_top_relievers`), add:

```python
def save_opening_lines(mlb_game_id: int, game_date: str, consensus: dict) -> None:
    """Store opening odds for a game. INSERT OR IGNORE — only first capture kept."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO opening_lines
            (game_date, mlb_game_id, home_ml, away_ml, total_line,
             over_price, under_price, captured_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (game_date, mlb_game_id,
              consensus.get("home_ml"), consensus.get("away_ml"),
              consensus.get("total_line"),
              consensus.get("over_price"), consensus.get("under_price"),
              now))
        conn.commit()
    except sqlite3.DatabaseError as e:
        print(f"[DB] Error saving opening lines: {e}")
    finally:
        conn.close()


def get_opening_lines(mlb_game_id: int, game_date: str) -> "dict | None":
    """Return opening odds for a game, or None if not captured."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM opening_lines WHERE mlb_game_id=? AND game_date=?",
        (mlb_game_id, game_date)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_signal_upgrades.py::test_opening_lines_table_exists tests/test_signal_upgrades.py::test_save_opening_lines_inserts_once tests/test_signal_upgrades.py::test_get_opening_lines_returns_saved_values tests/test_signal_upgrades.py::test_get_opening_lines_returns_none_when_missing -v
```
Expected: `4 passed`

- [ ] **Step 5: Wire opening line capture into `run_analysis()` in `engine.py`**

In `run_analysis()`, in the loop where games are analyzed (around line 70, just before `analysis = analyze_game(...)`), add:

```python
        # Store opening odds for this game (INSERT OR IGNORE — only first capture kept)
        if odds_data and odds_data.get("consensus"):
            db.save_opening_lines(
                g.get("mlb_game_id"),
                date.today().isoformat(),
                odds_data["consensus"]
            )
```

Also add it in `run_refresh()` in the same loop (around line 258, before `analysis = analyze_game(...)`):

```python
        # Refresh opening lines capture (INSERT OR IGNORE no-ops if already saved today)
        if odds_data and odds_data.get("consensus"):
            db.save_opening_lines(
                g.get("mlb_game_id"),
                date.today().isoformat(),
                odds_data["consensus"]
            )
```

- [ ] **Step 6: Add line movement comparison to `run_refresh()` in `engine.py`**

At the top of `engine.py`, add the import for `implied_probability`:

```python
from data_odds import fetch_odds, match_odds_to_game, implied_probability
```

In `run_refresh()`, in the pick comparison loop after the `still_approved` check (around line 338), add a line movement check. Find the block that starts `if not still_approved:` and add before it:

```python
        # ── Line movement warning ──
        opening = db.get_opening_lines(mlb_game_id, date.today().isoformat())
        line_moved_against = False
        line_move_desc = ""
        if opening:
            cur_consensus = refreshed.get("agents", {}).get("market", {}).get("detail", {})
            if pick_type == "moneyline":
                pick_side = "home" if conn_pick.get("pick_team") == refreshed.get("home_team") else "away"
                if pick_side == "home" and opening.get("home_ml") and cur_consensus.get("home_ml"):
                    open_prob = implied_probability(opening["home_ml"])
                    cur_prob = implied_probability(cur_consensus["home_ml"])
                    if open_prob - cur_prob >= 0.05:
                        line_moved_against = True
                        line_move_desc = (f"Home ML moved from {opening['home_ml']} to "
                                         f"{cur_consensus['home_ml']} — sharp money on away")
                elif pick_side == "away" and opening.get("away_ml") and cur_consensus.get("away_ml"):
                    open_prob = implied_probability(opening["away_ml"])
                    cur_prob = implied_probability(cur_consensus["away_ml"])
                    if open_prob - cur_prob >= 0.05:
                        line_moved_against = True
                        line_move_desc = (f"Away ML moved from {opening['away_ml']} to "
                                         f"{cur_consensus['away_ml']} — sharp money on home")
            elif pick_type in ("over", "under"):
                open_total = opening.get("total_line")
                cur_total = cur_consensus.get("total_line")
                if open_total and cur_total:
                    if pick_type == "over" and cur_total >= open_total + 0.5:
                        line_moved_against = True
                        line_move_desc = f"Total moved from {open_total} to {cur_total} — line went against OVER"
                    elif pick_type == "under" and cur_total <= open_total - 0.5:
                        line_moved_against = True
                        line_move_desc = f"Total moved from {open_total} to {cur_total} — line went against UNDER"
```

Then in the `elif new_conf <= prior_conf - 2:` block and the `else:` block, append the line movement info when present. Find the existing `else: print(f"  ✅ {refreshed['game']} — unchanged (conf {new_conf}/10)")` and change it to:

```python
        else:
            if line_moved_against:
                update = {
                    "game": refreshed["game"],
                    "original_pick": conn_pick.get("pick_team", "?"),
                    "update": "Line has moved against pick — sharp money disagrees",
                    "action": "Watch",
                    "reason": line_move_desc,
                }
                send_update(update)
                print(f"  ⚠️  Line movement alert: {refreshed['game']} — {line_move_desc}")
                updates_sent += 1
            else:
                print(f"  ✅ {refreshed['game']} — unchanged (conf {new_conf}/10)")
```

- [ ] **Step 7: Commit**

```bash
git add database.py engine.py tests/test_signal_upgrades.py
git commit -m "feat: add line movement tracking and opening line comparison in refresh"
```

---

### Task 4: Final integration test and full test suite pass

**Files:**
- Test: `tests/test_signal_upgrades.py`

- [ ] **Step 1: Run full test suite to verify no regressions**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all existing tests pass; `tests/test_analysis_log.py::test_run_results_grades_analysis_log` is a pre-existing failure — ignore it.

- [ ] **Step 2: Run a dry-run to verify end-to-end integration**

```bash
python3 engine.py --test 2>&1 | tail -20
```
Expected: runs without error; picks include split notes in pitching edge when splits are available.

- [ ] **Step 3: Final commit**

```bash
git add .
git commit -m "test: verify signal upgrades integration (Plan A complete)"
```
