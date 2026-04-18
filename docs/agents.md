# MLB Picks Engine — 7-Agent Scoring Reference

Each agent scores −1.0 (away edge) to +1.0 (home edge). Composite = weighted sum → normalized win probability → confidence 1–10.

## Weights

| Agent | Weight | Backtest Lift |
|-------|--------|--------------|
| Pitching | 25% | +0.080 |
| Offense | 20% | +0.041 |
| Bullpen | 17% | +0.050 |
| Advanced Metrics | 13% | −0.009 |
| Momentum | 10% | N/A |
| Market Value | 10% | N/A |
| Weather/Environment | 5% | N/A |

All weights live in `config.py WEIGHTS`. Never hardcode in analysis.py.

---

## Pitching Agent (25%)

- ERA, WHIP, K/BB, K/9 — venue-specific home/away split used as base (`fetch_pitcher_home_away_splits()`)
- Blended with opponent-adjusted 21-day rolling ERA when ≥5 starts available
- Blend: <5gs = season only | 5-9gs = 40% rolling | 10-19gs = 60% | ≥20gs = 75%
- `_pitcher_split_cache` prevents re-fetching per session; uses is-not-None guard (not `or`) to preserve 0.0 ERA

**Rest:**
- ≤3 days (short rest): ±0.12 penalty
- 4 days (normal): neutral
- 5–6 days (extra): ±0.05 bonus
- ≥8 days (rust): ±0.03 penalty

**Handedness:** LHP vs high-K lineup (≥24.5% K rate) = ±0.06

**Pitch Count Fatigue:** Last start ≥105 pitches = −0.04 penalty (via `last_pitch_count()`)

**GB/FB Ratio:** GB% ≥55% vs hitter park = −0.04 | FB% ≤35% vs pitcher park = +0.04 (via `get_gb_fb_ratio()`, 21-game rolling)

---

## Offense Agent (20%)

- OPS, OBP, SLG, runs/game — blended with 14-day rolling team R/G + OBP proxy
- Confirmed lineup: adjusts score if starter OPS differs from team season avg (`fetch_lineup_batting()`)
- `fetch_lineup_batting(player_ids)` — batch MLB people API, session-level cache

---

## Bullpen Agent (17%)

- Team ERA/WHIP/save% — blended with 14-day rolling bullpen ERA/WHIP
- Top 3 relievers by IP (last 7d) with IP-weighted ERA appended to edge string (`get_bullpen_top_relievers()`)

**Fatigue** (`_bullpen_fatigue_penalty()` in analysis.py, based on `ip_last_3`):
- ≤8.0 IP → no penalty
- 8–12 IP → −0.08
- >12 IP → −0.15

**Inherited Runner Strand Rate** (`get_inherited_runner_rate()`, 7-game rolling):
- ≥60% strand rate (elite): +0.04 bonus
- <40% strand rate (poor): −0.04 penalty
- Requires ≥3 inherited runners in sample; else no adjustment

---

## Advanced / Statcast Agent (13%)

Data from Baseball Savant (3 CSV endpoints, cached daily). Falls back to plate discipline if unavailable.

- **xwOBA luck diff** (wOBA − xwOBA): positive = lucky/expect regression
- **Barrel rate diff**: ≥2% gap triggers adjustment
- **Hard-hit rate diff**: ≥4% gap triggers adjustment
- **Pitcher xERA vs ERA**: ERA − xERA < −0.60 = lucky (ERA likely rises); > +0.75 = unlucky (ERA likely falls)

---

## Momentum Agent (10%)

- Win streaks (3+/5+), losing streaks (4+), win % differential
- **Travel fatigue** (`fetch_travel_context()`): away-team only
  - Road games ≥5 consecutive: +0.04
  - Timezone changes ≥2 in last 5 days: +0.05
  - Cap: 0.08

**Stolen Base Rate** (`get_team_stolen_base_rate()`, 14-game rolling):
- ≥1.5 SB/game = +0.04 speed edge (offensive pressure signal)
- Requires ≥5 games of data

---

## Market Agent (10%)

- Model win prob vs bookmaker implied prob — edge ≥5% = alpha signal
- Line movement: opening vs current — sharp-money warning on significant move
- Opening lines captured via `opening_lines` table (INSERT OR IGNORE — first capture kept)
- Refresh compares current odds to opening: ML implied prob drop ≥5pp or total ±0.5 against pick → Watch alert

---

## Weather/Environment Agent (5%)

**Weather** (Open-Meteo, via `fetch_venue_weather()` with `game_time_utc` + zoneinfo):
- Cold (<45°F): −0.15
- Wind out (E/SE/NE, ≥10mph): up to +0.20
- Wind in (W/SW/NW, ≥10mph): up to −0.20
- Rain ≥70%: −0.10
- Falls back to idx=19 (7pm local) when game time unavailable

**Park Factors** (`config.py PARK_FACTORS`, keyed by home team abbreviation, 30 parks):
- Applied as `(park_factor − 1.0) × 0.5` to score
- Also applied to projected run totals
- Range: COL 1.28 → SF 0.89

**HP Umpire** (`config.py UMPIRE_TENDENCIES`, 43 umps):
- `run_factor`: effect on scoring
- `k_factor`: effect on strikeouts
- Unknown umps default to neutral (0.0)

---

## Rolling Stats Blend

`_blend(season_val, rolling_val, n_games)` in analysis.py:
- <5 games: season only
- 5–9: 40% rolling / 60% season
- 10–19: 60% rolling / 40% season
- ≥20: 75% rolling / 25% season

Pitching uses opponent-adjusted rolling ERA (`get_pitcher_rolling_stats_adjusted()`):
- Weight per game = `opponent_rpg / 4.3` (league avg)
- Requires ≥3 opponent R/G data points; else weight = 1.0

---

## Lineup Cards

- Fetched via MLB API `lineups` hydrate (~3.5 hrs before game)
- Both confirmed → "Lineups confirmed" in Discord notes
- Not confirmed → "Lineup TBD — monitor before first pitch"

---

## Gotchas

**All thresholds in config.py** — never hardcode in analysis.py. Optimizer reads config.py to tune values. Includes MIN_CONFIDENCE, MIN_EDGE_SCORE, MIN_EV, HOME_FIELD_ADVANTAGE, BULLPEN_ERA_RUST_THRESHOLD.

**O/U confidence float cast** — K-rate nudge adds 0.5, bullpen nudge adds 1. Always cast with `int(round(conf))` not `int(conf)` to avoid truncation.

**Wind direction is compass** — `_wind_direction_label()` returns `N/NE/E/SE/S/SW/W/NW`. Use in both `score_weather()` and `_project_score()`. Never use `"out to CF"` style strings.

**fetch_venue_weather() is forecast-only** — Open-Meteo `/v1/forecast` only works for today + future. For historical dates use `fetch_venue_weather_archive()` (`archive-api.open-meteo.com/v1/archive`). `backfill_game_totals_weather()` calls the archive variant.

**fetch_pitcher_stats() returns {} on no season stats** — pitcher hasn't started yet → `home_pitcher_stats` is `{}` → `home_p.get('name','?')` returns `'?'`. Always fall back to `game.get('home_pitcher_name')` (populated from `probablePitcher.fullName` in schedule fetch).

**_parse_total_line() returns None on failure** — O/U grading pushes when total line can't be parsed or is out of range (5–15). If adding new O/U grading paths always guard: `if total_line is None: status = "push"`.
