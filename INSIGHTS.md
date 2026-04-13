# MLB Picks Engine — Daily Insights & Calibration Log

Track outcomes, model calibration signals, agent accuracy, and recurring patterns.
This file captures things that cannot be derived from the database or code — observations,
biases found, and learnings that should inform future weight tuning.

---

## How to Use This File

- **After each day's results**: add an entry under Daily Outcomes
- **After 2-3 weeks of data**: update the Calibration & Bias section
- **When you notice a pattern**: add it to Recurring Observations immediately
- **When an API or data issue occurs**: log it under Data Quality

---

## Daily Outcomes

Format per entry:
```
### YYYY-MM-DD
Picks: X sent | Results: X-X-X (W-L-P) | ROI: +/-X%

| Game | Pick | Conf | Odds | Result | Key Factor |
|------|------|------|------|--------|------------|
| Away @ Home | TEAM ML / OVER X.X | 8 | -130 | W | SP dominated, Statcast edge held |

Notes:
- What the model got right / wrong
- Any surprises (pitcher scratch, weather shift, late lineup change)
- Agent that contributed most / least on this day
```

---

<!-- Add daily entries below this line, newest first -->

### 2026-04-11
Picks: 5 sent | Results: 1-4-0 (W-L-P) | Net: -3.074 units

| Game | Pick | Conf | Odds | Result | Final | Key Factor |
|------|------|------|------|--------|-------|------------|
| WSH @ MIL | OVER 8.5 | 9 | -121 | L | 3-1 (total 4) | Model projected 11.7 runs — extreme miss; both SPs dominant |
| CWS @ KC | UNDER 9.0 | 8 | -108 | W | 0-2 (total 2) | Clean under, pitching dominated |
| LAA @ CIN | UNDER 9.0 | 7 | -85 | L | 3-7 (total 10) | Went over; CIN offense outperformed model |
| OAK @ NYM | NYM ML | 7 | -150 | L | A's 11-6 | A's blew out NYM despite Senga advantage |
| SF @ BAL | SF ML | 7 | -100 | L | SF 2-6 | BAL offense outperformed xStats signal |

Notes:
- WSH/MIL OVER at conf 9 was worst miss — model xERA regression signals for both SPs fired but actual results were opposite; both pitchers had great outings despite xERA warning
- A's at NYM: lineup confirmation showed even OPS but A's scored 11 — small sample early season Statcast may be unreliable
- First day of live data — 1-4 is a small sample, no weight changes warranted yet

---

## Calibration Tracker

Update this section every 2 weeks once you have enough sample.

### Win Rate by Confidence Level

| Confidence | Picks | Wins | Losses | Pushes | Win Rate | Expected |
|------------|-------|------|--------|--------|----------|----------|
| 10 | 0 | 0 | 0 | 0 | — | >65% |
| 9 | 0 | 0 | 0 | 0 | — | ~60% |
| 8 | 0 | 0 | 0 | 0 | — | ~55% |
| 7 | 0 | 0 | 0 | 0 | — | ~52% |

### Win Rate by Pick Type

| Pick Type | Picks | Wins | Win Rate |
|-----------|-------|------|----------|
| Moneyline | 0 | 0 | — |
| Over | 0 | 0 | — |
| Under | 0 | 0 | — |

### Win Rate by Top Agent Signal

Track which agent's edge most often appears in winning picks.

| Agent | Times Top Signal | Winning Picks | Win Rate |
|-------|-----------------|---------------|----------|
| Pitching | 0 | 0 | — |
| Advanced (Statcast) | 0 | 0 | — |
| Market | 0 | 0 | — |
| Weather/Park | 0 | 0 | — |
| Momentum | 0 | 0 | — |
| Bullpen | 0 | 0 | — |
| Offense | 0 | 0 | — |

---

## Recurring Observations & Biases

Document patterns here as soon as you notice them. Include sample size before drawing conclusions.

### Confirmed Biases (3+ data points)
*None yet — add entries as patterns emerge.*

### Suspected Biases (1-2 data points, watch list)
*None yet.*

### Format:
```
**[Bias Name]** — found YYYY-MM-DD, N picks
Description: What the model consistently gets wrong
Example: COL home unders — model underestimates offense even with park factor applied
Action: [watch / adjust weight / add hardcoded correction]
```

---

## Agent Performance Notes

### Pitching Agent
- Rest days logic added 2026-04-10 — monitor if short-rest picks underperform
- Backtest (2024+2025): lift +0.080 — strongest signal of 4 testable agents, but overweighted at 30%

### Bullpen Agent
- Backtest (2024+2025): lift +0.050 — rivaled offense despite only 10% weight; raised to 17% on 2026-04-10
- Fatigue signal added 2026-04-10: heavy use (>12 IP/3d) = −0.15 penalty; moderate (>8 IP/3d) = −0.08; fires automatically each run

### Advanced (Statcast) Agent
- xwOBA luck diff most reliable signal; barrel rate adjustments smaller magnitude
- Early season caveat: Statcast data thin in April (small sample), may be noisy before May

### Weather/Environment Agent
- Park factors: static table, may need updating if a park changes dimensions or roof policy
- Umpire table: 14 umps covered — check if new umps are being assigned and missing from table
- Wind direction scoring assumes standard park orientation (most parks open toward CF in E/SE direction)

### Market Agent
- Edge of +5% vs market implied prob = real alpha signal historically
- Watch for cases where market is right and model is wrong (reverse line movement)

---

## Data Quality Log

Log API issues, missing data, or anomalies here.

| Date | Issue | Impact | Resolution |
|------|-------|--------|------------|
| 2026-04-12 | `schedule?hydrate=boxscore` doesn't return player-level pitcher stats for historical dates | pitcher_game_logs = 0 rows on backfill | Switched `collect_boxscores()` to `/game/{gamePk}/boxscore` per game; backfilled April 1-11 (~1,350 rows) |

### Known Ongoing Issues
- Statcast data thin in April (small sample size for team stats early season)
- FanGraphs scraping non-functional — wRC+ not available
- Rolling stats active: pitcher_game_logs/team_game_logs backfilled April 1-11; auto-collected nightly via --results; blend kicks in at 5+ games

---

## Weight Tuning Log

Record any changes to `config.py WEIGHTS` here with reasoning.

| Date | Change | Reason | Result |
|------|--------|--------|--------|
| 2026-04-10 | Initial weights set | Baseline — pitching 30%, offense 20%, advanced 15%, market 10%, bullpen 10%, momentum 10%, weather 5% | TBD |
| 2026-04-10 | Bullpen +7pp (10→17%), Pitching −5pp (30→25%), Advanced −2pp (15→13%) | 2024+2025 backtest (4,855 games). Bullpen lift +0.050 rivaled offense; pitching lift +0.080 was highest but not dominant enough to justify 30%. Advanced lift near zero (likely end-of-season stats approximation, not true signal gap — small trim only). Momentum/weather held constant — all-neutral historical placeholders make them untestable. Model underconfident: actual win rates beat predictions by 5-14pp across all calibration buckets. | TBD — revisit after 30-50 live picks |

## Infrastructure Notes

- Scheduler migrated from cron to launchd on 2026-04-11 (macOS TCC blocked cron from accessing ~/Projects/)
- Project moved from ~/Documents/Claude/ to ~/Projects/Claude/ to avoid TCC-protected Documents folder
- Plists: ~/Library/LaunchAgents/com.marc.mlb-picks-engine.*.plist | Wrappers: run.sh, monitor.sh, run_optimizer.sh
- monitor.plist uses StartInterval=1800 (every 30 min); no-ops immediately when no pending picks

## Bug Log

| Date | Bug | Fix |
|------|-----|-----|
| 2026-04-11 | `--refresh` compared O/U pick confidence against ML confidence → false "reduce confidence" alerts for all O/U picks | Fixed: look up O/U confidence from `refreshed["ou_pick"]` when pick_type is over/under |
| 2026-04-11 | `--refresh` `still_approved` checked only by game id, not pick type → O/U picks could be wrongly cancelled if ML dropped | Fixed: track `approved_by_type = {(mlb_game_id, pick_type)}` |
| 2026-04-11 | `save_pick` had no dedup — each engine run inserted duplicate picks | Fixed: `pick_already_sent_today()` check before insert; dry run skips DB entirely |
| 2026-04-11 | `run_results()` sent 0-0-0 Discord recap when no picks existed (missing `return` after "Nothing to grade") or when all picks were already graded | Fixed: early `return` after empty picks check; guard against `total == 0 and pushes == 0` before `send_results()` |
| 2026-04-11 | `--refresh` sent cancel/reduce alerts for games already in progress or final — not actionable | Fixed: check `refreshed["status"]` in refresh loop; skip anything not in `{Scheduled, Pre-Game, Warmup, Delayed Start}` |
| 2026-04-12 | `run_results()` crashed with `ValueError` parsing O/U total line — notes field format `"Total line: 8.5 \| Lineups confirmed"` broke naive `.replace()` + `float()` | Fixed: `_parse_total_line()` uses regex `r"Total line:\s*([\d.]+)"` to extract number regardless of trailing text |
| 2026-04-12 | `collect_boxscores()` returned 0 pitcher rows for historical dates — `schedule?hydrate=boxscore` omits player-level stats for past games | Fixed: fetch `/game/{gamePk}/boxscore` per game; backfilled April 1-11 |
| 2026-04-12 | `fetch_travel_context()` called with `target_date=None` from `collect_game_data()` — `date.fromisoformat(None)` crashes → travel fatigue signal returns `{}` for all 15 teams on every run | Fixed: pass `g["game_date"]` (always str) instead of `target_date`; added `isinstance(game_date, str)` guard inside function |

---

## System Snapshot — 2026-04-12 (updated session 4)

### What's been built
- 7-agent weighted scoring engine with Discord alerts
- Backtester (`backtest.py`) — 2024+2025 historical validation, 4,855 games
- Bullpen fatigue signal — last 5 days of boxscores, heavy/moderate thresholds
- `--game` flag (`engine.py --game X`) — full 7-agent analysis on any game matching team name X, sent directly to Discord; bypasses confidence threshold and approved picks flow; supports partial name match and multi-token queries
- Game time in Discord alerts — `game_time_utc` from MLB API (`g["gameDate"]`), displayed as PT (`America/Los_Angeles`) via `discord_bot._format_game_time()`
- `analysis_log` table — all 15 games logged daily with ML + O/U predictions; updated on every `--refresh` run so evening games get confirmed lineup data before 11pm grading; `UNIQUE(game_date, mlb_game_id)` prevents duplicates
- `--status` shows two accuracy views: **PICKS SENT** (only approved picks) and **MODEL ML / MODEL O/U** (all 15 games — measures raw model signal independent of pick filter)
- **EV gate** — `_calculate_ev()` in analysis.py; `MIN_EV = -0.02` in config.py; rejects picks at clearly bad odds; ev_score in DB + Discord alerts
- **Pitcher scratch monitor** — `monitor.py` + `monitor.sh`; polls MLB API; Discord alert per side (away/home) per game; `scratch_alerts` table with UNIQUE(game_date, mlb_game_id, side)
- **Lineup strength scoring** — `fetch_lineup_batting(player_ids)` batch API in data_mlb.py; offense agent adjusts score when confirmed lineups OPS differs from team season OPS; session-level cache
- **Weather at game start time** — `fetch_venue_weather()` uses `game_time_utc` + `zoneinfo` to select correct forecast hour; falls back to 7pm
- **Travel fatigue** — `fetch_travel_context()` in data_mlb.py; away penalty: road_games≥5 → +0.04, tz_changes≥2 → +0.05, cap 0.08; in momentum agent
- **ROI tracking** — `ml_odds`/`ou_odds` stored in picks table at send time; `get_roi_summary()` calculates true unit P&L; `--status` shows net units
- **Rolling stats pipeline** — `pitcher_game_logs` + `team_game_logs` tables; `collect_boxscores()` uses `/game/{gamePk}/boxscore` per game (not hydrate — hydrate omits player stats for historical dates); auto-collects after `--results`; backfill via `--collect DATE`; blend weights: <5gs = season, 5-9 = 40%, 10-19 = 60%, ≥20 = 75%; pitching/offense/bullpen agents all blend; agent scores stored in `analysis_log` (7 columns) for optimizer signal
- **Agent scores in analysis_log** — all 7 agent scores stored daily across all 15 games; optimizer has full signal data independent of pick filter
- **Home/away SP splits** — `fetch_pitcher_home_away_splits()` in data_mlb.py; away SP uses `away_era` split, home SP uses `home_era` split as `_blend()` base; `_pitcher_split_cache` per session
- **Bullpen top reliever ERA** — `get_bullpen_top_relievers()` in database.py; top 3 relievers by IP (last 7d); IP-weighted ERA appended to bullpen edge
- **Line movement tracking** — `opening_lines` table (INSERT OR IGNORE); Watch alert in `--refresh` when ML implied prob drops ≥5pp or total moves ≥0.5 against pick
- **F5 picks** — `fetch_f5_odds()` (`baseball_mlb_h1` sport key); `_analyze_f5_pick()` fires when |pitching_score| ≥ 0.20; `pick_type = "f5_ml"`; graded via innings 1-5 linescore; Discord shows "F5 ML (First 5 Innings)"
- **Half-Kelly sizing** — `kelly_stake()` in analysis.py; 0.25–2.0x stake shown in Discord as `**Stake:** Xx units`
- **Opponent-adjusted rolling ERA** — `opponent_team_id` in `pitcher_game_logs`; `get_pitcher_rolling_stats_adjusted()` weights by `opponent_rpg / 4.3`

### Backtest findings (2024+2025, 4,855 games)
| Metric | Value |
|--------|-------|
| Overall win rate | 58.9% |
| Conf 8 win rate | 85.2% (46/54) |
| Conf 7 win rate | 72.3% (188/260) |
| Calibration bias | Model underconfident by 5-14pp across all buckets |
| Pitching lift | +0.080 (strongest) |
| Bullpen lift | +0.050 |
| Offense lift | +0.041 |
| Advanced lift | −0.009 (near zero — likely stats-approximation artifact) |
| Momentum lift | N/A — all-neutral in backtest (no point-in-time streak data) |
| Weather lift | N/A — all-neutral in backtest (no historical weather) |

### Current weights
| Agent | Weight |
|-------|--------|
| Pitching | 25% |
| Offense | 20% |
| Bullpen | 17% |
| Advanced | 13% |
| Momentum | 10% |
| Market | 10% |
| Weather | 5% |

### Next review trigger
Weekly optimizer runs every Sunday at 9pm — fully autonomous. See COMPLETED_IMPROVEMENTS.md for what has been implemented.

---

## Improvement Roadmap — updated 2026-04-12 (session 4)

Ranked by estimated impact on pick quality. Items marked ✅ are done.
Items marked 🔲 are not yet scheduled.

### Tier 1 — Highest impact on win rate

| # | Improvement | Status | Notes |
|---|-------------|--------|-------|
| 1 | **Expected Value (EV) gate** | ✅ Done 2026-04-11 | MIN_EV=-0.02 in config.py; O/U uses confidence-based win prob |
| 2 | **Pitcher scratch / lineup change detection** | ✅ Done 2026-04-11 | monitor.py; launchd plist active (every 30 min) |
| 3 | **Lineup strength scoring** | ✅ Done 2026-04-11 | Batch MLB people API; session cache |
| 4 | **Rolling stats pipeline** | ✅ Done 2026-04-12 | pitcher_game_logs + team_game_logs; blend <5/5-9/10-19/20+ games; pitching + offense + bullpen agents |
| 5 | **Home/away SP splits** | ✅ Done 2026-04-12 | fetch_pitcher_home_away_splits(); away SP uses away_era split, home SP uses home_era split |
| 6 | **F5 (First 5 Innings) picks** | ✅ Done 2026-04-12 | baseball_mlb_h1 sport key; fires when |pitching_score| ≥ 0.20; graded via linescore innings 1-5 |
| 7 | **Line movement tracking** | ✅ Done 2026-04-12 | opening_lines table; Watch alert on ≥5pp ML drop or ≥0.5 total move against pick |

### Tier 2 — Solid signal improvements

| # | Improvement | Status | Notes |
|---|-------------|--------|-------|
| 8 | **Weather at game-specific start time** | ✅ Done 2026-04-11 | zoneinfo; falls back to 7pm |
| 9 | **Travel fatigue signal** | ✅ Done 2026-04-11 | Away only; road_games + tz_changes; cap 0.08 |
| 10 | **Actual odds stored for ROI tracking** | ✅ Done 2026-04-11 | ml_odds/ou_odds in picks; net_units in --status |
| 11 | **Bullpen recent ERA from pitcher_game_logs** | ✅ Done 2026-04-12 | get_bullpen_top_relievers(); top 3 by IP last 7d; IP-weighted ERA in bullpen edge |
| 12 | **Pitcher velocity/stuff trends** | 🔲 | Statcast has per-game velo; velo -1-2mph over last month = regression before ERA shows it |
| 13 | **Opponent-adjusted rolling stats** | ✅ Done 2026-04-12 | get_pitcher_rolling_stats_adjusted(); weight = opponent_rpg / 4.3; ≥3 opponent games required |

### Tier 3 — Structural improvements

| # | Improvement | Status | Notes |
|---|-------------|--------|-------|
| 14 | **Kelly criterion pick sizing** | ✅ Done 2026-04-12 | kelly_stake() half-Kelly; 0.25–2.0x; shown in Discord as **Stake:** |
| 15 | **Correlated pick cap** | 🔲 | Max 2 overs/unders/same-division per day |
| 16 | **Backtest point-in-time stats** | 🔲 | April games analyzed with October stats — inflates accuracy |

### In optimizer queue (weekly auto-implementation)
✅ Umpire tendencies expansion (43 umps) — done 2026-04-11
✅ EV gate — done 2026-04-11
✅ Pitcher scratch monitor — done 2026-04-11
✅ ROI tracking — done 2026-04-11
✅ Weather timing — done 2026-04-11
✅ Travel fatigue — done 2026-04-11
✅ Rolling stats pipeline (pitching/offense/bullpen agents) — done 2026-04-12
✅ Home/away pitcher ERA splits (pitching agent) — done 2026-04-12
✅ Opening line movement tracking (market agent) — done 2026-04-12
✅ F5 picks (new pick type) — done 2026-04-12
✅ Bullpen recent ERA from game logs — done 2026-04-12
✅ Kelly criterion sizing — done 2026-04-12
✅ Opponent-adjusted rolling ERA — done 2026-04-12
🔲 API error handling & retry logic (data quality)
🔲 Correlated pick cap (max 2 overs/unders/same-division)
🔲 Pitcher velocity trends (Statcast per-game velo)

---

## Season Summary (update monthly)

| Month | Picks | W-L-P | Win Rate | ROI | Notes |
|-------|-------|-------|----------|-----|-------|
| April 2026 | 0 | 0-0-0 | — | — | Engine launched |
