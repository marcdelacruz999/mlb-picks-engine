# Completed Improvements

This file tracks all improvements implemented by the weekly optimizer or manual sessions.
The optimizer reads this file to prevent re-implementing completed work.

Each entry uses an HTML comment marker for reliable ID matching:
`<!-- id: improvement_id -->`

---

<!-- id: umpire_expansion -->
## Umpire Tendencies Expansion
**Date:** 2026-04-11
**Commit:** feat: expand umpire tendencies table to 43 MLB umpires
**Summary:** Expanded UMPIRE_TENDENCIES in config.py from 14 to 43 umps. Added 29 new entries with run_factor and k_factor values.

---

<!-- id: ev_gate -->
## Expected Value Gate
**Date:** 2026-04-11
**Commit:** feat: add expected value gate to pick approval; fix: correct O/U EV formula, add zero-odds guard, move MIN_EV to config
**Summary:** Added `_calculate_ev()` to analysis.py. `risk_filter()` now rejects picks with EV < MIN_EV (-0.02). ML picks use pick-side odds; O/U picks use `confidence/10*100` as win prob. ev_score stored in picks table and shown in Discord alerts.

---

<!-- id: pitcher_scratch_monitor -->
## Pitcher Scratch Monitor
**Date:** 2026-04-11
**Commit:** feat: add pitcher scratch monitor with Discord alerts; fix: track away/home separately in scratch_alerts
**Summary:** New `monitor.py` + `monitor.sh`. Compares current probable pitchers vs stored at pick-send time. Sends Discord alert on change. scratch_alerts table tracks away/home separately (UNIQUE(game_date, mlb_game_id, side)).

---

<!-- id: lineup_strength -->
## Confirmed Lineup Strength Scoring
**Date:** 2026-04-11
**Commit:** feat: score confirmed lineup strength vs team season average; fix: lineup batting cache sentinel, is-not-None guard
**Summary:** `fetch_lineup_batting(player_ids)` in data_mlb.py fetches individual player batting stats via MLB people API. score_offense() adjusts raw score based on confirmed lineup OPS vs team season OPS. Session-level cache prevents re-fetching.

---

<!-- id: weather_timing -->
## Weather at Game-Specific Start Time
**Date:** 2026-04-11
**Commit:** feat: fetch weather at game-specific start time using hourly forecast
**Summary:** `fetch_venue_weather()` now accepts `game_time_utc` and uses zoneinfo to find the correct hour index in Open-Meteo hourly data. Falls back to idx=19 (7pm) when time unavailable. Adds `forecast_for` to weather dict.

---

<!-- id: travel_fatigue -->
## Travel Fatigue Signal
**Date:** 2026-04-11
**Commit:** feat: add travel fatigue signal to momentum agent; fix: remove unused home_travel fetch, add timezone+cap tests
**Summary:** `fetch_travel_context(team_id, game_date)` computes consecutive_road_games, timezone_changes_last_5d, days_since_off_day. score_momentum() applies away-team penalty: road_games>=5 → +0.04, tz_changes>=2 → +0.05, capped at 0.08.

---

<!-- id: roi_tracking -->
## Send-Time Odds Storage and True ROI
**Date:** 2026-04-11
**Commit:** feat: store send-time odds and calculate true unit ROI in --status; fix: correct ROI denominator
**Summary:** ml_odds and ou_odds columns added to picks table. risk_filter() includes pick-side odds in pick dicts. engine.py stores in pick_record. get_roi_summary() calculates true unit profit/loss. _print_snapshot() shows net units. Denominator is all graded picks (W+L+P).

---

<!-- id: rolling_stats_pipeline -->
<!-- id: rolling_trends -->
## Rolling Stats Pipeline
**Date:** 2026-04-12
**Commit:** feat: add rolling stats pipeline with pitcher_game_logs and team_game_logs
**Summary:** Added pitcher_game_logs and team_game_logs tables. collect_boxscores() uses /game/{gamePk}/boxscore per game (NOT schedule?hydrate=boxscore which omits player stats historically). Auto-collects after --results; backfill via --collect DATE. Blend weights in _blend(): <5 games = season only, 5-9 = 40%, 10-19 = 60%, >=20 = 75%. Agent scores (7 columns) added to analysis_log.

---

<!-- id: home_away_sp_splits -->
<!-- id: home_away_splits -->
## Home/Away SP ERA Splits
**Date:** 2026-04-12
**Commit:** feat: add home/away SP ERA splits to pitching agent
**Summary:** fetch_pitcher_home_away_splits() in data_mlb.py fetches venue-specific ERA/WHIP/K9/BB9 from MLB Stats API hydrate endpoint. _pitcher_split_cache prevents re-fetching. score_pitching() uses away_era split for away SP, home_era split for home SP, as base for _blend(). Uses is-not-None guard (not `or`) to preserve 0.0 ERA values.

---

<!-- id: bullpen_key_relievers -->
## Bullpen Top Reliever ERA
**Date:** 2026-04-12
**Commit:** feat: surface top-reliever ERA from game logs in bullpen agent
**Summary:** get_bullpen_top_relievers() queries pitcher_game_logs for is_starter=0, returns top 3 relievers by total IP over last 7 days with IP-weighted ERA. Appended to bullpen edge string: "Home top pen (7d): Smith, Jones — 2.45 ERA".

---

<!-- id: line_movement_tracking -->
<!-- id: line_movement -->
## Line Movement Tracking
**Date:** 2026-04-12
**Commit:** feat: add line movement tracking and opening line comparison in refresh
**Summary:** opening_lines table (INSERT OR IGNORE — first capture kept). save_opening_lines()/get_opening_lines() in database.py. Captured in run_analysis() and run_refresh(). Line movement check in refresh else-branch fires Watch alert when ML implied prob drops >=5pp or total moves >=0.5 runs against pick direction.

---

<!-- id: f5_picks -->
## F5 (First 5 Innings) Picks
**Date:** 2026-04-12
**Commit:** feat: add F5 odds fetch, analysis, engine wiring, DB migration, grading (Plan B complete)
**Summary:** fetch_f5_odds() uses baseball_mlb_h1 sport key. _analyze_f5_pick() fires when |pitching_score| >= 0.20; confidence 7/8/9 by magnitude; pick_type = "f5_ml". picks table CHECK constraint on pick_type removed via rename/recreate migration. Graded via _grade_f5_pick() using /game/{pk}/linescore innings 1-5. Discord shows "F5 ML (First 5 Innings)".

---

<!-- id: kelly_criterion -->
## Half-Kelly Criterion Stake Sizing
**Date:** 2026-04-12
**Commit:** feat: add half-Kelly criterion stake sizing to picks
**Summary:** kelly_stake() in analysis.py. Half-Kelly formula: full_kelly = (b*p - q) / b; half = *0.5. Floor 0.25x, cap 2.0x. Returns 1.0 when odds unavailable. Wired into all 3 pick types (ML, O/U, F5). Discord shows "**Stake:** Xx units" after EV line.

---

<!-- id: travel_context_none_bug -->
## Travel Fatigue Bug Fix — fromisoformat(None) Crash
**Date:** 2026-04-12
**Commit:** fix: pass g["game_date"] to fetch_travel_context; add isinstance guard
**Summary:** `collect_game_data()` passed `target_date` (which is `None` on all normal runs) to `fetch_travel_context()`. `date.fromisoformat(None)` crashed → all 15 teams returned `{}` → travel fatigue signal was silently zero for every game every day since the feature was added. Fix: use `g["game_date"]` (always a valid YYYY-MM-DD str set by `fetch_todays_games()`). Also added `isinstance(game_date, str)` guard inside the function as a defensive backstop. Tests: 119 pass, 2 pre-existing failures unchanged.

---

<!-- id: opponent_adjusted_era -->
## Opponent-Adjusted Rolling SP ERA
**Date:** 2026-04-12
**Commit:** feat: opponent-adjusted rolling SP ERA; Plan C complete
**Summary:** opponent_team_id column added to pitcher_game_logs (migration + collect_boxscores population). get_pitcher_rolling_stats_adjusted() weights each start by opponent_rpg / 4.3 (league avg), using 14-day opponent R/G window. Requires >=3 opponent games or defaults weight=1.0. Used in collect_game_data() with plain fallback via `or`.

---

<!-- id: api_error_handling -->
## API Error Handling & Retry Logic
**Date:** 2026-04-13
**Commit:** fix: add API retry logic and fix travel context date parsing bug
**Summary:** _api_get() retry wrapper added to data_mlb.py with exponential backoff (3 retries, 2/4/8s delays). All external MLB Stats API calls routed through wrapper. data_odds.py similarly hardened. Prevents transient network failures from silently dropping game data.

---

<!-- id: independent_ml_ou_picks -->
## ML and O/U Picks Are Now Independent Per Game
**Date:** 2026-04-13
**Commit:** feat: send ML and O/U as independent picks; raise MAX_PICKS_PER_DAY to 20; fix O/U edge scoring
**Summary:** Previously a single game could only contribute one pick (ML or O/U, whichever ranked higher). Now both are evaluated independently and both sent to Discord if they pass gates. MAX_PICKS_PER_DAY raised from 5→20. O/U edge_score now based on line gap (abs(projected-line)/8.0) not ML composite edge. O/U edge gate enforced separately. Correlated pick cap task removed from optimizer queue — intentional design decision.

---

<!-- id: ou_line_bug_fix -->
## O/U Total Line Was Saving None in analysis_log
**Date:** 2026-04-13
**Commit:** fix: ou_line was saving None in analysis_log (wrong key 'line' vs 'total_line'); add O/U edge gate
**Summary:** save_analysis_log() called ou.get("line") but the O/U dict uses key "total_line". Result: ou_line was always NULL → run_results() could never grade O/U model accuracy. Fixed in both the morning run and refresh run save paths. Today's rows backfilled via team-name match against live odds.

---

<!-- id: ev_implausible_odds -->
## EV Inflation from Garbage Odds Data
**Date:** 2026-04-13
**Commit:** fix: reject implausible O/U odds (<15 abs value) in EV calc; add O/U edge gate
**Summary:** The Odds API occasionally returns juice like -14 (data error). _calculate_ev() now returns None for abs(odds)<15, treating it as unavailable odds → pick blocked by EV gate. Prevents EV scores like +4.7 on effectively even-money bets.
