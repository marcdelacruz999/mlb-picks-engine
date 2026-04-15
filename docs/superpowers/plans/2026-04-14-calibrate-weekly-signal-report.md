# Weekly Signal Calibration Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `calibrate.py` — a standalone weekly script that analyzes agent signal performance vs outcomes, generates a Discord calibration report, and optionally applies weight changes to `config.py`.

**Architecture:** Single script with argparse entry point. Pure functions for each stage: DB query → signal parsing → win-rate analysis → weight suggestions → Discord embed. The `--apply` flag triggers config.py mutation + git commit. All logic is self-contained — no imports from engine.py or analysis.py.

**Tech Stack:** Python 3.9, sqlite3, re, argparse, requests (Discord webhook), subprocess (git commit), config.py (WEIGHTS read/write)

---

### Task 1: DB Query Layer

**Files:**
- Create: `calibrate.py` (initial skeleton + fetch function)
- Create: `tests/test_calibrate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibrate.py
import sqlite3
import tempfile
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def _make_db(picks):
    """Create a temp SQLite DB with the picks table populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE picks (
            id INTEGER PRIMARY KEY,
            pick_type TEXT,
            pick_team TEXT,
            confidence INTEGER,
            status TEXT,
            edge_score REAL,
            edge_pitching TEXT,
            edge_offense TEXT,
            edge_bullpen TEXT,
            edge_advanced TEXT,
            edge_market TEXT,
            edge_weather TEXT,
            notes TEXT,
            ml_odds INTEGER,
            ou_odds INTEGER,
            ev_score REAL,
            discord_sent INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    for p in picks:
        conn.execute("""
            INSERT INTO picks (pick_type, pick_team, confidence, status,
                edge_score, edge_pitching, edge_offense, edge_bullpen,
                edge_advanced, edge_market, edge_weather, notes,
                ml_odds, ou_odds, ev_score, discord_sent, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p.get("pick_type","moneyline"), p.get("pick_team","Team A"),
            p.get("confidence",7), p.get("status","won"),
            p.get("edge_score",0.20),
            p.get("edge_pitching",""), p.get("edge_offense",""),
            p.get("edge_bullpen",""), p.get("edge_advanced",""),
            p.get("edge_market",""), p.get("edge_weather",""),
            p.get("notes",""), p.get("ml_odds",-110), p.get("ou_odds",None),
            p.get("ev_score",0.05), p.get("discord_sent",1),
            p.get("created_at","2026-04-14 08:00:00"),
        ))
    conn.commit()
    return conn


def test_fetch_graded_picks_returns_sent_graded_only():
    from calibrate import fetch_graded_picks
    conn = _make_db([
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "pending", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "won", "discord_sent": 0, "created_at": "2026-04-14 08:00:00"},
        {"status": "lost", "discord_sent": 1, "created_at": "2026-04-08 08:00:00"},
    ])
    rows = fetch_graded_picks(conn, days=7, _now="2026-04-14 23:59:59")
    assert len(rows) == 2
    statuses = {r["status"] for r in rows}
    assert statuses == {"won", "lost"}


def test_fetch_graded_picks_window():
    from calibrate import fetch_graded_picks
    conn = _make_db([
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-06 08:00:00"},
    ])
    # 7-day window from Apr 14 → only Apr 14 pick qualifies
    rows = fetch_graded_picks(conn, days=7, _now="2026-04-14 23:59:59")
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_calibrate.py::test_fetch_graded_picks_returns_sent_graded_only -v
```

Expected: `ModuleNotFoundError: No module named 'calibrate'`

- [ ] **Step 3: Write the minimal implementation**

Create `calibrate.py`:

```python
"""
calibrate.py — Weekly Signal Calibration Report

Usage:
    python3 calibrate.py             # analyze + post Discord report
    python3 calibrate.py --apply     # apply suggested weights to config.py + commit
    python3 calibrate.py --days N    # override lookback window (default 7)
    python3 calibrate.py --test      # dry run, print to stdout, no Discord send
"""

import sqlite3
import re
import json
import argparse
import subprocess
from datetime import datetime, timedelta
from typing import Optional

import config


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_graded_picks(conn: sqlite3.Connection, days: int = 7,
                       _now: Optional[str] = None) -> list[dict]:
    """Return all discord-sent, fully-graded picks within the last N days."""
    if _now is None:
        _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff_dt = datetime.strptime(_now, "%Y-%m-%d %H:%M:%S") - timedelta(days=days)
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute("""
        SELECT pick_type, pick_team, confidence, status, edge_score,
               edge_pitching, edge_offense, edge_bullpen, edge_advanced,
               edge_market, edge_weather, notes, ml_odds, ou_odds, ev_score,
               created_at
        FROM picks
        WHERE discord_sent = 1
          AND status IN ('won', 'lost', 'push')
          AND created_at >= ?
    """, (cutoff,)).fetchall()

    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_calibrate.py::test_fetch_graded_picks_returns_sent_graded_only tests/test_calibrate.py::test_fetch_graded_picks_window -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add calibrate.py tests/test_calibrate.py
git commit -m "feat: calibrate.py scaffold + fetch_graded_picks with window filter"
```

---

### Task 2: Signal Parser

**Files:**
- Modify: `calibrate.py` — add `parse_signals(pick)`
- Modify: `tests/test_calibrate.py` — add signal parsing tests

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_calibrate.py`:

```python
def test_parse_signals_sp_home_advantage():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Home Team",
        "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
        "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
        "edge_market": "Market edge: 1.5%", "edge_weather": "Clear skies, wind 8 mph out",
        "notes": "",
    }
    sigs = parse_signals(pick)
    assert sigs["sp_home_advantage"] is True
    assert sigs["sp_away_advantage"] is False


def test_parse_signals_bullpen_era_bad():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Home Team",
        "edge_pitching": "", "edge_offense": "",
        "edge_bullpen": "Home top pen (7d): 3 appearances, 5.80 ERA. Away top pen (7d): 2 appearances, 3.20 ERA.",
        "edge_advanced": "", "edge_market": "Market edge: 3.0%",
        "edge_weather": "Wind 10 mph out to CF", "notes": "",
    }
    sigs = parse_signals(pick)
    assert sigs["bullpen_home_era_bad"] is True
    assert sigs["bullpen_away_era_bad"] is False


def test_parse_signals_rust_weak_pen_combo():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Home Team",
        "edge_pitching": "Home SP extended layoff (10 days rest). Away SP recent start.",
        "edge_offense": "",
        "edge_bullpen": "Home top pen (7d): 2 appearances, 6.10 ERA.",
        "edge_advanced": "", "edge_market": "Market edge: 2.5%",
        "edge_weather": "", "notes": "",
        "_pick_side": "home",
    }
    sigs = parse_signals(pick)
    assert sigs["sp_home_layoff"] is True
    assert sigs["bullpen_home_era_bad"] is True
    assert sigs["rust_weak_pen_home"] is True
    assert sigs["rust_weak_pen_away"] is False


def test_parse_signals_market_buckets():
    from calibrate import parse_signals
    def _pick(market_text):
        return {
            "pick_type": "moneyline", "pick_team": "Team",
            "edge_pitching": "", "edge_offense": "", "edge_bullpen": "",
            "edge_advanced": "", "edge_market": market_text,
            "edge_weather": "", "notes": "", "_pick_side": "home",
        }
    assert parse_signals(_pick("Market edge: 1.5%"))["market_edge_low"] is True
    assert parse_signals(_pick("Market edge: 3.0%"))["market_edge_mid"] is True
    assert parse_signals(_pick("Market edge: 5.2%"))["market_edge_high"] is True


def test_parse_signals_wind_strong():
    from calibrate import parse_signals
    pick = {
        "pick_type": "moneyline", "pick_team": "Team",
        "edge_pitching": "", "edge_offense": "", "edge_bullpen": "",
        "edge_advanced": "", "edge_market": "",
        "edge_weather": "Wind 15 mph out to left field. Clear skies.",
        "notes": "", "_pick_side": "home",
    }
    sigs = parse_signals(pick)
    assert sigs["wind_strong"] is True
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/test_calibrate.py::test_parse_signals_sp_home_advantage -v
```

Expected: `AttributeError: module 'calibrate' has no attribute 'parse_signals'`

- [ ] **Step 3: Implement `parse_signals`**

Add to `calibrate.py` after `fetch_graded_picks`:

```python
def _parse_era(text: str, side: str) -> Optional[float]:
    """Extract ERA float from bullpen edge text for given side (home/away)."""
    pattern = rf"{side.capitalize()} top pen \(7d\):.*?([\d]+\.[\d]+) ERA"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _parse_market_pct(text: str) -> Optional[float]:
    """Extract market edge percentage from edge_market text."""
    m = re.search(r"Market edge:\s*([\d.]+)%", text or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _parse_wind_mph(text: str) -> Optional[float]:
    """Extract wind speed from edge_weather text."""
    m = re.search(r"Wind\s+([\d]+)\s*mph", text or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def parse_signals(pick: dict) -> dict:
    """
    Parse a pick dict into boolean signal flags via string matching.
    pick may have a '_pick_side' key ('home'/'away') injected by the caller;
    otherwise we infer from pick_type + pick_team (not always possible).
    """
    ep = pick.get("edge_pitching") or ""
    eo = pick.get("edge_offense") or ""
    eb = pick.get("edge_bullpen") or ""
    ea = pick.get("edge_advanced") or ""
    em = pick.get("edge_market") or ""
    ew = pick.get("edge_weather") or ""
    notes = pick.get("notes") or ""

    # SP signals
    sp_home_adv = bool(re.search(r"Home SP.*clear pitching advantage", ep, re.IGNORECASE))
    sp_away_adv = bool(re.search(r"Away SP.*clear pitching advantage", ep, re.IGNORECASE))
    sp_home_layoff = bool(re.search(r"Home SP extended layoff", ep, re.IGNORECASE))
    sp_away_layoff = bool(re.search(r"Away SP extended layoff", ep, re.IGNORECASE))

    # Offense signals
    offense_home = bool(re.search(r"Home lineup has offensive advantage", eo, re.IGNORECASE))
    offense_away = bool(re.search(r"Away lineup has offensive advantage", eo, re.IGNORECASE))

    # Bullpen signals
    bp_home_stronger = bool(re.search(r"Home bullpen is stronger", eb, re.IGNORECASE))
    bp_away_stronger = bool(re.search(r"Away bullpen is stronger", eb, re.IGNORECASE))
    home_era = _parse_era(eb, "home")
    away_era = _parse_era(eb, "away")
    bp_home_era_bad = home_era is not None and home_era > 5.0
    bp_away_era_bad = away_era is not None and away_era > 5.0

    # Advanced signals
    adv_barrel = bool(re.search(r"barrel rate advantage", ea, re.IGNORECASE))
    adv_hardhit = bool(re.search(r"hard-hit rate edge", ea, re.IGNORECASE))
    adv_xwoba = bool(re.search(r"xwOBA", ea, re.IGNORECASE))

    # Market bucket
    mkt_pct = _parse_market_pct(em)
    mkt_low = mkt_pct is not None and mkt_pct < 2.0
    mkt_mid = mkt_pct is not None and 2.0 <= mkt_pct <= 4.0
    mkt_high = mkt_pct is not None and mkt_pct > 4.0

    # Other
    lineup_confirmed = bool(re.search(r"confirmed", notes, re.IGNORECASE))
    rain_flag = bool(re.search(r"Rain", ew, re.IGNORECASE))
    wind_mph = _parse_wind_mph(ew)
    wind_strong = wind_mph is not None and wind_mph > 12.0

    # Combo flags — need pick_side
    pick_side = pick.get("_pick_side", "")
    rust_home = sp_home_layoff and bp_home_era_bad and pick_side == "home"
    rust_away = sp_away_layoff and bp_away_era_bad and pick_side == "away"

    return {
        "sp_home_advantage": sp_home_adv,
        "sp_away_advantage": sp_away_adv,
        "sp_home_layoff": sp_home_layoff,
        "sp_away_layoff": sp_away_layoff,
        "offense_home_edge": offense_home,
        "offense_away_edge": offense_away,
        "bullpen_home_stronger": bp_home_stronger,
        "bullpen_away_stronger": bp_away_stronger,
        "bullpen_home_era_bad": bp_home_era_bad,
        "bullpen_away_era_bad": bp_away_era_bad,
        "advanced_barrel": adv_barrel,
        "advanced_hardhit": adv_hardhit,
        "advanced_xwoba": adv_xwoba,
        "market_edge_low": mkt_low,
        "market_edge_mid": mkt_mid,
        "market_edge_high": mkt_high,
        "lineup_confirmed": lineup_confirmed,
        "rain_flag": rain_flag,
        "wind_strong": wind_strong,
        "rust_weak_pen_home": rust_home,
        "rust_weak_pen_away": rust_away,
    }
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_calibrate.py -k "parse_signals" -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add calibrate.py tests/test_calibrate.py
git commit -m "feat: calibrate signal parser — 19 flags + rust+pen combo"
```

---

### Task 3: Win-Rate Analysis + Weight Suggestions

**Files:**
- Modify: `calibrate.py` — add `analyze_signals()` and `suggest_weights()`
- Modify: `tests/test_calibrate.py` — add analysis tests

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_calibrate.py`:

```python
def test_analyze_signals_win_rate():
    from calibrate import analyze_signals
    # 3 picks: 2 won, 1 lost. All have sp_home_advantage=True.
    picks = [
        {"pick_type": "moneyline", "status": "won",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
        {"pick_type": "moneyline", "status": "won",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
        {"pick_type": "moneyline", "status": "lost",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
    ]
    result = analyze_signals(picks)
    assert result["baseline_win_rate"] == round(2/3, 4)
    sp_row = result["signal_table"]["sp_home_advantage"]
    assert sp_row["n"] == 3
    assert sp_row["wins"] == 2
    assert sp_row["losses"] == 1
    assert sp_row["win_rate"] == round(2/3, 4)


def test_analyze_signals_min_n_filter():
    from calibrate import analyze_signals
    # Only 2 picks with a signal — should not appear in table (min N=3)
    picks = [
        {"pick_type": "moneyline", "status": "won",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
        {"pick_type": "moneyline", "status": "lost",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "edge_offense": "", "edge_bullpen": "", "edge_advanced": "",
         "edge_market": "", "edge_weather": "", "notes": "", "_pick_side": "home"},
    ]
    result = analyze_signals(picks)
    assert "sp_home_advantage" not in result["signal_table"]


def test_suggest_weights_nudge_up():
    from calibrate import suggest_weights
    current = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
               "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    # offense signal winning at 90% vs 60% baseline — should suggest +0.02 to offense
    signal_table = {
        "offense_home_edge": {"n": 6, "wins": 9, "losses": 1, "win_rate": 0.90, "delta": 0.30},
    }
    baseline = 0.60
    suggestions = suggest_weights(current, signal_table, baseline, n_picks=12)
    assert suggestions["offense"] == round(0.23 + 0.02, 4)


def test_suggest_weights_no_change_when_low_n():
    from calibrate import suggest_weights
    current = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
               "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    # offense signal only 4 picks — below N>=5 threshold for suggestions
    signal_table = {
        "offense_home_edge": {"n": 4, "wins": 4, "losses": 0, "win_rate": 1.0, "delta": 0.40},
    }
    baseline = 0.60
    suggestions = suggest_weights(current, signal_table, baseline, n_picks=10)
    assert suggestions == current


def test_suggest_weights_normalizes_to_1():
    from calibrate import suggest_weights
    current = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
               "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    signal_table = {
        "offense_home_edge": {"n": 6, "wins": 9, "losses": 1, "win_rate": 0.90, "delta": 0.30},
    }
    suggestions = suggest_weights(current, signal_table, 0.60, n_picks=12)
    total = round(sum(suggestions.values()), 4)
    assert total == 1.0, f"Weights sum to {total}, expected 1.0"
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/test_calibrate.py::test_analyze_signals_win_rate -v
```

Expected: `AttributeError: module 'calibrate' has no attribute 'analyze_signals'`

- [ ] **Step 3: Implement `analyze_signals` and `suggest_weights`**

Add to `calibrate.py`:

```python
# Signals that map to each agent weight key
_SIGNAL_TO_AGENT = {
    "sp_home_advantage":   "pitching",
    "sp_away_advantage":   "pitching",
    "sp_home_layoff":      "pitching",
    "sp_away_layoff":      "pitching",
    "offense_home_edge":   "offense",
    "offense_away_edge":   "offense",
    "bullpen_home_stronger": "bullpen",
    "bullpen_away_stronger": "bullpen",
    "bullpen_home_era_bad":  "bullpen",
    "bullpen_away_era_bad":  "bullpen",
    "advanced_barrel":     "advanced",
    "advanced_hardhit":    "advanced",
    "advanced_xwoba":      "advanced",
    "market_edge_low":     "market",
    "market_edge_mid":     "market",
    "market_edge_high":    "market",
    "rain_flag":           "weather",
    "wind_strong":         "weather",
    "rust_weak_pen_home":  "pitching",
    "rust_weak_pen_away":  "pitching",
}

MIN_SIGNAL_N = 3   # below this — noise, don't show
MIN_SUGGEST_N = 5  # below this — don't suggest weight changes
DIVERGE_THRESHOLD = 0.08   # signal win rate must differ by this much to trigger nudge
NUDGE = 0.02
MAX_NUDGE = 0.03
MIN_TOTAL_ADJUSTMENT = 0.03  # ignore noise commits below this magnitude


def analyze_signals(picks: list[dict]) -> dict:
    """
    For each signal flag, compute N, W, L, win_rate, delta vs baseline.
    Returns {baseline_win_rate, signal_table, pick_count, ml_record, ou_record}.
    """
    if not picks:
        return {"baseline_win_rate": 0.0, "signal_table": {}, "pick_count": 0,
                "ml_record": (0, 0), "ou_record": (0, 0)}

    # Baseline — all picks
    total = len(picks)
    total_wins = sum(1 for p in picks if p["status"] == "won")
    baseline = round(total_wins / total, 4) if total > 0 else 0.0

    # ML vs O/U records
    ml_picks = [p for p in picks if p.get("pick_type") in ("moneyline", "f5_ml")]
    ou_picks  = [p for p in picks if p.get("pick_type") in ("over", "under")]
    ml_rec = (sum(1 for p in ml_picks if p["status"] == "won"),
               sum(1 for p in ml_picks if p["status"] == "lost"))
    ou_rec = (sum(1 for p in ou_picks if p["status"] == "won"),
               sum(1 for p in ou_picks if p["status"] == "lost"))

    # Per-signal tallies
    tallies: dict[str, dict] = {}
    for pick in picks:
        sigs = parse_signals(pick)
        for sig, present in sigs.items():
            if not present:
                continue
            if sig not in tallies:
                tallies[sig] = {"n": 0, "wins": 0, "losses": 0}
            tallies[sig]["n"] += 1
            if pick["status"] == "won":
                tallies[sig]["wins"] += 1
            elif pick["status"] == "lost":
                tallies[sig]["losses"] += 1

    # Build signal_table — filter by MIN_SIGNAL_N
    signal_table = {}
    for sig, t in tallies.items():
        if t["n"] < MIN_SIGNAL_N:
            continue
        win_rate = round(t["wins"] / t["n"], 4) if t["n"] > 0 else 0.0
        delta = round(win_rate - baseline, 4)
        signal_table[sig] = {
            "n": t["n"],
            "wins": t["wins"],
            "losses": t["losses"],
            "win_rate": win_rate,
            "delta": delta,
        }

    return {
        "baseline_win_rate": baseline,
        "signal_table": signal_table,
        "pick_count": total,
        "ml_record": ml_rec,
        "ou_record": ou_rec,
    }


def suggest_weights(current: dict, signal_table: dict,
                    baseline: float, n_picks: int) -> dict:
    """
    Suggest agent weight adjustments based on signal performance.
    Returns a new weights dict (normalized to 1.0), or current if no changes warranted.
    """
    adjustments: dict[str, float] = {k: 0.0 for k in current}

    for sig, row in signal_table.items():
        if row["n"] < MIN_SUGGEST_N:
            continue
        if abs(row["delta"]) <= DIVERGE_THRESHOLD:
            continue
        agent = _SIGNAL_TO_AGENT.get(sig)
        if agent is None or agent not in current:
            continue
        nudge = NUDGE if row["delta"] > 0 else -NUDGE
        adjustments[agent] += nudge

    # Cap each agent adjustment at ±MAX_NUDGE
    for agent in adjustments:
        adjustments[agent] = max(-MAX_NUDGE, min(MAX_NUDGE, adjustments[agent]))

    total_adj = sum(abs(v) for v in adjustments.values())
    if total_adj < MIN_TOTAL_ADJUSTMENT:
        return current  # no meaningful change

    # Apply adjustments
    new_weights = {k: round(current[k] + adjustments[k], 4) for k in current}

    # Clamp to [0.01, 0.50] per weight
    for k in new_weights:
        new_weights[k] = max(0.01, min(0.50, new_weights[k]))

    # Normalize to sum exactly 1.0
    total = sum(new_weights.values())
    if total != 1.0:
        # Distribute remainder to largest weight
        diff = round(1.0 - total, 4)
        largest = max(new_weights, key=lambda k: new_weights[k])
        new_weights[largest] = round(new_weights[largest] + diff, 4)

    return new_weights
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_calibrate.py -k "analyze_signals or suggest_weights" -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add calibrate.py tests/test_calibrate.py
git commit -m "feat: calibrate analyze_signals + suggest_weights with nudge/normalize logic"
```

---

### Task 4: Discord Embed Builder

**Files:**
- Modify: `calibrate.py` — add `build_embed()` and `post_to_discord()`
- Modify: `tests/test_calibrate.py` — add embed structure tests

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_calibrate.py`:

```python
def test_build_embed_structure():
    from calibrate import build_embed
    analysis = {
        "baseline_win_rate": 0.75,
        "pick_count": 12,
        "ml_record": (9, 3),
        "ou_record": (0, 0),
        "signal_table": {
            "offense_home_edge": {"n": 8, "wins": 7, "losses": 1,
                                   "win_rate": 0.875, "delta": 0.125},
            "rust_weak_pen_home": {"n": 2, "wins": 0, "losses": 2,
                                    "win_rate": 0.0, "delta": -0.75},
        },
    }
    current_weights = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
                       "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    suggested_weights = {"pitching": 0.22, "offense": 0.25, "bullpen": 0.20,
                          "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.08}
    embed = build_embed(analysis, current_weights, suggested_weights, week_label="Apr 14")

    assert "embeds" in embed
    e = embed["embeds"][0]
    assert "Weekly Calibration" in e["title"]
    assert "9W-3L" in e["description"]
    assert "75.0%" in e["description"]
    # Fields should include signal breakdown and weights
    field_names = [f["name"] for f in e.get("fields", [])]
    assert any("SIGNAL" in n.upper() for n in field_names)
    assert any("WEIGHT" in n.upper() for n in field_names)


def test_build_embed_no_changes_message():
    from calibrate import build_embed
    analysis = {
        "baseline_win_rate": 0.70,
        "pick_count": 5,
        "ml_record": (3, 2),
        "ou_record": (0, 0),
        "signal_table": {},
    }
    weights = {"pitching": 0.22, "offense": 0.23, "bullpen": 0.20,
               "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
    embed = build_embed(analysis, weights, weights, week_label="Apr 14")
    desc = embed["embeds"][0]["description"]
    assert "calibrated" in desc.lower() or "no changes" in desc.lower()
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/test_calibrate.py::test_build_embed_structure -v
```

Expected: `AttributeError: module 'calibrate' has no attribute 'build_embed'`

- [ ] **Step 3: Implement `build_embed` and `post_to_discord`**

Add to `calibrate.py`:

```python
def _signal_label(sig: str) -> str:
    labels = {
        "sp_home_advantage":   "Home SP advantage",
        "sp_away_advantage":   "Away SP advantage",
        "sp_home_layoff":      "Home SP layoff",
        "sp_away_layoff":      "Away SP layoff",
        "offense_home_edge":   "Home offense edge",
        "offense_away_edge":   "Away offense edge",
        "bullpen_home_stronger": "Home bullpen stronger",
        "bullpen_away_stronger": "Away bullpen stronger",
        "bullpen_home_era_bad":  "Home bullpen ERA >5.0",
        "bullpen_away_era_bad":  "Away bullpen ERA >5.0",
        "advanced_barrel":     "Barrel rate edge",
        "advanced_hardhit":    "Hard-hit rate edge",
        "advanced_xwoba":      "xwOBA edge",
        "market_edge_low":     "Market edge <2%",
        "market_edge_mid":     "Market edge 2-4%",
        "market_edge_high":    "Market edge >4%",
        "lineup_confirmed":    "Lineup confirmed",
        "rain_flag":           "Rain flag",
        "wind_strong":         "Wind >12 mph",
        "rust_weak_pen_home":  "Rust + weak pen (home)",
        "rust_weak_pen_away":  "Rust + weak pen (away)",
    }
    return labels.get(sig, sig)


def build_embed(analysis: dict, current_weights: dict,
                suggested_weights: dict, week_label: str) -> dict:
    """Build a Discord webhook payload with the calibration report embed."""
    bl = analysis["baseline_win_rate"]
    n  = analysis["pick_count"]
    ml_w, ml_l = analysis["ml_record"]
    ou_w, ou_l = analysis["ou_record"]
    st = analysis["signal_table"]

    ou_str = f"O/U: {ou_w}W-{ou_l}L" if (ou_w + ou_l) > 0 else "O/U: 0 graded"
    desc_line = (
        f"Record: {ml_w}W-{ml_l}L ({bl*100:.1f}%) | "
        f"{n} picks | ML: {ml_w}W-{ml_l}L | {ou_str}"
    )

    # Signal breakdown field
    if st:
        sig_lines = []
        for sig, row in sorted(st.items(), key=lambda x: x[1]["delta"], reverse=True):
            icon = "✅" if row["delta"] >= 0.05 else ("⚠️" if row["delta"] <= -0.10 else "➡️")
            flag = " ← flagged" if "rust_weak_pen" in sig else ""
            sig_lines.append(
                f"{icon} {_signal_label(sig):<22} "
                f"{row['n']} picks  {row['wins']}W-{row['losses']}L  "
                f"{row['win_rate']*100:.1f}%  ({row['delta']:+.1%}){flag}"
            )
        signal_value = "```\n" + "\n".join(sig_lines) + "\n```"
    else:
        signal_value = "_No signals with N ≥ 3 picks this week._"

    # Weight suggestions field
    weights_changed = current_weights != suggested_weights
    if weights_changed:
        wt_lines = []
        for agent in current_weights:
            cur = current_weights[agent]
            sug = suggested_weights[agent]
            diff = sug - cur
            if abs(diff) >= 0.01:
                wt_lines.append(f"{agent:<10} {cur:.2f} → {sug:.2f}  ({diff:+.2f})")
            else:
                wt_lines.append(f"{agent:<10} {cur:.2f}   (hold)")
        wt_lines.append("\nRun: python3 calibrate.py --apply to apply")
        weight_value = "```\n" + "\n".join(wt_lines) + "\n```"
    else:
        weight_value = "_Weights look calibrated — no changes recommended._"

    sep = "━" * 32
    if n < 10:
        desc = f"{desc_line}\n\nNot enough graded picks this week ({n}) — report only, no suggestions."
    elif not weights_changed:
        desc = f"{desc_line}\n\nWeights look calibrated — no changes recommended."
    else:
        desc = desc_line

    embed = {
        "title": f"📊 Weekly Calibration Report — Week of {week_label}",
        "description": f"{sep}\n{desc}",
        "color": 0x2ECC71 if bl >= 0.60 else 0xE74C3C,
        "fields": [
            {"name": "SIGNAL BREAKDOWN", "value": signal_value, "inline": False},
            {"name": "SUGGESTED WEIGHTS", "value": weight_value, "inline": False},
        ],
        "footer": {"text": sep},
    }

    return {"embeds": [embed]}


def post_to_discord(payload: dict) -> bool:
    """POST embed payload to Discord webhook. Returns True on success."""
    import requests
    url = config.DISCORD_WEBHOOK_URL
    resp = requests.post(url, json=payload, timeout=10)
    return resp.status_code in (200, 204)
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_calibrate.py::test_build_embed_structure tests/test_calibrate.py::test_build_embed_no_changes_message -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add calibrate.py tests/test_calibrate.py
git commit -m "feat: calibrate build_embed + post_to_discord"
```

---

### Task 5: Calibration Log Writer

**Files:**
- Modify: `calibrate.py` — add `write_calibration_log()`
- Modify: `tests/test_calibrate.py` — add log writer test

- [ ] **Step 1: Write the failing test**

Add to `tests/test_calibrate.py`:

```python
def test_write_calibration_log_appends():
    import tempfile, os, json
    from calibrate import write_calibration_log

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        log_path = f.name

    try:
        entry = {
            "date": "2026-04-21",
            "window_days": 7,
            "pick_count": 12,
            "win_rate": 0.75,
            "signal_table": {"offense_home_edge": {"n": 8, "wins": 7, "losses": 1,
                                                    "win_rate": 0.875, "delta": 0.125}},
            "weights_before": {"pitching": 0.22, "offense": 0.23},
            "weights_after":  {"pitching": 0.22, "offense": 0.25},
            "applied": False,
        }
        write_calibration_log(entry, log_path=log_path)
        write_calibration_log(entry, log_path=log_path)

        lines = open(log_path).readlines()
        assert len(lines) == 2
        parsed = json.loads(lines[0])
        assert parsed["win_rate"] == 0.75
        assert parsed["applied"] is False
    finally:
        os.unlink(log_path)
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/test_calibrate.py::test_write_calibration_log_appends -v
```

Expected: `AttributeError: module 'calibrate' has no attribute 'write_calibration_log'`

- [ ] **Step 3: Implement `write_calibration_log`**

Add to `calibrate.py`:

```python
import os

_DEFAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_log.jsonl")


def write_calibration_log(entry: dict, log_path: str = _DEFAULT_LOG_PATH) -> None:
    """Append a calibration run entry to the JSONL log."""
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
```

- [ ] **Step 4: Run the test**

```bash
python3 -m pytest tests/test_calibrate.py::test_write_calibration_log_appends -v
```

Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add calibrate.py tests/test_calibrate.py
git commit -m "feat: calibrate write_calibration_log append-only JSONL"
```

---

### Task 6: --apply Flag (Config Write + Git Commit)

**Files:**
- Modify: `calibrate.py` — add `apply_weights()` and `_update_config_weights()`
- Modify: `tests/test_calibrate.py` — add apply tests

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_calibrate.py`:

```python
def test_update_config_weights_writes_correctly():
    import tempfile, os
    from calibrate import _update_config_weights

    config_content = '''WEIGHTS = {
    "pitching":    0.22,
    "offense":     0.23,
    "bullpen":     0.20,
    "advanced":    0.13,
    "momentum":    0.07,
    "weather":     0.05,
    "market":      0.10,
}
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(config_content)
        tmp_path = f.name

    try:
        new_weights = {"pitching": 0.20, "offense": 0.25, "bullpen": 0.20,
                       "advanced": 0.13, "momentum": 0.07, "weather": 0.05, "market": 0.10}
        _update_config_weights(new_weights, config_path=tmp_path)
        result = open(tmp_path).read()
        assert '"pitching":    0.20' in result or '"pitching": 0.20' in result
        assert '"offense":     0.25' in result or '"offense": 0.25' in result
    finally:
        os.unlink(tmp_path)


def test_apply_weights_requires_min_picks():
    from calibrate import apply_weights
    # Fewer than 10 picks → should return False with a message
    result = apply_weights(
        picks=[],
        analysis={"baseline_win_rate": 0.0, "signal_table": {}, "pick_count": 3,
                   "ml_record": (2, 1), "ou_record": (0, 0)},
        suggested_weights={"pitching": 0.22},
        current_weights={"pitching": 0.22},
        dry_run=True,
    )
    assert result["applied"] is False
    assert "not enough" in result["reason"].lower()
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/test_calibrate.py::test_update_config_weights_writes_correctly -v
```

Expected: `AttributeError: module 'calibrate' has no attribute '_update_config_weights'`

- [ ] **Step 3: Implement apply logic**

Add to `calibrate.py`:

```python
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
MIN_APPLY_PICKS = 10


def _update_config_weights(new_weights: dict, config_path: str = _CONFIG_PATH) -> None:
    """
    Rewrite the WEIGHTS dict in config.py using line-by-line replacement.
    Matches lines like:    "pitching":    0.22,
    """
    with open(config_path) as f:
        lines = f.readlines()

    updated = []
    for line in lines:
        replaced = False
        for agent, value in new_weights.items():
            pattern = rf'(\s*"{agent}":\s+)[\d.]+,'
            m = re.match(pattern, line)
            if m:
                updated.append(f'{m.group(1)}{value},\n')
                replaced = True
                break
        if not replaced:
            updated.append(line)

    with open(config_path, "w") as f:
        f.writelines(updated)


def apply_weights(picks: list, analysis: dict, suggested_weights: dict,
                  current_weights: dict, dry_run: bool = False) -> dict:
    """
    Apply suggested weights to config.py and commit.
    Returns dict with {applied: bool, reason: str}.
    """
    n = analysis["pick_count"]
    if n < MIN_APPLY_PICKS:
        return {"applied": False,
                "reason": f"not enough graded picks this week ({n}) — need {MIN_APPLY_PICKS}"}

    if suggested_weights == current_weights:
        return {"applied": False, "reason": "no weight changes suggested"}

    if dry_run:
        return {"applied": False, "reason": "dry_run=True, skipped write"}

    # Apply to config.py
    _update_config_weights(suggested_weights)

    # Summary for commit message
    changes = []
    for agent in current_weights:
        diff = round(suggested_weights.get(agent, current_weights[agent]) - current_weights[agent], 4)
        if abs(diff) >= 0.01:
            changes.append(f"{agent} {diff:+.2f}")
    summary = ", ".join(changes) if changes else "minor rebalance"

    date_str = datetime.now().strftime("%Y-%m-%d")
    commit_msg = f"calibration: weekly weight update {date_str} — {summary}"

    subprocess.run(["git", "add", "config.py"], check=True)
    subprocess.run(["git", "commit", "-m", commit_msg], check=True)

    return {"applied": True, "reason": summary}
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_calibrate.py::test_update_config_weights_writes_correctly tests/test_calibrate.py::test_apply_weights_requires_min_picks -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add calibrate.py tests/test_calibrate.py
git commit -m "feat: calibrate --apply logic — _update_config_weights + apply_weights guard"
```

---

### Task 7: CLI Entry Point

**Files:**
- Modify: `calibrate.py` — add `main()` with argparse
- Modify: `tests/test_calibrate.py` — add end-to-end smoke test

- [ ] **Step 1: Write the failing test**

Add to `tests/test_calibrate.py`:

```python
def test_main_test_mode_no_discord(monkeypatch):
    """--test mode runs full pipeline and prints to stdout, no Discord call."""
    import io, sys
    from calibrate import main

    # Patch _open_db to return our test DB
    conn = _make_db([
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 08:00:00",
         "edge_pitching": "Home SP has clear pitching advantage over Away SP.",
         "pick_type": "moneyline"},
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 09:00:00",
         "edge_offense": "Home lineup has offensive advantage over Away.",
         "pick_type": "moneyline"},
        {"status": "lost", "discord_sent": 1, "created_at": "2026-04-14 10:00:00",
         "edge_pitching": "Home SP extended layoff (10 days rest).",
         "edge_bullpen": "Home top pen (7d): 2 appearances, 6.10 ERA.",
         "pick_type": "moneyline"},
    ])
    monkeypatch.setattr("calibrate._open_db", lambda: conn)

    posted = []
    monkeypatch.setattr("calibrate.post_to_discord", lambda p: posted.append(p) or True)

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    main(["--test"])

    output = captured.getvalue()
    assert "Calibration" in output or "Record" in output
    assert len(posted) == 0  # --test should NOT post to Discord
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/test_calibrate.py::test_main_test_mode_no_discord -v
```

Expected: `AttributeError: module 'calibrate' has no attribute 'main'`

- [ ] **Step 3: Implement `main()`**

Add to the bottom of `calibrate.py`:

```python
def main(argv=None):
    parser = argparse.ArgumentParser(description="Weekly signal calibration report")
    parser.add_argument("--apply", action="store_true",
                        help="Apply suggested weights to config.py and commit")
    parser.add_argument("--days", type=int, default=7,
                        help="Lookback window in days (default: 7)")
    parser.add_argument("--test", action="store_true",
                        help="Dry run — print report to stdout, no Discord send")
    args = parser.parse_args(argv)

    conn = _open_db()
    picks = fetch_graded_picks(conn, days=args.days)
    conn.close()

    analysis = analyze_signals(picks)
    current_weights = dict(config.WEIGHTS)
    suggested_weights = suggest_weights(
        current_weights, analysis["signal_table"],
        analysis["baseline_win_rate"], analysis["pick_count"]
    )

    week_label = datetime.now().strftime("%b %-d")
    payload = build_embed(analysis, current_weights, suggested_weights, week_label)

    if args.test:
        # Print embed description to stdout
        e = payload["embeds"][0]
        print(e["title"])
        print(e["description"])
        for field in e.get("fields", []):
            print(f"\n{field['name']}")
            print(field["value"])
        return

    # Post to Discord
    ok = post_to_discord(payload)
    if not ok:
        print("WARNING: Discord post failed")

    # Write log entry
    apply_result = {"applied": False, "reason": "report-only run"}
    if args.apply:
        apply_result = apply_weights(
            picks=picks,
            analysis=analysis,
            suggested_weights=suggested_weights,
            current_weights=current_weights,
        )

    log_entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "window_days": args.days,
        "pick_count": analysis["pick_count"],
        "win_rate": analysis["baseline_win_rate"],
        "signal_table": analysis["signal_table"],
        "weights_before": current_weights,
        "weights_after": suggested_weights if apply_result["applied"] else current_weights,
        "applied": apply_result["applied"],
    }
    write_calibration_log(log_entry)

    status = "applied" if apply_result["applied"] else "report only"
    print(f"Calibration complete ({status}) — {analysis['pick_count']} picks, "
          f"{analysis['baseline_win_rate']*100:.1f}% win rate")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all calibrate tests**

```bash
python3 -m pytest tests/test_calibrate.py -v
```

Expected: all tests pass

- [ ] **Step 5: Smoke test --test mode manually**

```bash
python3 calibrate.py --test --days 7
```

Expected: calibration report printed to stdout, no errors.

- [ ] **Step 6: Commit**

```bash
git add calibrate.py tests/test_calibrate.py
git commit -m "feat: calibrate main() CLI entry point with --test/--apply/--days"
```

---

### Task 8: launchd Plist + .gitignore + CLAUDE.md

**Files:**
- Create: `~/Library/LaunchAgents/com.marc.mlb-picks-engine.calibrate.plist`
- Modify: `.gitignore`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `calibration_log.jsonl` to `.gitignore`**

```bash
echo "calibration_log.jsonl" >> /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.gitignore
```

Verify:
```bash
grep "calibration_log" /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.gitignore
```

Expected: `calibration_log.jsonl`

- [ ] **Step 2: Create the launchd plist**

Create `~/Library/LaunchAgents/com.marc.mlb-picks-engine.calibrate.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.marc.mlb-picks-engine.calibrate</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/run_calibrate.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>2</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/engine.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/engine.log</string>
</dict>
</plist>
```

- [ ] **Step 3: Create `run_calibrate.sh`**

Create `/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/run_calibrate.sh`:

```bash
#!/bin/bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
echo "[$(date)] calibrate.py starting" >> engine.log
/usr/bin/python3 calibrate.py >> engine.log 2>&1
echo "[$(date)] calibrate.py done (exit $?)" >> engine.log
```

```bash
chmod +x /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/run_calibrate.sh
```

- [ ] **Step 4: Load the plist**

```bash
launchctl load ~/Library/LaunchAgents/com.marc.mlb-picks-engine.calibrate.plist
launchctl list | grep calibrate
```

Expected: entry visible with PID `-` (not running, waits for Monday 9 AM)

- [ ] **Step 5: Update CLAUDE.md — add calibrate.py to schedule table**

In the Daily Schedule table in CLAUDE.md, add a row:

```
| Every Monday 9:00 AM | `calibrate.py` | Weekly signal calibration report — posts to Discord, optionally applies weight nudges |
```

In the File Map section, add:

```
calibrate.py       — weekly signal calibration report; parse signals from stored edge text,
                     compute win rates per signal, suggest weight adjustments, post Discord embed
calibration_log.jsonl  — append-only log of weekly calibration runs (gitignored)
```

Also add CLI usage:

```
python3 calibrate.py           # analyze + post Discord report
python3 calibrate.py --apply   # apply suggested weights to config.py + commit
python3 calibrate.py --days N  # override lookback window (default 7)
python3 calibrate.py --test    # dry run, print to stdout, no Discord send
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore CLAUDE.md run_calibrate.sh
git commit -m "feat: calibrate launchd plist + run_calibrate.sh + CLAUDE.md docs"
```

---

### Task 9: Full Test Suite Verification

- [ ] **Step 1: Run all calibrate tests**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_calibrate.py -v
```

Expected: all tests pass

- [ ] **Step 2: Run the full test suite to check for regressions**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: existing tests still pass (3 pre-existing failures are OK — see `docs/testing.md`)

- [ ] **Step 3: Run `calibrate.py --test` end-to-end against real DB**

```bash
python3 calibrate.py --test --days 14
```

Expected: calibration report prints to stdout with signal breakdown. No errors.

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -p  # stage any remaining changes
git commit -m "chore: calibrate.py final cleanup"
```
