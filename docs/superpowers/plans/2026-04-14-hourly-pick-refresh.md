# Hourly Pick Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh ML board, O/U board, and individual pick cards every hour — boards PATCH in place, pick cards PATCH in place using stored Discord message IDs.

**Architecture:** Add `discord_message_id` to the `picks` table; update `send_pick()` to use `?wait=true` and return the message ID; add `send_pick_edit()` to PATCH existing cards; in `engine.py` replace the dedup-skip with a PATCH when a pick was already sent today.

**Tech Stack:** Python 3.9, SQLite (database.py), Discord webhooks (discord_bot.py), engine.py orchestrator

---

### Task 1: Change board refresh interval from 3h to 1h

**Files:**
- Modify: `engine.py` (two `interval_hours=3` calls)

- [ ] **Step 1: Edit engine.py board calls**

Find these two lines in `engine.py` (around line 243 and 251):
```python
    if db.board_needs_update(today, interval_hours=3):
```
```python
    if db.ou_board_needs_update(today, interval_hours=3):
```

Change both to `interval_hours=1`:
```python
    if db.board_needs_update(today, interval_hours=1):
```
```python
    if db.ou_board_needs_update(today, interval_hours=1):
```

- [ ] **Step 2: Verify change**

```bash
grep "interval_hours" engine.py
```
Expected output:
```
    if db.board_needs_update(today, interval_hours=1):
    if db.ou_board_needs_update(today, interval_hours=1):
```

- [ ] **Step 3: Commit**

```bash
git add engine.py
git commit -m "feat: refresh ML and O/U boards every hour instead of every 3h"
```

---

### Task 2: Add discord_message_id column to picks table

**Files:**
- Modify: `database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_f5_picks.py` (after the existing `fresh_db` fixture):

```python
def test_picks_table_has_discord_message_id_column(fresh_db):
    """picks table must have discord_message_id column."""
    import sqlite3
    conn = sqlite3.connect(fresh_db)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(picks)").fetchall()]
    conn.close()
    assert "discord_message_id" in cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 -m pytest tests/test_f5_picks.py::test_picks_table_has_discord_message_id_column -v
```
Expected: FAIL — `AssertionError: assert 'discord_message_id' in [...]`

- [ ] **Step 3: Add column to CREATE TABLE in database.py**

In `database.py` find the `picks` CREATE TABLE statement (line ~150). After the `discord_sent INTEGER DEFAULT 0,` line, add:

```python
        discord_message_id TEXT,
```

So it reads:
```python
        discord_sent INTEGER DEFAULT 0,
        discord_message_id TEXT,
        created_at TEXT,
```

- [ ] **Step 4: Add migration probe for existing DBs**

In `database.py`, find `init_db()`. After `conn.commit()` and before `conn.close()`, add the migration:

```python
    # Migration: add discord_message_id to picks if missing
    try:
        conn.execute("ALTER TABLE picks ADD COLUMN discord_message_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python3 -m pytest tests/test_f5_picks.py::test_picks_table_has_discord_message_id_column -v
```
Expected: PASS

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -q
```
Expected: all existing tests still pass (138+1)

- [ ] **Step 7: Commit**

```bash
git add database.py tests/test_f5_picks.py
git commit -m "feat: add discord_message_id column to picks table with migration"
```

---

### Task 3: Update save_pick and mark_pick_sent to store message ID

**Files:**
- Modify: `database.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_f5_picks.py`:

```python
def test_mark_pick_sent_stores_message_id(fresh_db):
    """mark_pick_sent should store the discord_message_id."""
    import sqlite3
    import database as _db
    _db.init_db()

    # Insert a game row first
    conn = sqlite3.connect(fresh_db)
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO games (mlb_game_id, game_date, status) VALUES (?,?,?)",
        (999, "2026-04-14", "scheduled")
    )
    conn.commit()
    game_id = conn.execute("SELECT id FROM games WHERE mlb_game_id=999").fetchone()[0]

    conn.execute("""
        INSERT INTO picks
        (game_id, pick_type, pick_team, confidence, win_probability, edge_score,
         projected_away_score, projected_home_score,
         edge_pitching, edge_offense, edge_advanced, edge_bullpen, edge_weather, edge_market,
         notes, ev_score, ml_odds, ou_odds, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (game_id, "moneyline", "Yankees", 8, 62.0, 0.15,
          3.2, 4.1, "", "", "", "", "", "", "", 0.05, -130, None, now, now))
    conn.commit()
    pick_id = conn.execute("SELECT id FROM picks ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()

    _db.mark_pick_sent(pick_id, message_id="1234567890")

    conn = sqlite3.connect(fresh_db)
    row = conn.execute("SELECT discord_sent, discord_message_id FROM picks WHERE id=?", (pick_id,)).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == "1234567890"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_f5_picks.py::test_mark_pick_sent_stores_message_id -v
```
Expected: FAIL — `TypeError: mark_pick_sent() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Update mark_pick_sent in database.py**

Find `mark_pick_sent` (line ~477):

```python
def mark_pick_sent(pick_id: int):
    """Mark a pick as sent to Discord."""
    conn = get_connection()
    conn.execute(
        "UPDATE picks SET discord_sent=1 WHERE id=?", (pick_id,)
    )
    conn.commit()
    conn.close()
```

Replace with:

```python
def mark_pick_sent(pick_id: int, message_id: Optional[str] = None):
    """Mark a pick as sent to Discord, optionally storing the message ID."""
    conn = get_connection()
    conn.execute(
        "UPDATE picks SET discord_sent=1, discord_message_id=? WHERE id=?",
        (message_id, pick_id)
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_f5_picks.py::test_mark_pick_sent_stores_message_id -v
```
Expected: PASS

- [ ] **Step 5: Add get_sent_pick_today function**

After `mark_pick_sent` in `database.py`, add:

```python
def get_sent_pick_today(game_id: int, pick_type: str) -> Optional[dict]:
    """Return the discord-sent pick for this game+type today, or None."""
    conn = get_connection()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT id, discord_message_id, confidence FROM picks "
        "WHERE game_id=? AND pick_type=? AND discord_sent=1 AND created_at LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        (game_id, pick_type, f"{today}%")
    ).fetchone()
    conn.close()
    return dict(row) if row else None
```

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -q
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add database.py tests/test_f5_picks.py
git commit -m "feat: mark_pick_sent stores discord_message_id, add get_sent_pick_today"
```

---

### Task 4: Update send_pick to return message ID

**Files:**
- Modify: `discord_bot.py`

- [ ] **Step 1: Update send_pick to use ?wait=true and return message_id**

Find `send_pick` in `discord_bot.py` (line ~15). Replace entirely:

```python
def send_pick(pick: dict) -> Optional[str]:
    """
    Send a high-confidence pick alert to Discord.
    Returns the Discord message ID on success, None on failure.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL configured — printing pick locally.")
        print(_format_pick_message(pick))
        return None

    payload = {"content": _format_pick_message(pick)}

    try:
        resp = requests.post(
            f"{DISCORD_WEBHOOK_URL}?wait=true",
            json=payload,
            timeout=10
        )
        if resp.status_code == 200:
            message_id = resp.json().get("id")
            print(f"[DISCORD] Pick sent: {pick.get('game', '?')} (message {message_id})")
            return message_id
        else:
            print(f"[DISCORD] Failed ({resp.status_code}): {resp.text}")
            return None
    except Exception as e:
        print(f"[DISCORD] Error sending pick: {e}")
        return None
```

- [ ] **Step 2: Add send_pick_edit function**

After `send_pick`, add:

```python
def send_pick_edit(message_id: str, pick: dict) -> bool:
    """
    Edit an existing pick card in Discord in-place via PATCH.
    Returns True if the edit succeeded.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL — printing pick edit locally.")
        print(_format_pick_message(pick))
        return False

    patch_url = f"{DISCORD_WEBHOOK_URL}/messages/{message_id}"
    payload = {"content": _format_pick_message(pick)}

    try:
        resp = requests.patch(patch_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[DISCORD] Pick updated: {pick.get('game', '?')} (message {message_id})")
            return True
        else:
            print(f"[DISCORD] Pick edit failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[DISCORD] Error editing pick: {e}")
        return False
```

- [ ] **Step 3: Update the import in engine.py**

Find the import line in `engine.py` (line ~26):
```python
from discord_bot import send_pick, send_update, send_results, send_daily_board, send_ou_board, export_payload, _format_game_time
```

Add `send_pick_edit`:
```python
from discord_bot import send_pick, send_pick_edit, send_update, send_results, send_daily_board, send_ou_board, export_payload, _format_game_time
```

- [ ] **Step 4: Verify no syntax errors**

```bash
python3 -c "import discord_bot; import engine; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add discord_bot.py engine.py
git commit -m "feat: send_pick returns message_id, add send_pick_edit for in-place PATCH"
```

---

### Task 5: Wire up hourly pick card PATCH in engine.py

**Files:**
- Modify: `engine.py`

- [ ] **Step 1: Replace dedup-skip with PATCH logic**

Find the dedup block in `engine.py` (around line 198):

```python
        # Skip if a discord-sent pick already exists today for this game + pick type
        if db.pick_already_sent_today(game_id, pick["pick_type"]):
            print(f"[DB] Pick already sent today for game_id={game_id} type={pick['pick_type']} — skipping.")
            continue
```

Replace with:

```python
        # If already sent today, PATCH the existing message with fresh data
        existing = db.get_sent_pick_today(game_id, pick["pick_type"])
        if existing:
            msg_id = existing.get("discord_message_id")
            if msg_id:
                send_pick_edit(msg_id, pick)
            else:
                print(f"[DB] Pick already sent for game_id={game_id} type={pick['pick_type']} but no message_id stored — skipping.")
            continue
```

- [ ] **Step 2: Update mark_pick_sent call to pass message_id**

Find the line (around line 230):
```python
        sent = send_pick(pick)
        if sent:
            db.mark_pick_sent(pick_id)
```

Replace with:
```python
        message_id = send_pick(pick)
        if message_id:
            db.mark_pick_sent(pick_id, message_id=message_id)
```

- [ ] **Step 3: Verify no syntax errors**

```bash
python3 -c "import engine; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Dry-run test**

```bash
python3 engine.py --test 2>&1 | tail -20
```
Expected: runs without errors, prints `[DRY RUN]` lines

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -q
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add engine.py
git commit -m "feat: hourly pick card PATCH — edit existing Discord message instead of skipping"
```

---

### Task 6: Add "Last updated" footer to pick card messages

**Files:**
- Modify: `discord_bot.py`

- [ ] **Step 1: Add timestamp footer to _format_pick_message**

Find `_format_pick_message` in `discord_bot.py` (line ~96). At the very end, before `return msg`, add:

```python
    from datetime import datetime
    import zoneinfo
    now_pt = datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
    msg += f"\n🔄 *Updated {now_pt.strftime('%-I:%M %p')} PT*"
```

- [ ] **Step 2: Verify formatting**

```bash
python3 -c "
from discord_bot import _format_pick_message
pick = {
    'game': 'Yankees @ Red Sox',
    'pick_type': 'moneyline',
    'pick_team': 'Yankees',
    'confidence': 8,
    'win_probability': 62.0,
    'projected_away_score': 4.2,
    'projected_home_score': 3.1,
    'away_team': 'Yankees',
    'home_team': 'Red Sox',
}
print(_format_pick_message(pick))
"
```
Expected: message ends with `🔄 *Updated H:MM AM/PM PT*`

- [ ] **Step 3: Run full test suite**

```bash
python3 -m pytest tests/ -q
```
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add discord_bot.py
git commit -m "feat: add last-updated timestamp footer to pick card messages"
```
