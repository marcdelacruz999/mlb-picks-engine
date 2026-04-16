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

## Operator Console Output (each run)

1. Game Analysis Board — all 15 games with composite scores
2. Approved Picks Board — ranked by confidence
3. Watchlist — up to 5 games near threshold (conf 5–6) to monitor
4. Discord Payload — exact JSON sent to webhook
5. Tracking Snapshot — 30-day record, win rate, ROI
