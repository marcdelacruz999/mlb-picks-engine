# MLB Picks Engine — Discord Reference

Webhook URL in `config.py DISCORD_WEBHOOK_URL`. All sends in `discord_bot.py`.

## Message Types

| Trigger | Format | When |
|---------|--------|------|
| Approved pick | `🚨 MLB HIGH-CONFIDENCE PICK` | 8am run + --game |
| SP scratched | `⚠️ PITCHER SCRATCH ALERT` | monitor.py, every 30 min |
| Line movement | `⚠️ MLB PICK UPDATE` — Watch | --refresh, ≥5pp ML drop or ≥0.5 total move |
| Pick cancelled | `⚠️ MLB PICK UPDATE` — Cancel | --refresh, drops below threshold |
| Reduce confidence | `⚠️ MLB PICK UPDATE` — Reduce | --refresh, conf drops 2+ |
| ML picks board | `⚾ MLB ML PICKS — [date]` | Hourly run, edits in-place every 3h via PATCH |
| O/U picks board | `🎯 MLB O/U PICKS — [date]` | Hourly run, edits in-place every 3h via PATCH |
| ML results recap | `📊 MLB DAILY RESULTS` | 11pm --results |
| O/U results recap | `🎯 MLB O/U RESULTS — [date]` | 11pm --results, separate message |
| Optimizer report | `⚙️ MLB ENGINE — DAILY OPTIMIZER REPORT` | 11:30pm nightly |

## Pick Alert Format

```
🚨 MLB HIGH-CONFIDENCE PICK
[Away] @ [Home] — [Date/Time PT]

**Pick:** [Team] [ML/Over/Under/F5 ML]
**Confidence:** X/10 | **Win Probability:** XX%
**Projected Score:** X.X – X.X
**EV:** +X.XX
**Stake:** X.Xx units

**Current Odds:**
  ML: Away -XXX / Home +XXX
  RL: Away -1.5 (+XXX) / Home +1.5 (-XXX)
  Total: X.X (O -XXX / U +XXX)

**Edge Summary:**
  Pitching: [edge description]
  Offense: [edge description]
  Advanced (Statcast): [edge description]
  Bullpen: [edge description]
  Weather: [edge description]
  Market: [edge description]

**Notes:** [lineup status, total line for O/U picks]
```

## Key Formatting Notes

- **Date/Time**: game start in PT (`America/Los_Angeles`) via `discord_bot._format_game_time()`
  - Source: `g["gameDate"]` (UTC ISO) → `game_time_utc` field
- **F5 picks**: shown as "{Team} F5 ML (First 5 Innings)"
- **SP scratch alert**: sent per side (away/home separately), deduped by `scratch_alerts` table
- **Stake line**: appears after EV line — `**Stake:** X.Xx units` (half-Kelly, 0.25–2.0x)
- **edge_advanced** is always included in Discord output and stored in DB

## Nightly Report (`_format_nightly_report`)

Three sections: **Confidence Picks** → **ML Board** → **O/U Board**.

### Confidence Picks grading source
Use `pick.get("status")` from the `picks` table — **not** `entry.get("ml_status")` from `analysis_log`. The sent pick can differ from the raw model call (a later run may flip the side), making `analysis_log.ml_status` wrong for the confidence section.

### ML/O/U Board — ground truth for sent picks
Call `db.get_today_sent_picks_with_game()` and pass as `approved=` to both `send_daily_board()` and `send_ou_board()`. This joins `picks` + `games` to include `mlb_game_id` and covers picks sent in any earlier run, not just the current run's `approved` list. Sent games show with a `🎯` flag.

### `analysis_log.ml_pick_team` ≠ sent pick
`analysis_log` stores the raw model call; `picks` table is ground truth. They diverge when a later run flips the side after lineups confirm. Never use `analysis_log` to determine confidence pick outcomes.

### Stuck game with 0-0 score after `--results`
If a game has NULL scores and `pending` status, fetch manually:
```python
requests.get('https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&hydrate=linescore')
```
Then patch `games` (away_score, home_score, total_runs) and `analysis_log` (actual_away_score, actual_home_score, actual_total, ml_status, ou_status) directly.

### Format rules
- Section headers: plain text, no `**bold**` — e.g. `🎯 CONFIDENCE PICKS  ━━━━━━━━━━━━━━━━━━━━`
- Summary lines: bold stats, emojis outside — e.g. `🔥 **4W-1L · ON FIRE · ROI +60.0%** 💰`
- Score string: winner name first — e.g. `CHC 12-4` not `4-12`
- Result word: plain — `WON` / `LOST` / `PUSH` (no bold)

## Operator Console Output (each run)

1. Game Analysis Board — all 15 games with composite scores
2. Approved Picks Board — ranked by confidence
3. Watchlist — up to 5 games near threshold (conf 5–6) to monitor
4. Discord Payload — exact JSON sent to webhook
5. Tracking Snapshot — 30-day record, win rate, ROI
