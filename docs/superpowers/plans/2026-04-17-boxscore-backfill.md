# Boxscore New-Field Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill pitch_count, batters_faced, ground_outs, fly_outs, inherited_runners, inherited_runners_scored (pitcher) and pitching_strikeouts, pitching_walks, pitching_hits_allowed, pitching_earned_runs, pitching_home_runs_allowed (team) for all 17 dates already in the DB (2026-04-01 through 2026-04-17).

**Architecture:** A standalone script `backfill_boxscores.py` re-fetches boxscores via the existing `collect_boxscores()` function and issues `UPDATE` statements (not INSERT) against existing rows matched by `(mlb_game_id, pitcher_id)` and `(mlb_game_id, team_id)`. No schema changes. No new tables.

**Tech Stack:** Python 3.9, SQLite via `database.get_connection()`, `data_mlb.collect_boxscores()`

---

### Task 1: Write and run the backfill script

**Files:**
- Create: `backfill_boxscores.py` (root of project, deleted after successful run)

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""
One-time backfill: populate new stat columns for pitcher_game_logs and team_game_logs
rows collected before pitch_count/GB-FB/inherited_runners/team_pitching columns existed.

Run from project root:
    python3 backfill_boxscores.py

Safe to re-run — UPDATE only touches rows where pitch_count IS 0 or NULL.
"""
import sys
from data_mlb import collect_boxscores
import database as db


def backfill_date(conn, game_date: str) -> tuple[int, int]:
    """Re-fetch boxscore for game_date and UPDATE existing rows. Returns (pitcher_updated, team_updated)."""
    data = collect_boxscores(game_date)
    pitcher_logs = data.get("pitcher_logs", [])
    team_logs = data.get("team_logs", [])

    p_updated = 0
    for p in pitcher_logs:
        cur = conn.execute(
            "UPDATE pitcher_game_logs SET "
            "pitch_count=?, batters_faced=?, ground_outs=?, fly_outs=?, "
            "inherited_runners=?, inherited_runners_scored=? "
            "WHERE mlb_game_id=? AND pitcher_id=?",
            (
                p.get("pitch_count", 0) or 0,
                p.get("batters_faced", 0) or 0,
                p.get("ground_outs", 0) or 0,
                p.get("fly_outs", 0) or 0,
                p.get("inherited_runners", 0) or 0,
                p.get("inherited_runners_scored", 0) or 0,
                p["mlb_game_id"],
                p["pitcher_id"],
            ),
        )
        p_updated += cur.rowcount

    t_updated = 0
    for t in team_logs:
        cur = conn.execute(
            "UPDATE team_game_logs SET "
            "pitching_strikeouts=?, pitching_walks=?, pitching_hits_allowed=?, "
            "pitching_earned_runs=?, pitching_home_runs_allowed=? "
            "WHERE mlb_game_id=? AND team_id=?",
            (
                t.get("pitching_strikeouts", 0) or 0,
                t.get("pitching_walks", 0) or 0,
                t.get("pitching_hits_allowed", 0) or 0,
                t.get("pitching_earned_runs", 0) or 0,
                t.get("pitching_home_runs_allowed", 0) or 0,
                t["mlb_game_id"],
                t["team_id"],
            ),
        )
        t_updated += cur.rowcount

    conn.commit()
    return p_updated, t_updated


def main():
    conn = db.get_connection()

    # Get all distinct dates that have pitcher rows with pitch_count still 0
    dates = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT game_date FROM pitcher_game_logs "
            "WHERE pitch_count = 0 OR pitch_count IS NULL "
            "ORDER BY game_date"
        ).fetchall()
    ]

    if not dates:
        print("Nothing to backfill — all rows already have pitch_count populated.")
        conn.close()
        return

    print(f"Backfilling {len(dates)} dates: {dates[0]} → {dates[-1]}")
    total_p = total_t = 0

    for game_date in dates:
        p, t = backfill_date(conn, game_date)
        total_p += p
        total_t += t
        print(f"  {game_date}: {p} pitcher rows, {t} team rows updated")

    conn.close()
    print(f"\nDone. Total: {total_p} pitcher rows, {total_t} team rows updated.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
python3 backfill_boxscores.py
```

Expected output: 17 date lines each showing ~80-150 pitcher rows and ~26 team rows updated. Final total ~1,896 pitcher rows, ~444 team rows.

- [ ] **Step 3: Verify the backfill**

```bash
python3 -c "
import database as db
conn = db.get_connection()

p = conn.execute('SELECT COUNT(*) FROM pitcher_game_logs WHERE pitch_count > 0').fetchone()[0]
p_total = conn.execute('SELECT COUNT(*) FROM pitcher_game_logs').fetchone()[0]
print(f'pitch_count populated: {p}/{p_total} pitcher rows')

gb = conn.execute('SELECT COUNT(*) FROM pitcher_game_logs WHERE ground_outs > 0 OR fly_outs > 0').fetchone()[0]
print(f'gb/fb populated: {gb}/{p_total} pitcher rows')

inh = conn.execute('SELECT COUNT(*) FROM pitcher_game_logs WHERE inherited_runners > 0').fetchone()[0]
print(f'inherited_runners > 0: {inh} rows (relievers only — normal if < total)')

t = conn.execute('SELECT COUNT(*) FROM team_game_logs WHERE pitching_strikeouts > 0').fetchone()[0]
t_total = conn.execute('SELECT COUNT(*) FROM team_game_logs').fetchone()[0]
print(f'pitching_strikeouts populated: {t}/{t_total} team rows')

conn.close()
"
```

Expected: pitch_count populated in majority of pitcher rows (starters always have it; relievers may show 0 legitimately if they threw 0 pitches per API). Team pitching strikeouts populated in all 444 rows.

- [ ] **Step 4: Run full test suite to confirm no regressions**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass (234+).

- [ ] **Step 5: Delete the one-time script**

```bash
rm backfill_boxscores.py
```

- [ ] **Step 6: Commit**

```bash
git add -u
git commit -m "chore: backfill pitch_count, GB/FB, inherited runners, team pitching for 2026-04-01 to 2026-04-17"
```
