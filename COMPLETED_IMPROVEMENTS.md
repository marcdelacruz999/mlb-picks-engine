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
