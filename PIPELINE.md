# MLB Picks Engine — Pipeline Framework

Last updated: 2026-04-12

---

## Architecture at a Glance

```
MLB Stats API ──┐
Baseball Savant ─┤                              ┌─ Discord Alerts
The Odds API ───┼──► Data Layer ──► 7 Agents ──┼─ Pick Updates
The Odds API F5 ┤                               └─ Daily Results
Open-Meteo ────┘
```

---

## Daily Pipeline

```
8:00 AM   ── Full Analysis Run
              │
              ├─ Fetch 15 games (schedule, pitchers, lineups, odds, F5 odds, weather, Statcast)
              ├─ Fetch home/away SP splits (MLB Stats API hydrate, cached per session)
              ├─ Fetch opponent-adjusted rolling ERA from pitcher_game_logs
              ├─ Save opening lines per game (INSERT OR IGNORE — first capture)
              ├─ Run 7-agent scoring on each game
              ├─ EV gate + confidence/edge filter
              ├─ Evaluate F5 picks (fires when |pitching_score| ≥ 0.20 AND own team bullpen weak ≤ -0.10)
              ├─ Assign Kelly stake (half-Kelly, 0.25–2.0x) per approved pick
              ├─ Send approved picks to Discord (max 5; ML + O/U + F5 eligible)
              └─ Log all 15 games to analysis_log

11:00 AM  ┐
 1:00 PM  ├─ --refresh (x4)
 3:00 PM  │    ├─ Re-score active picks with updated lineups/odds
 5:00 PM  ┘    ├─ Save current odds (INSERT OR IGNORE — opening lines preserved)
               ├─ Compare current odds to opening lines
               │     ML: implied prob drop ≥5pp → Watch alert
               │     O/U: line moves ≥0.5 against pick → Watch alert
               ├─ Cancel alert if pick drops below threshold
               └─ Reduce confidence alert if conf drops 2+

Every 30m ── Pitcher Scratch Monitor
              ├─ Compare current probable pitcher vs stored at send time
              └─ Discord alert immediately on SP change (per side, deduped)

11:00 PM  ── Results & Grading
              ├─ Grade ML picks vs final score
              ├─ Grade O/U picks vs final total
              ├─ Grade F5 picks via /game/{pk}/linescore (innings 1-5 sum)
              ├─ Calculate true unit ROI (using stored send-time odds + Kelly stake)
              ├─ Send W/L recap to Discord
              └─ Auto-run collect_boxscores() → save pitcher_game_logs + team_game_logs

11:30 PM  ── Daily Optimizer (every night)
              ├─ Analyze 30-day pick performance + agent signal differentials
              ├─ Snapshot full pipeline (files, DB tables, data gaps, rolling stats)
              ├─ Select highest-priority improvement:
              │     Weight rebalance (≥20 picks, 7-day cooldown)
              │     Threshold tune (≥30 picks, 7-day cooldown)
              │     Code queue (Claude CLI, 7-day cooldown between changes)
              ├─ Implement + run pytest — revert on failure
              └─ Send Discord optimizer report (runs every night regardless)
```

---

## Data Layer

| Source | What's Fetched | Notes |
|--------|---------------|-------|
| MLB Stats API | Schedule, probable pitchers, pitcher game logs (rest), team batting/pitching/records, venue coords, HP umpires, confirmed lineups, travel schedule | Free |
| MLB Stats API | Home/away SP ERA/WHIP/K9/BB9 splits per pitcher (`hydrate=stats(type=statSplits)`) | Free, cached per session |
| MLB Stats API | Boxscores per game (`/game/{gamePk}/boxscore`) for pitcher_game_logs | Free, runs after --results |
| MLB Stats API | Linescore per game (`/game/{pk}/linescore`) for F5 grading | Free, runs in --results |
| Baseball Savant | Team xwOBA, barrel rate, hard-hit rate, pitcher xERA (437+ pitchers) | Free, cached daily |
| The Odds API | Moneyline, run line, totals (full-game) | 500 req/month free |
| The Odds API | F5 moneyline + totals (`baseball_mlb_h1` sport key) | Same API key, separate quota |
| Open-Meteo | Hourly weather forecast at actual game start time (via `game_time_utc` + `zoneinfo`) | Free |

---

## 7-Agent Scoring Engine

Each agent scores **−1.0 to +1.0** (negative = away edge, positive = home edge).

```
Game Dict
    │
    ├─► Pitching Agent (25%)
    │     Venue-specific split ERA/WHIP/K9/BB9 (away SP uses away_era, home SP uses home_era)
    │     Blended with opponent-adjusted 21-day rolling ERA when ≥5 starts available
    │     Blend: <5gs = season only | 5-9 = 40% rolling | 10-19 = 60% | ≥20 = 75%
    │     Rest: short (<4d) = −0.12 | extra (5-6d) = +0.05 | rust (8d+) = −0.03
    │     Handedness: LHP vs K-prone lineup (≥24.5% K rate) = ±0.06
    │     Pitch count fatigue: last start ≥105 pitches = −0.04 penalty
    │     GB/FB ratio: GB% ≥55% vs hitter park = −0.04 | FB% ≤35% vs pitcher park = +0.04
    │
    ├─► Offense Agent (20%)
    │     OPS, OBP, SLG, runs/game — blended with 14-day rolling team R/G + OBP proxy
    │     Confirmed lineup: adjusts score if starter OPS differs from team season avg
    │
    ├─► Bullpen Agent (17%)
    │     Team ERA/WHIP/save% — blended with 14-day rolling bullpen ERA/WHIP
    │     Fatigue: last 3 days >12 IP = −0.15 | >8 IP = −0.08
    │     Inherited runner strand rate: ≥60% (elite) = +0.04 | ≤40% (poor) = −0.04
    │     Key relievers: top 3 by IP (last 7d) with IP-weighted ERA appended to edge
    │
    ├─► Advanced/Statcast Agent (13%)
    │     xwOBA luck diff (positive = lucky/expect regression)
    │     Barrel rate diff ≥2%, hard-hit rate diff ≥4%
    │     Pitcher xERA vs ERA (lucky/unlucky regression signal)
    │
    ├─► Momentum Agent (10%)
    │     Win streaks (3+/5+), losing streaks (4+), win % differential
    │     Travel fatigue: road games ≥5 = +0.04 | tz crosses ≥2 = +0.05 | cap 0.08
    │     Stolen base rate: ≥1.5 SB/game (14d rolling) = +0.04 speed edge
    │
    ├─► Market Agent (10%)
    │     Model prob vs bookmaker implied prob edge (≥5% = alpha signal)
    │     Line movement: opening vs current — sharp-money warning on significant move
    │
    └─► Weather/Park Agent (5%)
          Temp, wind speed/direction at actual game start time
          Park factors (30 parks, COL 1.28 → SF 0.89)
          HP umpire tendencies (43 umps)
```

**Composite score** = weighted sum → normalized to win probability → confidence 1–10

---

## Pick Filter (3 Gates + Kelly Sizing)

```
Composite score
      │
      ▼
Gate 1: Confidence ≥ 7/10
      │
      ▼
Gate 2: Edge score ≥ 0.12
      │
      ▼
Gate 3: EV ≥ −0.02
         ML:  win_prob × (payout) − loss_prob × 1.0
         O/U: confidence/10 as win prob (no model-computed O/U prob)
         F5:  confidence/10 as win prob
      │
      ▼
Kelly Sizing (half-Kelly)
         b = payout per $1 (−150 → 0.667; +130 → 1.30)
         full_kelly = (b×p − q) / b
         half_kelly = full_kelly × 0.5
         stake = max(0.25, min(half_kelly, 2.0))  ← floor 0.25x, cap 2.0x
      │
      ▼
Approved Pick (max 5/day, type: moneyline | over | under | f5_ml)
```

---

## Pick Types & Storage

```
Approved Pick ──► save_pick()
                    │
                    ├─ pick_type: moneyline | over | under | f5_ml
                    ├─ confidence, win_probability, edge_score
                    ├─ ev_score
                    ├─ kelly_fraction  (stake multiplier 0.25–2.0x)
                    ├─ ml_odds / ou_odds  (send-time odds for true ROI)
                    ├─ 6 agent edge descriptions
                    └─ discord_sent flag

analysis_log ──► ALL 15 games logged regardless of pick threshold
                  Includes all 7 agent scores as columns
                  Powers MODEL ML / MODEL O/U accuracy in --status
```

---

## F5 Picks (First 5 Innings)

F5 picks fire when the SP has a clear edge but their own team's bullpen is weak — isolate SP quality for 5 innings rather than risk the pen giving back the lead in the full game.

```
Trigger:  |pitching_score| ≥ 0.20  (strong SP edge)
          AND own_bullpen_score ≤ -0.10  (own pen is weak — can't hold full game)
Odds:     The Odds API baseball_mlb_h1 sport key (separate fetch)
Pick dir: pitching_score > 0 → F5 Home ML
          pitching_score < 0 → F5 Away ML
Conf:     score ≥ 0.40 → 9 | ≥ 0.30 → 8 | ≥ 0.20 → 7
Grading:  /game/{pk}/linescore — sum innings 1-5 per team
          home_f5 > away_f5 → f5_home won | tie → push
Discord:  "{Team} F5 ML (First 5 Innings)"
```

---

## Rolling Stats Pipeline

Runs automatically — pitcher and team game logs accumulate daily.

```
After --results (11pm):
    collect_boxscores(today) → pitcher_game_logs + team_game_logs

On demand:
    python3 engine.py --collect YYYY-MM-DD  (backfill a specific date)

pitcher_game_logs:
    mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
    opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs,
    pitch_count, batters_faced, ground_outs, fly_outs, inherited_runners, inherited_runners_scored

team_game_logs:
    mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
    strikeouts, walks, at_bats, left_on_base,
    team_k, team_bb, team_hits_allowed, team_earned_runs, team_pitches

batter_game_logs:
    mlb_game_id, game_date, player_id, team_id, at_bats, hits, doubles, triples,
    home_runs, rbi, strikeouts, walks,
    runs, stolen_bases, hit_by_pitch, plate_appearances

Opponent-adjusted ERA:
    weight = opponent_rpg / 4.3  (league avg)
    requires ≥3 opponent games in 14-day window; else weight = 1.0
    used as primary rolling source; falls back to plain rolling if None
```

---

## Discord Alert Types

| Trigger | Format |
|---------|--------|
| Approved pick | `🚨 MLB HIGH-CONFIDENCE PICK` — game, pick, conf, win prob, **Stake: Xx units**, EV, odds block, 6-agent edge, lineup status |
| SP scratched | `⚠️ PITCHER SCRATCH ALERT` — old vs new pitcher, per side (away/home), deduped |
| Line movement | `⚠️ MLB PICK UPDATE` — Watch action, line movement description |
| Pick cancelled | `⚠️ MLB PICK UPDATE` — Cancel or Reduce Confidence action |
| 11pm results | `✅ MLB DAILY RESULTS` — W/L/P recap with net units |
| Sunday optimizer | Discord report — what changed, live vs backtest signal comparison per agent |

---

## --status Output

```
PICKS SENT:  5W - 2L - 0P  (71.4% win rate)  +1.23 units  [7 graded]
MODEL ML:    9W - 6L  (60.0% accuracy)  [15 games]
MODEL O/U:   7W - 4L  (63.6% accuracy)  [11 games]
```

- **PICKS SENT** — only approved sent picks; units = true P&L using stored send-time odds
- **MODEL ML/O/U** — all 15 daily games; raw pipeline signal independent of pick threshold

---

## Daily Optimizer

Runs every night at 11:30pm (after --results + boxscore collection). Analysis and Discord report always fire. Code/config changes throttled to once per 7 days. Code improvements never repeat (tracked via `COMPLETED_IMPROVEMENTS.md`).

Priority order:
```
1. Data quality issues (log errors)        ← always first if present
2. Weight rebalance (needs 20+ picks)      ← live signal vs backtest blend
3. Threshold tuning (needs 30+ picks)      ← raise/lower MIN_CONFIDENCE
4. Code queue (in order — all done as of 2026-04-12):
   ✅ Umpire expansion (43 umps)
   ✅ EV gate
   ✅ Pitcher scratch monitor
   ✅ ROI tracking
   ✅ Weather timing
   ✅ Lineup strength scoring
   ✅ Travel fatigue
   ✅ Rolling stats pipeline
   ✅ Home/away SP ERA splits
   ✅ Opening line movement tracking
   ✅ Bullpen key reliever ERA
   ✅ F5 picks
   ✅ Kelly criterion sizing
   ✅ Opponent-adjusted rolling ERA
   ✅ API error handling & retry logic
   🔲 Correlated pick cap
   🔲 Pitcher velocity trends
```

**Backtest blend:** 100% backtest prior until 20 live picks → ramps to 80% live signal at 200+ picks.

**Backtest reference (2024+2025, 4,855 games):**
- Overall win rate: 58.9% | Conf 8: 85.2% | Conf 7: 72.3%
- Agent lift: pitching +0.080, bullpen +0.050, offense +0.041, advanced −0.009
- Model underconfident by 5–14pp across all calibration buckets

---

## Current Weights

| Agent | Weight | Backtest Lift | Notes |
|-------|--------|--------------|-------|
| Pitching | 25% | +0.080 | Venue splits + opponent-adjusted rolling ERA |
| Offense | 20% | +0.041 | Confirmed lineup scoring + rolling team R/G |
| Bullpen | 17% | +0.050 | Raised from 10%; key reliever ERA from game logs |
| Advanced | 13% | −0.009 | Near-zero lift — end-of-season stats artifact |
| Momentum | 10% | N/A | Travel fatigue added; streak data not in backtest |
| Market | 10% | N/A | EV gate + line movement tracking |
| Weather | 5% | N/A | Game-time forecast + park factors + umpires |

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `picks` | Approved picks sent to Discord; `pick_type` now includes `f5_ml` |
| `analysis_log` | All 15 games per day; 7 agent score columns; updated on each --refresh |
| `pitcher_game_logs` | Per-start: IP, ER, K, BB, H, HR, opponent_team_id |
| `team_game_logs` | Per-game: R, H, HR, K, BB, AB per team |
| `opening_lines` | First captured odds per game per day (INSERT OR IGNORE) |
| `scratch_alerts` | Pitcher scratch dedup: UNIQUE(game_date, mlb_game_id, side) |
| `games` | Game records with final scores |
| `teams` | 30 teams with mlb_id, abbreviation, division, league |
| `daily_results` | W/L/P/ROI per day |

---

## File Map

```
mlb-picks-engine/
├── engine.py                   orchestrator + CLI flags (--test/--refresh/--results/--status/--game/--collect)
├── analysis.py                 7 agents, _analyze_f5_pick(), kelly_stake(), EV gate, risk filter
├── data_mlb.py                 MLB/Statcast/weather fetches, home/away splits, lineup batting, collect_boxscores()
├── data_odds.py                full-game + F5 odds consensus (mode for lines, implied prob for ML)
├── discord_bot.py              all Discord message formatting + webhook sends
├── database.py                 SQLite — all tables, rolling stats queries, opening_lines, top relievers
├── config.py                   WEIGHTS, thresholds, PARK_FACTORS (30), UMPIRE_TENDENCIES (43)
├── monitor.py                  pitcher scratch monitor (every 30 min via launchd)
├── optimizer.py                weekly autonomous improvement engine
├── backtest.py                 2024+2025 historical validator (4,855 games)
├── COMPLETED_IMPROVEMENTS.md   optimizer dedup tracking (<!-- id: xxx --> markers)
├── PIPELINE.md                 this file
├── INSIGHTS.md                 calibration log, bias tracker, weight tuning, roadmap
├── CLAUDE.md                   developer reference for Claude sessions
└── SETUP.md                    first-time setup instructions
```

---

## launchd Schedule

| Label | Trigger | Command |
|-------|---------|---------|
| `mlb-picks-engine.main` | 8:00 AM daily | `engine.py` |
| `mlb-picks-engine.refresh-11` | 11:00 AM daily | `engine.py --refresh` |
| `mlb-picks-engine.refresh-13` | 1:00 PM daily | `engine.py --refresh` |
| `mlb-picks-engine.refresh-15` | 3:00 PM daily | `engine.py --refresh` |
| `mlb-picks-engine.refresh-17` | 5:00 PM daily | `engine.py --refresh` |
| `mlb-picks-engine.results` | 11:00 PM daily | `engine.py --results` |
| `mlb-picks-engine.monitor` | Every 30 min | `monitor.py` |
| `mlb-picks-engine.optimizer` | 11:30 PM daily | `optimizer.py` |
