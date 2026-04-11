# Bullpen Fatigue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add recent bullpen workload awareness to the bullpen scoring agent so a fatigued bullpen gets a score penalty.

**Architecture:** A new `fetch_bullpen_recent_usage(team_id)` function in `data_mlb.py` fetches the team's last 7 days of completed games via the MLB schedule API with boxscore hydration, sums relief innings pitched (all pitchers after the starter), and returns usage totals for the last 3 and 5 days. `score_bullpen()` in `analysis.py` reads `home_bullpen_usage` and `away_bullpen_usage` from the game dict and adjusts the base score by a penalty for the fatigued side. `enrich_games()` in `data_mlb.py` fetches and attaches usage for both teams.

**Tech Stack:** Python 3, MLB Stats API (free, no key), `requests`, existing `analysis.py`/`data_mlb.py` patterns.

---

## File Structure

| File | Change |
|------|--------|
| `data_mlb.py` | Add `_parse_ip()` helper + `fetch_bullpen_recent_usage()` + wire into `enrich_games()` |
| `analysis.py` | Update `score_bullpen()` to read usage and apply fatigue penalty |
| `tests/test_bullpen_fatigue.py` | New test file |

---

## IP Parsing Note

The MLB API returns innings pitched as a string like `"6.2"` which means **6 full innings + 2 outs** (not 6.2 decimal innings). Convert with: `innings + outs / 3`. So `"6.2"` → 6.667.

---

## Fatigue Thresholds

Applied per team based on `ip_last_3` (relief innings over last 3 days):

| ip_last_3 | Penalty |
|-----------|---------|
| ≤ 8.0 | 0.00 (normal) |
| 8.1 – 12.0 | 0.08 (moderate fatigue) |
| > 12.0 | 0.15 (heavy fatigue) |

Score convention: positive = home edge, negative = away edge.
If home bullpen is fatigued → `score -= penalty`. If away is fatigued → `score += penalty`.
Net adjustment: `score += away_penalty - home_penalty`.

---

## Task 1: Add `_parse_ip()` and `fetch_bullpen_recent_usage()` to `data_mlb.py`

**Files:**
- Modify: `data_mlb.py`
- Test: `tests/test_bullpen_fatigue.py`

- [ ] **Step 1: Create test file and write failing tests**

Create `/Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue/tests/test_bullpen_fatigue.py`:

```python
import sys
sys.path.insert(0, "/Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue")

import pytest
from unittest.mock import patch, MagicMock
import json
from datetime import date


def _mock_schedule_response(games):
    """Helper: build a fake MLB schedule API response with boxscore data."""
    return {"dates": games}


def _make_game(date_str, team_id, side, pitcher_ids, ip_list, final=True):
    """
    Helper: build a fake game entry.
    pitcher_ids: [starter_id, rel1_id, rel2_id, ...]
    ip_list: ["5.0", "2.1", "1.0"] — one per pitcher, same order
    """
    players = {}
    for pid, ip in zip(pitcher_ids, ip_list):
        players[f"ID{pid}"] = {
            "stats": {"pitching": {"inningsPitched": ip}}
        }
    team_data = {"pitchers": pitcher_ids, "players": players, "team": {"id": team_id}}
    opponent = {"pitchers": [], "players": {}, "team": {"id": 9999}}

    if side == "home":
        teams = {"home": team_data, "away": opponent}
    else:
        teams = {"away": team_data, "home": opponent}

    state = "Final" if final else "Live"
    return {
        "dates": [{
            "date": date_str,
            "games": [{
                "status": {"abstractGameState": state},
                "teams": teams,
            }]
        }]
    }


def test_parse_ip_whole_innings():
    import data_mlb
    assert data_mlb._parse_ip("6.0") == pytest.approx(6.0)


def test_parse_ip_partial_innings():
    import data_mlb
    # "6.2" = 6 innings + 2 outs = 6 + 2/3
    assert data_mlb._parse_ip("6.2") == pytest.approx(6.667, abs=0.01)


def test_parse_ip_one_out():
    import data_mlb
    assert data_mlb._parse_ip("3.1") == pytest.approx(3.333, abs=0.01)


def test_parse_ip_invalid_returns_zero():
    import data_mlb
    assert data_mlb._parse_ip(None) == 0.0
    assert data_mlb._parse_ip("") == 0.0


def test_fetch_bullpen_recent_usage_sums_relief_ip(tmp_path):
    """Starter throws 5.0, two relievers throw 2.1 and 1.0 — bullpen IP = 3.1 = 3.333."""
    import data_mlb
    today = date(2026, 4, 15)
    # Game was 2 days ago
    game_date = date(2026, 4, 13).isoformat()
    fake_resp = _make_game(game_date, team_id=147, side="home",
                           pitcher_ids=[100, 101, 102],
                           ip_list=["5.0", "2.1", "1.0"])

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"dates": fake_resp["dates"]}
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_bullpen_recent_usage(147, as_of_date=today)

    # 2.1 + 1.0 = 2.333 + 1.0 = 3.333 IP from relievers
    assert result["ip_last_3"] == pytest.approx(3.333, abs=0.01)
    assert result["ip_last_5"] == pytest.approx(3.333, abs=0.01)
    assert result["games_last_3"] == 1
    assert result["games_last_5"] == 1


def test_fetch_bullpen_recent_usage_excludes_nonfinal(tmp_path):
    """Live/in-progress games are excluded."""
    import data_mlb
    today = date(2026, 4, 15)
    game_date = date(2026, 4, 14).isoformat()
    fake_resp = _make_game(game_date, team_id=147, side="home",
                           pitcher_ids=[100, 101],
                           ip_list=["7.0", "2.0"],
                           final=False)  # not final

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"dates": fake_resp["dates"]}
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_bullpen_recent_usage(147, as_of_date=today)

    assert result["ip_last_3"] == 0.0
    assert result["games_last_3"] == 0


def test_fetch_bullpen_recent_usage_only_counts_matching_team():
    """If the team played as away, use the away side's pitchers."""
    import data_mlb
    today = date(2026, 4, 15)
    game_date = date(2026, 4, 13).isoformat()
    # Team 147 played as away; home team 999 had their own pitchers
    fake_resp = _make_game(game_date, team_id=147, side="away",
                           pitcher_ids=[200, 201],
                           ip_list=["6.0", "3.0"])

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"dates": fake_resp["dates"]}
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_bullpen_recent_usage(147, as_of_date=today)

    assert result["ip_last_3"] == pytest.approx(3.0)


def test_fetch_bullpen_recent_usage_api_error_returns_zeros():
    import data_mlb
    with patch("data_mlb.requests.get", side_effect=Exception("timeout")):
        result = data_mlb.fetch_bullpen_recent_usage(147)

    assert result == {"ip_last_3": 0.0, "ip_last_5": 0.0, "games_last_3": 0, "games_last_5": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue && python3 -m pytest tests/test_bullpen_fatigue.py -v
```

Expected: FAIL — `_parse_ip` and `fetch_bullpen_recent_usage` not defined.

- [ ] **Step 3: Add `_parse_ip()` to `data_mlb.py`**

Insert after the `MLB_BASE` constant (around line 16), before the first function:

```python
def _parse_ip(ip_str) -> float:
    """
    Convert MLB API innings-pitched string to decimal innings.
    '6.2' means 6 full innings + 2 outs = 6 + 2/3 = 6.667.
    '3.0' means 3 full innings = 3.0.
    """
    try:
        s = str(ip_str).strip()
        if not s or s in ("None", ""):
            return 0.0
        parts = s.split(".")
        innings = int(parts[0])
        outs = int(parts[1]) if len(parts) > 1 else 0
        return innings + outs / 3.0
    except Exception:
        return 0.0
```

- [ ] **Step 4: Add `fetch_bullpen_recent_usage()` to `data_mlb.py`**

Insert after `fetch_pitcher_rest()` (after line ~301):

```python
def fetch_bullpen_recent_usage(team_id: int, as_of_date=None) -> dict:
    """
    Returns recent bullpen workload for a team.
    Fetches last 7 days of completed games, sums relief innings (all pitchers after the starter).
    Returns: {
        "ip_last_3": float,   # bullpen IP in last 3 days
        "ip_last_5": float,   # bullpen IP in last 5 days
        "games_last_3": int,  # games played in last 3 days
        "games_last_5": int,  # games played in last 5 days
    }
    """
    today = as_of_date or date.today()
    start = today - timedelta(days=7)
    end = today - timedelta(days=1)

    url = (
        f"{MLB_BASE}/schedule"
        f"?teamId={team_id}&startDate={start}&endDate={end}"
        f"&sportId=1&gameType=R&hydrate=boxscore"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching bullpen usage for team {team_id}: {e}")
        return {"ip_last_3": 0.0, "ip_last_5": 0.0, "games_last_3": 0, "games_last_5": 0}

    games_seen = []

    for date_entry in data.get("dates", []):
        game_date_str = date_entry.get("date", "")
        try:
            game_date = date.fromisoformat(game_date_str)
        except ValueError:
            continue
        days_ago = (today - game_date).days

        for game in date_entry.get("games", []):
            if game.get("status", {}).get("abstractGameState") != "Final":
                continue

            # Determine which side this team played on
            home_id = game.get("teams", {}).get("home", {}).get("team", {}).get("id")
            away_id = game.get("teams", {}).get("away", {}).get("team", {}).get("id")
            if team_id == home_id:
                side = "home"
            elif team_id == away_id:
                side = "away"
            else:
                continue

            team_data = game.get("teams", {}).get(side, {})
            pitcher_ids = team_data.get("pitchers", [])
            players = team_data.get("players", {})

            # Sum IP for all pitchers after the starter (index 1+)
            relief_ip = 0.0
            for pid in pitcher_ids[1:]:
                player = players.get(f"ID{pid}", {})
                ip_str = player.get("stats", {}).get("pitching", {}).get("inningsPitched", "0.0")
                relief_ip += _parse_ip(ip_str)

            games_seen.append({"days_ago": days_ago, "bullpen_ip": relief_ip})

    ip_last_3 = sum(g["bullpen_ip"] for g in games_seen if g["days_ago"] <= 3)
    ip_last_5 = sum(g["bullpen_ip"] for g in games_seen if g["days_ago"] <= 5)
    games_last_3 = sum(1 for g in games_seen if g["days_ago"] <= 3)
    games_last_5 = sum(1 for g in games_seen if g["days_ago"] <= 5)

    return {
        "ip_last_3": round(ip_last_3, 2),
        "ip_last_5": round(ip_last_5, 2),
        "games_last_3": games_last_3,
        "games_last_5": games_last_5,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue && python3 -m pytest tests/test_bullpen_fatigue.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add data_mlb.py tests/test_bullpen_fatigue.py
git commit -m "feat: add fetch_bullpen_recent_usage() and _parse_ip() to data_mlb.py"
```

---

## Task 2: Apply Fatigue Penalty in `score_bullpen()`

**Files:**
- Modify: `analysis.py` (lines 181–226)
- Modify: `tests/test_bullpen_fatigue.py`

- [ ] **Step 1: Write failing tests for fatigue penalty**

Append to `tests/test_bullpen_fatigue.py`:

```python
def _make_bullpen_game(home_era=4.00, away_era=4.00,
                       home_whip=1.30, away_whip=1.30,
                       home_k9=8.5, away_k9=8.5,
                       home_sv=20, home_svo=25,
                       away_sv=20, away_svo=25,
                       home_usage=None, away_usage=None):
    """Helper: build a game dict for score_bullpen tests."""
    return {
        "home_pitching": {
            "era": home_era, "whip": home_whip, "k_per_9": home_k9,
            "saves": home_sv, "save_opportunities": home_svo,
        },
        "away_pitching": {
            "era": away_era, "whip": away_whip, "k_per_9": away_k9,
            "saves": away_sv, "save_opportunities": away_svo,
        },
        "home_bullpen_usage": home_usage or {},
        "away_bullpen_usage": away_usage or {},
    }


def test_score_bullpen_no_fatigue_unaffected():
    """Equal bullpens, no fatigue → score near 0."""
    import analysis
    game = _make_bullpen_game()
    result = analysis.score_bullpen(game)
    assert abs(result["score"]) < 0.05


def test_score_bullpen_home_fatigue_reduces_home_edge():
    """Home bullpen fatigued (ip_last_3 > 12) → score shifts negative (away advantage)."""
    import analysis
    game = _make_bullpen_game(
        home_era=3.80, away_era=4.20,  # home slightly better on paper
        home_usage={"ip_last_3": 14.0, "ip_last_5": 18.0},
        away_usage={},
    )
    result = analysis.score_bullpen(game)
    # Without fatigue, home ERA/WHIP advantage would give positive score.
    # Heavy fatigue penalty (-0.15) should push it negative or near zero.
    assert result["score"] < 0.05
    assert "fatigue" in result["edge"].lower() or result["score"] < 0.0


def test_score_bullpen_away_fatigue_benefits_home():
    """Away bullpen fatigued → score shifts positive (home advantage)."""
    import analysis
    game = _make_bullpen_game(
        away_usage={"ip_last_3": 13.0, "ip_last_5": 17.0},
        home_usage={},
    )
    result = analysis.score_bullpen(game)
    assert result["score"] > 0.05


def test_score_bullpen_moderate_fatigue():
    """Moderate fatigue (ip_last_3 between 8 and 12) → smaller penalty (-0.08)."""
    import analysis
    # Equal bullpens, away has moderate fatigue
    game = _make_bullpen_game(
        away_usage={"ip_last_3": 10.0, "ip_last_5": 14.0},
        home_usage={},
    )
    result = analysis.score_bullpen(game)
    # Should be positive but less extreme than heavy fatigue
    assert 0.02 < result["score"] < 0.15


def test_score_bullpen_fatigue_detail_included():
    """Fatigue data appears in detail dict."""
    import analysis
    game = _make_bullpen_game(
        home_usage={"ip_last_3": 13.0, "ip_last_5": 17.0},
        away_usage={"ip_last_3": 2.0, "ip_last_5": 4.0},
    )
    result = analysis.score_bullpen(game)
    assert "home_bp_ip_last_3" in result["detail"]
    assert "away_bp_ip_last_3" in result["detail"]
    assert result["detail"]["home_bp_ip_last_3"] == 13.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue && python3 -m pytest tests/test_bullpen_fatigue.py::test_score_bullpen_home_fatigue_reduces_home_edge tests/test_bullpen_fatigue.py::test_score_bullpen_away_fatigue_benefits_home tests/test_bullpen_fatigue.py::test_score_bullpen_fatigue_detail_included -v
```

Expected: FAIL or partial pass (the no-fatigue test may pass; others fail because fatigue isn't applied yet).

- [ ] **Step 3: Update `score_bullpen()` in `analysis.py`**

Replace the entire `score_bullpen` function (lines 181–226):

```python
def score_bullpen(game: dict) -> dict:
    """
    BULLPEN AGENT — Compare team bullpen strength with fatigue adjustment.
    Factors: season ERA, WHIP, K/9, save%, plus recent workload (last 3 days IP).

    Fatigue thresholds (ip_last_3):
      ≤ 8.0  → no penalty
      8–12   → -0.08 (moderate)
      > 12   → -0.15 (heavy)
    Positive score = home edge; negative = away edge.
    """
    home_bp = game.get("home_pitching", {})
    away_bp = game.get("away_pitching", {})

    if not home_bp or not away_bp:
        return {"score": 0.0, "edge": "Insufficient bullpen data", "detail": {}}

    era_diff = _safe(away_bp.get("era")) - _safe(home_bp.get("era"))
    whip_diff = _safe(away_bp.get("whip")) - _safe(home_bp.get("whip"))
    k9_diff = _safe(home_bp.get("k_per_9")) - _safe(away_bp.get("k_per_9"))

    home_sv = _safe(home_bp.get("saves"))
    home_svo = max(_safe(home_bp.get("save_opportunities")), 1)
    away_sv = _safe(away_bp.get("saves"))
    away_svo = max(_safe(away_bp.get("save_opportunities")), 1)
    save_pct_diff = (home_sv / home_svo) - (away_sv / away_svo)

    raw = (
        era_diff * 0.30 +
        whip_diff * 0.25 +
        k9_diff * 0.03 +
        save_pct_diff * 0.5
    )
    score = raw / 2.0

    # Fatigue adjustment
    home_usage = game.get("home_bullpen_usage", {})
    away_usage = game.get("away_bullpen_usage", {})

    def _fatigue_penalty(usage: dict) -> float:
        ip3 = usage.get("ip_last_3", 0.0) if usage else 0.0
        if ip3 > 12.0:
            return 0.15
        elif ip3 > 8.0:
            return 0.08
        return 0.0

    home_penalty = _fatigue_penalty(home_usage)
    away_penalty = _fatigue_penalty(away_usage)
    score += away_penalty - home_penalty

    score = _clamp(score)

    # Build edge description
    fatigue_notes = []
    if home_penalty > 0:
        level = "heavily" if home_penalty >= 0.15 else "moderately"
        fatigue_notes.append(f"home pen {level} fatigued ({home_usage.get('ip_last_3', 0):.1f} IP/3d)")
    if away_penalty > 0:
        level = "heavily" if away_penalty >= 0.15 else "moderately"
        fatigue_notes.append(f"away pen {level} fatigued ({away_usage.get('ip_last_3', 0):.1f} IP/3d)")

    if score > 0.15:
        edge = "Home bullpen is stronger"
    elif score < -0.15:
        edge = "Away bullpen is stronger"
    else:
        edge = "Bullpens are comparable"

    if fatigue_notes:
        edge += f" — {', '.join(fatigue_notes)}"

    return {
        "score": round(score, 3),
        "edge": edge,
        "detail": {
            "home_bp_era": home_bp.get("era"),
            "away_bp_era": away_bp.get("era"),
            "home_bp_ip_last_3": home_usage.get("ip_last_3", 0.0),
            "away_bp_ip_last_3": away_usage.get("ip_last_3", 0.0),
        }
    }
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue && python3 -m pytest tests/ -v
```

Expected: all PASS (23 existing + new bullpen tests).

- [ ] **Step 5: Commit**

```bash
git add analysis.py tests/test_bullpen_fatigue.py
git commit -m "feat: bullpen fatigue penalty in score_bullpen() — heavy >12 IP/3d, moderate >8 IP/3d"
```

---

## Task 3: Wire Bullpen Usage into `enrich_games()`

**Files:**
- Modify: `data_mlb.py` (lines ~555–577, the enrichment loop)

- [ ] **Step 1: Add bullpen usage fetch to `enrich_games()`**

In `data_mlb.py`, find the enrichment loop where `home_pitching` and `away_pitching` are fetched (lines ~555–557):

```python
        # Fetch team pitching / bullpen
        g["away_pitching"] = fetch_team_pitching(g["away_team_mlb_id"])
        g["home_pitching"] = fetch_team_pitching(g["home_team_mlb_id"])
```

Add two lines directly after:

```python
        # Fetch bullpen recent usage (fatigue signal)
        g["away_bullpen_usage"] = fetch_bullpen_recent_usage(g["away_team_mlb_id"])
        g["home_bullpen_usage"] = fetch_bullpen_recent_usage(g["home_team_mlb_id"])
```

- [ ] **Step 2: Smoke test the live engine**

```bash
cd /Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue && python3 engine.py --test 2>&1 | head -40
```

Expected: runs without error. Look for `[DATA] Enriched N games` and bullpen data flowing through. No crash = pass.

- [ ] **Step 3: Run all tests**

```bash
cd /Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue && python3 -m pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add data_mlb.py
git commit -m "feat: wire fetch_bullpen_recent_usage into enrich_games() for live scoring"
```

---

## Post-Implementation Verification

After all tasks:

```bash
# All tests pass
python3 -m pytest tests/ -v

# Engine runs cleanly
python3 engine.py --test

# Imports clean
python3 -c "import data_mlb; import analysis; print('OK')"
```

Remove "Bullpen fatigue (recent usage last 3-7 days) not yet implemented" from `CLAUDE.md` Known Limitations.
