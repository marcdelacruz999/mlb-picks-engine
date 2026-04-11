# MLB Picks Engine — Claude Code Reference

## Project Location
`/Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/`

## What This Is
A fully automated MLB betting picks engine. It collects live game data, runs a 7-agent weighted analysis, filters for high-confidence plays only, and sends formatted alerts to a private Discord channel via webhook. No manual intervention needed day-to-day.

---

## Daily Automated Schedule (cron)

| Time | Command | What it does |
|------|---------|--------------|
| 9:00 AM | `engine.py` | Full analysis — sends approved picks to Discord (catches 10am games) |
| 11:00 AM | `engine.py --refresh` | Re-validates picks, sends cancel/reduce alerts if edge changed |
| 1:00 PM | `engine.py --refresh` | Same |
| 3:00 PM | `engine.py --refresh` | Same |
| 5:00 PM | `engine.py --refresh` | Same |
| 11:00 PM | `engine.py --results` | Grades picks vs final scores, sends recap to Discord |

All output logged to `engine.log`.

---

## CLI Usage

```bash
python3 engine.py              # Full analysis + send picks to Discord
python3 engine.py --test       # Dry run: analyze only, no Discord
python3 engine.py --refresh    # Re-validate sent picks, send updates if changed
python3 engine.py --results    # Grade today's picks after games finish
python3 engine.py --status     # Print 30-day tracking snapshot
```

---

## Analysis System — 7 Agents

Each agent scores from -1.0 (away edge) to +1.0 (home edge).

| Agent | Weight | Data Source |
|-------|--------|-------------|
| Pitching | 30% | MLB Stats API — ERA, WHIP, K/BB, K/9, handedness matchup, pitcher rest days |
| Offense | 20% | MLB Stats API — OPS, OBP, SLG, runs per game |
| Advanced Metrics | 15% | Baseball Savant Statcast — xwOBA luck diff, barrel rate, hard-hit rate, pitcher xERA regression |
| Market Value | 10% | The Odds API — model prob vs implied prob edge |
| Bullpen | 10% | MLB Stats API — team ERA, WHIP, save %, holds |
| Momentum | 10% | MLB Stats API — win streaks, win %, last 10 |
| Weather/Environment | 5% | Open-Meteo + park factors + HP umpire tendencies |

### Statcast Logic (Advanced Agent)
- **xwOBA luck diff** (wOBA - xwOBA): positive = team outperforming contact quality (lucky, expect regression); negative = underperforming (unlucky, expect bounce back)
- **Barrel rate differential**: ≥2% gap triggers a score adjustment
- **Hard-hit rate differential**: ≥4% gap triggers a score adjustment
- **Pitcher xERA vs ERA**: ERA - xERA < -0.60 = pitcher getting lucky (ERA likely to rise); > +0.75 = unlucky (ERA likely to fall)
- Falls back to plate discipline + WHIP/ERA proxy if Statcast unavailable
- Data source: Baseball Savant (free, no API key) — 3 endpoints cached daily

### Handedness Logic
- Pitcher L/R hand shown in Discord alerts
- LHP vs high-K-rate lineup gets a ±0.06 score adjustment
- K-rate threshold: league avg (22.5%) + 2%

### Pitcher Rest Logic
- ≤3 days rest (short rest): ±0.12 score penalty for that team's SP
- 4 days rest (normal): neutral
- 5-6 days rest (extra rest): ±0.05 bonus
- ≥8 days rest (extended layoff): ±0.03 rust penalty
- Data: MLB Stats API game log endpoint per pitcher, fetched per game

### Weather/Environment Logic (5% weight agent covers all three)
**Weather:**
- Cold (<45°F) → -0.15 (suppresses offense)
- Wind out (E/SE/NE, ≥10mph) → up to +0.20 (hitter-friendly)
- Wind in (W/SW/NW, ≥10mph) → up to -0.20 (pitcher-friendly)
- Rain ≥70% → -0.10
- Coordinates pulled from MLB venue API, forecast from Open-Meteo

**Park Factors** (`config.py PARK_FACTORS`, keyed by home team abbreviation):
- Factor applied as `(park_factor - 1.0) * 0.5` score adjustment
- Also applied directly to projected run totals in score projection
- Range: COL 1.28 (extreme hitter) → SF 0.89 (extreme pitcher)
- 30-park static table (multi-year FanGraphs averages)

**HP Umpire Tendencies** (`config.py UMPIRE_TENDENCIES`):
- HP umpire fetched from MLB API `officials` hydrate at game fetch time
- `run_factor`: +/- effect on scoring (positive = more runs)
- `k_factor`: +/- effect on strikeouts
- 14 known extreme umps in table; unknown umps default to neutral (0.0)

### Lineup Cards
- Fetched via MLB API `lineups` hydrate in schedule call (~3.5 hrs before game)
- If both lineups confirmed: pick notes say "Lineups confirmed"
- If not confirmed: pick notes say "Lineup TBD — monitor before first pitch"
- Watchlist entries reflect unconfirmed lineup risk

---

## Pick Rules

- Allowed types: **moneyline**, **over**, **under**
- Minimum confidence: **7/10**
- Minimum edge score: **0.12**
- Maximum picks per day: **5**
- If nothing qualifies → outputs **PASS**, nothing sent to Discord

---

## Operator Output (each run)

1. **Game Analysis Board** — all 15 games with composite scores
2. **Approved Picks Board** — ranked by confidence
3. **Watchlist** — up to 5 games near threshold (conf 5-6) to monitor
4. **Discord Payload** — exact JSON sent to webhook
5. **Tracking Snapshot** — 30-day record, win rate, ROI

---

## Discord Message Types

| Trigger | Format |
|---------|--------|
| Approved pick | `🚨 MLB HIGH-CONFIDENCE PICK` |
| Pick update (cancel/reduce) | `⚠️ MLB PICK UPDATE` |
| Evening results recap | `✅ MLB DAILY RESULTS` |

Update alerts fire automatically during `--refresh` runs when:
- A pick drops below threshold → **Cancel** alert
- Confidence drops 2+ points → **Reduce Confidence** alert

Each pick alert includes:
- Game, pick, confidence, win probability, projected score
- **Current Odds**: ML, run line (±1.5), total with over/under prices
- **Edge Summary**: Pitching, Offense, Advanced (Statcast), Bullpen, Weather, Market
- **Notes**: lineup status (confirmed or TBD), total line for O/U picks

---

## File Structure

```
mlb-picks-engine/
├── engine.py       — Main orchestrator (run this)
├── analysis.py     — 7-agent weighted scoring + risk filter + watchlist
├── data_mlb.py     — MLB Stats API (schedule, pitchers, batting, records, officials,
│                     lineups, pitcher rest); Baseball Savant Statcast; Open-Meteo weather
├── data_odds.py    — The Odds API (moneyline, run line, totals; pre-game filter; consensus)
├── discord_bot.py  — Webhook sender (pick, update, results); formats edge summary + odds block
├── database.py     — SQLite tracking (picks, games, results, ROI)
├── config.py       — API keys, weights, thresholds, PARK_FACTORS, UMPIRE_TENDENCIES
├── mlb_picks.db    — Auto-created SQLite database
├── engine.log      — Cron output log
├── SETUP.md        — First-time setup instructions
├── INSIGHTS.md     — Daily outcomes, calibration tracker, bias log, weight tuning history
└── CLAUDE.md       — This file
```

---

## Config (config.py)

```python
DISCORD_WEBHOOK_URL = "..."   # Private Discord channel webhook
ODDS_API_KEY = "..."          # The Odds API — 500 free req/month
MAX_PICKS_PER_DAY = 5
MIN_CONFIDENCE = 7
MIN_EDGE_SCORE = 0.12
SEASON_YEAR = 2026
WEIGHTS = {
    "pitching": 0.30, "offense": 0.20, "bullpen": 0.10,
    "advanced": 0.15, "momentum": 0.10, "weather": 0.05, "market": 0.10
}
PARK_FACTORS = { "COL": 1.28, "CIN": 1.13, ..., "SF": 0.89 }  # 30 parks, keyed by team abbr
UMPIRE_TENDENCIES = { "Laz Diaz": {"run_factor": -0.08, ...}, ... }  # 14 extreme umps
```

---

## Database

SQLite at `mlb_picks.db`. Tables:
- `picks` — every approved pick with edge details (`edge_pitching`, `edge_offense`, `edge_advanced`, `edge_bullpen`, `edge_weather`, `edge_market`), `discord_sent` flag, outcome status
- `games` — game records with final scores
- `teams` — all 30 MLB teams with mlb_id, abbreviation, division, league
- `players` — player records
- `pitcher_stats` — season stats per pitcher
- `team_batting` — season batting stats per team
- `bullpen_stats` — season bullpen stats per team
- `odds` — odds snapshots per game
- `daily_results` — wins/losses/pushes/ROI per day

To clear test picks (keep only Discord-sent):
```python
conn.execute("DELETE FROM picks WHERE created_at LIKE ? AND discord_sent=0", (f'{today}%',))
```

---

## Data Sources Summary

| Source | What it provides | Cost |
|--------|-----------------|------|
| MLB Stats API | Schedule, probable pitchers, pitcher game logs (rest), team batting/pitching/records, venue coords, HP officials, confirmed lineups | Free |
| Baseball Savant | Team batting Statcast, team pitching Statcast, pitcher xERA (437+ pitchers) — 3 CSV endpoints cached daily | Free |
| Open-Meteo | Game-time weather forecast via venue lat/lon | Free |
| The Odds API | Moneyline, run line, totals from multiple bookmakers | Free (500 req/month) |
| Discord Webhook | Pick/update/results delivery | Free |

## Known Limitations / Future Improvements

- Rolling 7/14/30-day team trends not yet implemented — uses season stats only
- Home/away pitcher splits not yet fetched — uses season ERA only
- Line movement tracking (opening vs current) not stored
- FanGraphs scraping non-functional (requires JS) — wRC+ not available

## Consensus Odds Logic (`data_odds.py`)

| Market | Line | Price |
|--------|------|-------|
| Moneyline | implied probability average → converted back to American | N/A |
| Run line | mode (most common ±1.5 line across books) | average across books |
| Totals | mode (most common line across books) | average across books |

Notes:
- ML values > ±500 are excluded (outlier/prop markets)
- Only standard ±1.5 run lines are used (not alternate spreads)
- Pre-game filter: games with `commence_time ≤ now_utc` are skipped — no live in-game odds
- F5 vs full-game disambiguation: when multiple entries match a game, the one with the highest total line (full game) is used
