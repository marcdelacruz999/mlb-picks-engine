# Weekly Signal Calibration Report — Design Spec

**Date:** 2026-04-14
**Status:** Approved
**Scope:** New standalone `calibrate.py` script + launchd schedule

---

## Problem

Agent weights were last tuned by the backtester (Apr 12). We identified two ML losses on Apr 13-14 sharing a common pattern — SP rust risk + weak bullpen ERA — by manual analysis. That manual process should be automated and run weekly so signal drift gets caught before it compounds over a week of picks.

The optimizer's weight rebalance is data-triggered and auto-applies small nudges. This is different: it's a human-reviewed weekly report that requires approval before touching config.

---

## Design

### `calibrate.py` — Weekly Signal Calibration

**Entry points:**
- `python3 calibrate.py` — analyze + post Discord report, no config changes
- `python3 calibrate.py --apply` — apply suggested weight changes to `config.py` and commit
- `python3 calibrate.py --days N` — override lookback window (default 7)
- `python3 calibrate.py --test` — dry run, print report to stdout, no Discord send

**Schedule:** Every Monday at 9:00 AM via launchd (`com.marc.mlb-picks-engine.calibrate.plist`)

---

### Data Source

Reads from `mlb_picks.db` — the `picks` table. Only considers picks where:
- `discord_sent = 1` (actually sent, not internal analysis artifacts)
- `status IN ('won', 'lost', 'push')` (fully graded)
- `created_at >= now - N days`

Fields used: `pick_type`, `pick_team`, `confidence`, `status`, `edge_score`,
`edge_pitching`, `edge_offense`, `edge_bullpen`, `edge_advanced`, `edge_market`,
`edge_weather`, `notes`, `ml_odds`, `ou_odds`, `ev_score`

---

### Signal Parsing

Each pick's stored edge text is parsed into boolean signal flags via string matching.
No re-computation — use what the agents already wrote.

| Signal Flag | Parse Rule |
|-------------|-----------|
| `sp_home_advantage` | `edge_pitching` contains "Home SP" + "clear pitching advantage" |
| `sp_away_advantage` | `edge_pitching` contains "Away SP" + "clear pitching advantage" |
| `sp_home_layoff` | `edge_pitching` contains "Home SP extended layoff" |
| `sp_away_layoff` | `edge_pitching` contains "Away SP extended layoff" |
| `offense_home_edge` | `edge_offense` contains "Home lineup has offensive advantage" |
| `offense_away_edge` | `edge_offense` contains "Away lineup has offensive advantage" |
| `bullpen_home_stronger` | `edge_bullpen` contains "Home bullpen is stronger" |
| `bullpen_away_stronger` | `edge_bullpen` contains "Away bullpen is stronger" |
| `bullpen_home_era_bad` | parse ERA from "Home top pen (7d): ... X.XX ERA", X > 5.0 |
| `bullpen_away_era_bad` | parse ERA from "Away top pen (7d): ... X.XX ERA", X > 5.0 |
| `advanced_barrel` | `edge_advanced` contains "barrel rate advantage" |
| `advanced_hardhit` | `edge_advanced` contains "hard-hit rate edge" |
| `advanced_xwoba` | `edge_advanced` contains "xwOBA" |
| `market_edge_low` | parse market % from `edge_market`, bucket: < 2% |
| `market_edge_mid` | parse market %, bucket: 2–4% |
| `market_edge_high` | parse market %, bucket: > 4% |
| `lineup_confirmed` | `notes` contains "confirmed" |
| `rain_flag` | `edge_weather` contains "Rain" |
| `wind_strong` | parse wind mph from `edge_weather`, > 12 mph |

**Rust+pen combo flag** (the pattern we identified):
- `rust_weak_pen_home`: `sp_home_layoff AND bullpen_home_era_bad AND pick_side == home`
- `rust_weak_pen_away`: `sp_away_layoff AND bullpen_away_era_bad AND pick_side == away`

---

### Signal → Outcome Table

For each signal flag, compute across all graded picks in the window:
- N picks where signal was present
- W-L record
- Win rate %
- Delta vs baseline win rate (overall win rate for that pick type)

Only surface signals with N ≥ 3 picks (below that is noise).

---

### Weight Suggestion Logic

Compare each signal's observed win rate to the current agent weight's implied contribution:

1. Compute baseline win rate (all ML picks in window)
2. For each agent's primary signals, compute signal win rate
3. If signal win rate diverges from baseline by > 8 percentage points AND N ≥ 5:
   - Signal winning more than expected → suggest +0.02 to that agent's weight
   - Signal winning less than expected → suggest -0.02 to that agent's weight
4. Cap any single suggested move at ±0.03 per week
5. Normalize suggested weights to sum to 1.00
6. Only suggest changes if total adjustment magnitude > 0.03 (avoid noise commits)

Weight suggestions go into the Discord report. Nothing is applied until `--apply` is run.

---

### `--apply` Flow

1. Requires ≥ 10 graded picks in window (below that: print warning, exit)
2. Reads suggested weights from the analysis (re-runs analysis, doesn't cache)
3. Updates `WEIGHTS` dict in `config.py` via string replacement (same approach optimizer uses)
4. Commits with message: `calibration: weekly weight update YYYY-MM-DD — {summary}`
5. Logs to `calibration_log.jsonl`: timestamp, window, pick count, old weights, new weights, signal table

---

### Calibration Log

Append-only JSONL at project root: `calibration_log.jsonl`

Each entry:
```json
{
  "date": "2026-04-21",
  "window_days": 7,
  "pick_count": 12,
  "win_rate": 0.75,
  "signal_table": {...},
  "weights_before": {...},
  "weights_after": {...},
  "applied": true
}
```

---

### Discord Output

Single embed posted to the existing `DISCORD_WEBHOOK_URL`. Posted every Monday regardless of whether `--apply` was run.

**Embed structure:**
```
📊 Weekly Calibration Report — Week of Apr 14
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Record: 9W-3L (75.0%) | 12 picks | ML: 9W-3L | O/U: 0 graded

SIGNAL BREAKDOWN
✅ Home offense edge    8 picks  7W-1L  87.5%  (+12.5% vs baseline)
✅ Lineup confirmed     9 picks  7W-2L  77.8%  (+2.8%)
⚠️ Rust + weak pen     2 picks  0W-2L   0.0%  (-75.0%) ← flagged
➡️ Market edge >4%     3 picks  2W-1L  66.7%  (-8.3%)

SUGGESTED WEIGHTS (current → suggested)
offense:  0.23 → 0.25  (+0.02)
bullpen:  0.20 → 0.20  (hold)
pitching: 0.22 → 0.20  (-0.02)
[others unchanged]

Run: python3 calibrate.py --apply to apply
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

If no changes are suggested: "Weights look calibrated — no changes recommended."
If < 10 picks: "Not enough graded picks this week (N) — report only, no suggestions."

---

### launchd Schedule

`~/Library/LaunchAgents/com.marc.mlb-picks-engine.calibrate.plist`

- Runs every Monday at 9:00 AM
- Uses same `run_engine.sh`-style wrapper for logging to `engine.log`
- `WeeklyInterval` key with `Weekday = 2` (Monday), `Hour = 9`, `Minute = 0`

---

## File Changes

| File | Change |
|------|--------|
| `calibrate.py` | New script |
| `calibration_log.jsonl` | New append-only log (gitignored) |
| `~/Library/LaunchAgents/com.marc.mlb-picks-engine.calibrate.plist` | New plist |
| `.gitignore` | Add `calibration_log.jsonl` |
| `CLAUDE.md` | Add calibrate.py to schedule table and file map |

No changes to `engine.py`, `optimizer.py`, `analysis.py`, or `database.py`.

---

## Out of Scope

- Auto-applying weights without `--apply` flag
- O/U signal analysis (not enough graded O/U picks yet — add when ≥ 20 graded)
- Slack/Telegram output (Discord only, same as all other engine output)
- Historical re-analysis (only looks back N days, not full history)
