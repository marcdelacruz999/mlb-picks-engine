# Nightly Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two nightly Discord messages (ML recap + O/U board) with a single compact, emoji-rich nightly report showing Confidence Picks, ML Board, and O/U Board with final scores and results.

**Architecture:** Add `_format_nightly_report(results, log_entries, sent_picks_by_game)` and `send_nightly_report(results, log_entries, sent_picks_by_game)` to `discord_bot.py`. Update `run_results()` in `engine.py` to call `send_nightly_report()` instead of `send_results()` + inline O/U send. Add `--report` CLI flag to `engine.py` that re-queries graded data from the DB and re-sends the report manually.

**Tech Stack:** Python 3.9, SQLite (via `database.py`), Discord webhooks (`requests`), `zoneinfo`

---

## File Map

- **Modify:** `discord_bot.py` — add `_format_nightly_report()` + `send_nightly_report()`; keep `send_results()` for now (remove at end)
- **Modify:** `engine.py` — replace `send_results()` + inline O/U send with `send_nightly_report()`; add `--report` CLI flag + `run_report()` function
- **Modify:** `tests/test_discord.py` (or create if absent) — unit tests for formatter

---

## Task 1: Write failing tests for `_format_nightly_report`

**Files:**
- Test: `tests/test_nightly_report.py` (create)

- [ ] **Step 1: Create test file with failing tests**

```python
# tests/test_nightly_report.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from discord_bot import _format_nightly_report

# Shared fixtures
RESULTS = {
    "wins": 3,
    "losses": 0,
    "pushes": 0,
    "roi": 100.0,
    "ml_correct": 12,
    "ml_incorrect": 3,
    "ou_correct": 1,
    "ou_incorrect": 5,
    "pick_lines": [
        "✅ ATL Braves (moneyline) — WON",
        "✅ TB Rays (moneyline) — WON",
        "✅ HOU Astros (moneyline) — WON",
    ],
}

LOG_ENTRIES = [
    {
        "mlb_game_id": 1, "away_team": "Miami Marlins", "home_team": "Atlanta Braves",
        "ml_pick_team": "Atlanta Braves", "ml_confidence": 7, "ml_status": "correct",
        "ou_pick": None, "ou_line": None, "ou_status": "none",
        "actual_away_score": 3, "actual_home_score": 6,
    },
    {
        "mlb_game_id": 2, "away_team": "Tampa Bay Rays", "home_team": "Chicago White Sox",
        "ml_pick_team": "Tampa Bay Rays", "ml_confidence": 6, "ml_status": "correct",
        "ou_pick": None, "ou_line": None, "ou_status": "none",
        "actual_away_score": 8, "actual_home_score": 3,
    },
    {
        "mlb_game_id": 3, "away_team": "Kansas City Royals", "home_team": "Detroit Tigers",
        "ml_pick_team": "Detroit Tigers", "ml_confidence": 4, "ml_status": "correct",
        "ou_pick": "under", "ou_line": 8.0, "ou_status": "correct",
        "actual_away_score": 1, "actual_home_score": 2,
    },
    {
        "mlb_game_id": 4, "away_team": "San Francisco Giants", "home_team": "Cincinnati Reds",
        "ml_pick_team": "Cincinnati Reds", "ml_confidence": 8, "ml_status": "correct",
        "ou_pick": "under", "ou_line": 8.5, "ou_status": "incorrect",
        "actual_away_score": 3, "actual_home_score": 8,
    },
]

# Game IDs for sent picks (game_id=1 and game_id=2 map to mlb_game_id 1 and 2)
SENT_PICKS_BY_GAME = {
    10: [{"pick_type": "moneyline", "pick_team": "Atlanta Braves", "confidence": 7,
          "win_probability": 63.0, "ml_odds": -150}],
    20: [{"pick_type": "moneyline", "pick_team": "Tampa Bay Rays", "confidence": 7,
          "win_probability": 52.8, "ml_odds": -100}],
}

# mlb_game_id -> local game_id mapping (needed for sent pick lookup)
MLB_TO_LOCAL = {1: 10, 2: 20, 3: 30, 4: 40}


def test_report_contains_header():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "MLB NIGHTLY REPORT" in msg

def test_report_contains_confidence_section():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "CONFIDENCE PICKS" in msg

def test_report_contains_ml_board_section():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "ML BOARD" in msg

def test_report_contains_ou_board_section():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "O/U BOARD" in msg

def test_confidence_picks_show_result():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "WON" in msg or "✅" in msg

def test_ml_board_shows_all_games():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "Marlins" in msg or "MIA" in msg
    assert "Reds" in msg or "CIN" in msg

def test_ou_board_only_shows_games_with_ou_pick():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    # KC @ DET has an O/U pick; MIA @ ATL does not
    assert "Tigers" in msg or "DET" in msg
    # Rays game has no O/U pick, should not appear in O/U section
    lines = msg.split("\n")
    ou_start = next((i for i, l in enumerate(lines) if "O/U BOARD" in l), None)
    assert ou_start is not None
    ou_section = "\n".join(lines[ou_start:])
    # TB Rays had no O/U pick
    assert "Rays" not in ou_section

def test_ml_record_summary():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "12W-3L" in msg or "12W - 3L" in msg

def test_ou_record_summary():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "1W-5L" in msg or "1W - 5L" in msg

def test_confidence_picks_roi():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "ROI" in msg
    assert "100" in msg

def test_no_pass_day_when_picks_exist():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "PASS" not in msg or "WON" in msg  # PASS only valid when 0 picks sent

def test_pass_day_when_no_picks():
    msg = _format_nightly_report(
        {**RESULTS, "wins": 0, "losses": 0, "pushes": 0, "roi": 0.0, "pick_lines": []},
        LOG_ENTRIES, {}, MLB_TO_LOCAL
    )
    assert "PASS" in msg
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_nightly_report.py -v 2>&1 | head -40
```

Expected: All tests fail with `ImportError: cannot import name '_format_nightly_report'`

---

## Task 2: Implement `_format_nightly_report` in `discord_bot.py`

**Files:**
- Modify: `discord_bot.py` — add after `_format_results_message()` (line ~310)

- [ ] **Step 1: Add the formatter function**

Add this function after `_format_results_message()` in `discord_bot.py`:

```python
def _format_nightly_report(
    results: dict,
    log_entries: list,
    sent_picks_by_game: dict,
    mlb_to_local: dict,
) -> str:
    """
    Format the compact nightly report with three sections:
    1. Confidence Picks (sent picks with result)
    2. ML Board (all games)
    3. O/U Board (only games with O/U pick)

    Args:
        results: grading results dict from run_results()
        log_entries: list of analysis_log dicts (with ml_status, ou_status, scores)
        sent_picks_by_game: {local_game_id: [pick_dict, ...]}
        mlb_to_local: {mlb_game_id: local_game_id}
    """
    today = date.today().strftime("%B %d, %Y")
    wins = results.get("wins", 0)
    losses = results.get("losses", 0)
    roi = results.get("roi", 0.0)
    ml_correct = results.get("ml_correct", 0)
    ml_incorrect = results.get("ml_incorrect", 0)
    ou_correct = results.get("ou_correct", 0)
    ou_incorrect = results.get("ou_incorrect", 0)

    roi_str = f"+{roi}%" if roi > 0 else f"{roi}%"

    lines = [f"⚾ **MLB NIGHTLY REPORT — {today}**", ""]

    # ── Section 1: Confidence Picks ──────────────────────────
    lines.append("🎯 **CONFIDENCE PICKS**  ━━━━━━━━━━━━━━━━━━━━")

    sent_any = False
    for entry in sorted(log_entries, key=lambda e: e.get("ml_confidence", 0), reverse=True):
        mlb_game_id = entry.get("mlb_game_id")
        local_id = mlb_to_local.get(mlb_game_id)
        game_picks = sent_picks_by_game.get(local_id, [])
        ml_picks = [p for p in game_picks if p["pick_type"] in ("moneyline", "f5_ml")]
        if not ml_picks:
            continue

        pick = ml_picks[0]
        away = entry.get("away_team", "?")
        home = entry.get("home_team", "?")
        away_short = away.split()[-1][:3].upper()
        home_short = home.split()[-1][:3].upper()
        pick_team = pick.get("pick_team", "?")
        pick_short = pick_team.split()[-1]
        conf = pick.get("confidence", "?")
        win_prob = pick.get("win_probability", 0)
        odds = pick.get("ml_odds", "")
        odds_str = f" {odds:+d}" if isinstance(odds, int) else ""
        away_sc = entry.get("actual_away_score", 0) or 0
        home_sc = entry.get("actual_home_score", 0) or 0

        # Score string: winner name first
        if away_sc > home_sc:
            score_str = f"{away_short} {away_sc}-{home_sc}"
        else:
            score_str = f"{home_short} {home_sc}-{away_sc}"

        status = entry.get("ml_status", "pending")
        if status == "correct":
            result_emoji = "✅"
            result_word = "WON"
        elif status == "incorrect":
            result_emoji = "❌"
            result_word = "LOST"
        else:
            result_emoji = "➖"
            result_word = "PUSH"

        pick_type_label = "F5 ML" if pick.get("pick_type") == "f5_ml" else "ML"
        lines.append(
            f"{result_emoji} {away_short} @ {home_short}  →  "
            f"{pick_short} {pick_type_label}{odds_str}  {int(win_prob)}% · {conf}/10  "
            f"{score_str}  **{result_word}**"
        )
        sent_any = True

    if not sent_any:
        lines.append("📋 PASS — no picks met threshold today")

    total_picks = wins + losses
    if total_picks > 0:
        lines.append(f"📊 **{wins}W-{losses}L · ROI {roi_str}**")
    lines.append("")

    # ── Section 2: ML Board ──────────────────────────────────
    lines.append("📋 **ML BOARD**  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    sorted_entries = sorted(log_entries, key=lambda e: e.get("ml_confidence", 0), reverse=True)
    for entry in sorted_entries:
        conf = entry.get("ml_confidence", 0)
        away = entry.get("away_team", "?")
        home = entry.get("home_team", "?")
        away_short = away.split()[-1][:3].upper()
        home_short = home.split()[-1][:3].upper()
        pick_team = entry.get("ml_pick_team", "?")
        pick_short = pick_team.split()[-1]

        if conf >= 8:
            tier = "🔥"
        elif conf >= 7:
            tier = "🎯"
        elif conf >= 4:
            tier = "➡️"
        else:
            tier = "⚠️"

        away_sc = entry.get("actual_away_score", 0) or 0
        home_sc = entry.get("actual_home_score", 0) or 0
        if away_sc > home_sc:
            score_str = f"{away_short} {away_sc}-{home_sc}"
        else:
            score_str = f"{home_short} {home_sc}-{away_sc}"

        status = entry.get("ml_status", "pending")
        if status == "correct":
            result = "✅"
        elif status == "incorrect":
            result = "❌"
        else:
            result = "➖"

        # Flag if a confidence pick was sent for this game
        mlb_game_id = entry.get("mlb_game_id")
        local_id = mlb_to_local.get(mlb_game_id)
        game_picks = sent_picks_by_game.get(local_id, [])
        sent_flag = " 🎯" if any(p["pick_type"] in ("moneyline", "f5_ml") for p in game_picks) else ""

        lines.append(
            f"{tier} {away_short} @ {home_short}  →  {pick_short} {conf}/10  "
            f"{score_str}  {result}{sent_flag}"
        )

    ml_total = ml_correct + ml_incorrect
    ml_pct = round(ml_correct / ml_total * 100, 1) if ml_total > 0 else 0
    lines.append(f"📊 **{ml_correct}W-{ml_incorrect}L · {ml_pct}%**")
    lines.append("")

    # ── Section 3: O/U Board ─────────────────────────────────
    ou_lines = []
    for entry in sorted_entries:
        ou_pick = entry.get("ou_pick")
        ou_line_val = entry.get("ou_line")
        if not ou_pick or not ou_line_val:
            continue

        away = entry.get("away_team", "?")
        home = entry.get("home_team", "?")
        away_short = away.split()[-1][:3].upper()
        home_short = home.split()[-1][:3].upper()

        direction_emoji = "🔼" if ou_pick == "over" else "🔽"
        direction_label = "O" if ou_pick == "over" else "U"

        away_sc = entry.get("actual_away_score", 0) or 0
        home_sc = entry.get("actual_home_score", 0) or 0
        total_sc = away_sc + home_sc

        ou_status = entry.get("ou_status", "pending")
        if ou_status == "correct":
            marker = "✅"
            result_char = "✓"
        elif ou_status == "incorrect":
            marker = "❌"
            result_char = "✗"
        else:
            marker = "➖"
            result_char = "push"

        # Flag if a sent pick matches this O/U
        mlb_game_id = entry.get("mlb_game_id")
        local_id = mlb_to_local.get(mlb_game_id)
        game_picks = sent_picks_by_game.get(local_id, [])
        sent_flag = " 🎯" if any(p["pick_type"] in ("over", "under") for p in game_picks) else ""

        ou_lines.append(
            f"{marker} {away_short} @ {home_short}  →  "
            f"{direction_emoji} {direction_label}{ou_line_val}  Total: {total_sc}  {result_char}{sent_flag}"
        )

    if ou_lines:
        lines.append("🎰 **O/U BOARD**  ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.extend(ou_lines)
        ou_total = ou_correct + ou_incorrect
        ou_pct = round(ou_correct / ou_total * 100, 1) if ou_total > 0 else 0
        lines.append(f"📊 **{ou_correct}W-{ou_incorrect}L · {ou_pct}%**")

    return "\n".join(lines)
```

- [ ] **Step 2: Run the failing tests — they should now pass**

```bash
python3 -m pytest tests/test_nightly_report.py -v 2>&1
```

Expected: All 12 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add discord_bot.py tests/test_nightly_report.py
git commit -m "feat: add _format_nightly_report formatter to discord_bot"
```

---

## Task 3: Add `send_nightly_report` to `discord_bot.py`

**Files:**
- Modify: `discord_bot.py` — add after `_format_nightly_report()`

- [ ] **Step 1: Add the send function**

Add immediately after `_format_nightly_report()`:

```python
def send_nightly_report(
    results: dict,
    log_entries: list,
    sent_picks_by_game: dict,
    mlb_to_local: dict,
) -> bool:
    """Send the nightly results report to Discord. Returns True on success."""
    msg = _format_nightly_report(results, log_entries, sent_picks_by_game, mlb_to_local)

    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL — printing nightly report locally.")
        print(msg)
        return False

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
        if resp.status_code in (200, 204):
            print("  📤 Nightly report sent to Discord.")
            return True
        else:
            print(f"[DISCORD] Nightly report send failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[DISCORD] Error sending nightly report: {e}")
        return False
```

- [ ] **Step 2: Write a smoke test**

Add to `tests/test_nightly_report.py`:

```python
from unittest.mock import patch, MagicMock
from discord_bot import send_nightly_report

def test_send_nightly_report_no_webhook(monkeypatch):
    monkeypatch.setattr("discord_bot.DISCORD_WEBHOOK_URL", "")
    result = send_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert result is False

def test_send_nightly_report_success(monkeypatch):
    monkeypatch.setattr("discord_bot.DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/fake")
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    with patch("discord_bot.requests.post", return_value=mock_resp):
        result = send_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert result is True
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/test_nightly_report.py -v 2>&1
```

Expected: All 14 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add discord_bot.py tests/test_nightly_report.py
git commit -m "feat: add send_nightly_report to discord_bot"
```

---

## Task 4: Wire `send_nightly_report` into `run_results()` in `engine.py`

**Files:**
- Modify: `engine.py` — update import line ~26, replace send calls in `run_results()`

- [ ] **Step 1: Update the import in `engine.py`**

Find line 26:
```python
from discord_bot import send_pick, send_pick_edit, send_update, send_results, send_daily_board, send_ou_board, export_payload, _format_game_time
```

Replace with:
```python
from discord_bot import send_pick, send_pick_edit, send_update, send_results, send_nightly_report, send_daily_board, send_ou_board, export_payload, _format_game_time
```

- [ ] **Step 2: Build the `mlb_to_local` map in `run_results()`**

In `run_results()`, after the `sent_picks_by_game` dict is built (around line 815), add:

```python
    # Build mlb_game_id -> local game_id map for nightly report
    mlb_to_local = {}
    for p in picks:
        game_row = conn.execute(
            "SELECT id, mlb_game_id FROM games WHERE id=?", (p["game_id"],)
        ).fetchone()
        if game_row:
            mlb_to_local[game_row["mlb_game_id"]] = game_row["id"]
    # Also cover games in log_entries that had no sent pick
    for entry in log_entries:
        mid = entry.get("mlb_game_id")
        if mid and mid not in mlb_to_local:
            game_row = conn.execute(
                "SELECT id FROM games WHERE mlb_game_id=?", (mid,)
            ).fetchone()
            if game_row:
                mlb_to_local[mid] = game_row["id"]
```

- [ ] **Step 3: Replace the two existing Discord sends with `send_nightly_report`**

Find the block in `run_results()` that starts with:
```python
    # Send ML recap to Discord
    sent = send_results(results)
    if sent:
        print("  📤 Results recap sent to Discord.")

    # ── Send O/U results board (all games with O/U pick + outcome) ──
    ou_results_lines = []
    for entry in log_entries:
        ...
    if ou_results_lines:
        ...
        import requests as _req
        if DISCORD_WEBHOOK_URL:
            ...
            print("  📤 O/U results board sent to Discord.")
```

Replace the entire block (from `# Send ML recap to Discord` through the closing `if DISCORD_WEBHOOK_URL:` block, before `# ── Collect and store post-game boxscore data ──`) with:

```python
    # ── Send nightly report to Discord ──
    send_nightly_report(results, log_entries, sent_picks_by_game, mlb_to_local)
```

- [ ] **Step 4: Dry-run to confirm no errors**

```bash
python3 engine.py --test 2>&1 | tail -20
```

Expected: Runs without ImportError or NameError.

- [ ] **Step 5: Commit**

```bash
git add engine.py
git commit -m "feat: wire send_nightly_report into run_results, replace ML recap + O/U board sends"
```

---

## Task 5: Add `--report` CLI flag to `engine.py`

**Files:**
- Modify: `engine.py` — add `run_report()` function and `--report` flag in `main()`

- [ ] **Step 1: Add `run_report()` function**

Add this function before `main()` in `engine.py`:

```python
def run_report(target_date: Optional[str] = None) -> None:
    """
    Re-send the nightly report for a given date (default: today).
    Queries graded analysis_log and sent picks from the DB.
    """
    db.init_db()
    grade_date = target_date or date.today().isoformat()
    conn = db.get_connection()

    log_entries = db.get_analysis_log_for_date(grade_date)
    if not log_entries:
        print(f"[REPORT] No analysis log entries for {grade_date}.")
        return

    picks = db.get_picks_for_date(grade_date)

    # Build sent_picks_by_game
    sent_picks_by_game = {}
    for p in picks:
        gid = p["game_id"]
        if gid not in sent_picks_by_game:
            sent_picks_by_game[gid] = []
        sent_picks_by_game[gid].append(p)

    # Build mlb_to_local
    mlb_to_local = {}
    for p in picks:
        game_row = conn.execute(
            "SELECT id, mlb_game_id FROM games WHERE id=?", (p["game_id"],)
        ).fetchone()
        if game_row:
            mlb_to_local[game_row["mlb_game_id"]] = game_row["id"]
    for entry in log_entries:
        mid = entry.get("mlb_game_id")
        if mid and mid not in mlb_to_local:
            game_row = conn.execute(
                "SELECT id FROM games WHERE mlb_game_id=?", (mid,)
            ).fetchone()
            if game_row:
                mlb_to_local[mid] = game_row["id"]

    # Compute summary stats from graded entries
    wins = sum(1 for p in picks if p.get("status") == "won")
    losses = sum(1 for p in picks if p.get("status") == "lost")
    pushes = sum(1 for p in picks if p.get("status") == "push")
    roi = round((wins - losses) / max(wins + losses, 1) * 100, 1)
    ml_correct = sum(1 for e in log_entries if e.get("ml_status") == "correct")
    ml_incorrect = sum(1 for e in log_entries if e.get("ml_status") == "incorrect")
    ou_correct = sum(1 for e in log_entries if e.get("ou_status") == "correct")
    ou_incorrect = sum(1 for e in log_entries if e.get("ou_status") == "incorrect")

    results = {
        "wins": wins, "losses": losses, "pushes": pushes, "roi": roi,
        "ml_correct": ml_correct, "ml_incorrect": ml_incorrect,
        "ou_correct": ou_correct, "ou_incorrect": ou_incorrect,
        "pick_lines": [],
    }

    send_nightly_report(results, log_entries, sent_picks_by_game, mlb_to_local)
```

- [ ] **Step 2: Add `get_analysis_log_for_date` to `database.py`**

In `database.py`, add after `get_today_analysis_log()`:

```python
def get_analysis_log_for_date(target_date: str) -> list:
    """Return all analysis_log entries for a given date string (YYYY-MM-DD)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM analysis_log WHERE game_date=? ORDER BY ml_confidence DESC",
        (target_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 3: Add `--report` flag to `main()` in `engine.py`**

In the `main()` function's `if/elif` chain, add before the `else` clause:

```python
    elif "--report" in args:
        idx = args.index("--report")
        target = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        run_report(target)
```

Also add `get_analysis_log_for_date` to the database import (if database functions are used via `db.` prefix, no import change needed — just call `db.get_analysis_log_for_date(grade_date)`). Update the call in `run_report()` accordingly:

```python
    log_entries = db.get_analysis_log_for_date(grade_date)
```

- [ ] **Step 4: Update CLAUDE.md CLI Usage table**

In `CLAUDE.md`, find the CLI Usage section and add the new flag:

```markdown
python3 engine.py --report          # Re-send nightly report for today (already graded)
python3 engine.py --report DATE     # Re-send nightly report for DATE (YYYY-MM-DD)
```

- [ ] **Step 5: Test the flag**

```bash
python3 engine.py --report 2026-04-15 2>&1
```

Expected: Prints the nightly report to stdout (or sends to Discord if webhook is set). No errors.

- [ ] **Step 6: Commit**

```bash
git add engine.py database.py CLAUDE.md
git commit -m "feat: add --report CLI flag to re-send nightly report for any date"
```

---

## Task 6: End-to-end smoke test

- [ ] **Step 1: Run full test suite**

```bash
python3 -m pytest tests/ -v 2>&1 | tail -30
```

Expected: All tests pass. No regressions.

- [ ] **Step 2: Preview the report locally**

Temporarily unset the webhook to force stdout output:

```bash
DISCORD_WEBHOOK_URL="" python3 engine.py --report 2026-04-15 2>&1
```

Expected: Full three-section nightly report prints to terminal with correct scores, emojis, and record lines.

- [ ] **Step 3: Send live to Discord**

```bash
python3 engine.py --report 2026-04-15 2>&1
```

Expected: `📤 Nightly report sent to Discord.`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: nightly report feature complete — replace ML recap + O/U board with unified send"
```
