# MLB Picks Engine — Pipeline Framework

Last updated: 2026-04-11

---

## Architecture at a Glance

```
MLB Stats API ──┐
Baseball Savant ─┤                              ┌─ Discord Alerts
The Odds API ───┼──► Data Layer ──► 7 Agents ──┼─ Pick Updates
Open-Meteo ────┘                               └─ Daily Results
```

---

## Daily Pipeline

```
8:00 AM   ── Full Analysis Run
              │
              ├─ Fetch 15 games (schedule, pitchers, lineups, odds, weather, Statcast)
              ├─ Run 7-agent scoring on each game
              ├─ EV gate + confidence/edge filter
              ├─ Send approved picks to Discord (max 5)
              └─ Log all 15 games to analysis_log

11:00 AM  ┐
 1:00 PM  ├─ --refresh (x4)
 3:00 PM  │    ├─ Re-score active picks with updated lineups/odds
 5:00 PM  ┘    ├─ Cancel alert if pick drops below threshold
               └─ Reduce confidence alert if conf drops 2+

Every 30m ── Pitcher Scratch Monitor
              ├─ Compare current probable pitcher vs stored at send time
              └─ Discord alert immediately on SP change (per side, deduped)

11:00 PM  ── Results & Grading
              ├─ Grade all sent picks vs final scores
              ├─ Calculate true unit ROI (using stored send-time odds)
              └─ Send W/L recap to Discord

Sunday 9pm ── Autonomous Optimizer
              ├─ Analyze 30-day pick performance + agent signal differentials
              ├─ Select highest-priority improvement from queue
              ├─ Implement via Claude CLI (code change or config change)
              ├─ Run pytest — revert on failure
              └─ Commit + send Discord improvement report
```

---

## Data Layer

| Source | What's Fetched | Notes |
|--------|---------------|-------|
| MLB Stats API | Schedule, probable pitchers, pitcher game logs, team batting/pitching/records, venue coords, HP umpires, confirmed lineups, pitcher rest days, travel schedule | Free |
| Baseball Savant | Team xwOBA, barrel rate, hard-hit rate, pitcher xERA (437+ pitchers) | Free, cached daily |
| The Odds API | Moneyline, run line, totals from multiple books | 500 req/month free |
| Open-Meteo | Hourly weather forecast at actual game start time (via `game_time_utc` + `zoneinfo`) | Free |

---

## 7-Agent Scoring Engine

Each agent scores **−1.0 to +1.0** (negative = away edge, positive = home edge).

```
Game Dict
    │
    ├─► Pitching Agent (25%)
    │     ERA, WHIP, K/BB, K/9, handedness matchup
    │     Rest days: short (<4d) = −0.12 | extra (5-6d) = +0.05 | rust (8d+) = −0.03
    │
    ├─► Offense Agent (20%)
    │     OPS, OBP, SLG, runs/game
    │     Confirmed lineup: adjusts score if starter OPS differs from team season avg
    │
    ├─► Bullpen Agent (17%)
    │     Team ERA/WHIP/save%, holds
    │     Fatigue: last 3 days >12 IP = −0.15 | >8 IP = −0.08
    │
    ├─► Advanced/Statcast Agent (13%)
    │     xwOBA luck diff (regression signal)
    │     Barrel rate diff ≥2%, hard-hit rate diff ≥4%
    │     Pitcher xERA vs ERA (lucky/unlucky regression)
    │
    ├─► Momentum Agent (10%)
    │     Win streaks (3+ / 5+), losing streaks (4+)
    │     Win % differential
    │     Travel fatigue: road games ≥5 = +0.04 | tz crosses ≥2 = +0.05 | cap 0.08
    │
    ├─► Market Agent (10%)
    │     Model prob vs implied prob edge
    │     Edge ≥5% = real alpha signal
    │
    └─► Weather/Park Agent (5%)
          Temp, wind speed/direction at game start time
          Park factors (30 parks, COL 1.28 → SF 0.89)
          HP umpire tendencies (43 umps)
```

**Composite score** = weighted sum → normalized to win probability → confidence 1–10

---

## Pick Filter (3 Gates)

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
         (win_prob × payout − loss_prob)
         ML: payout = 100/|odds| if negative, odds/100 if positive
         O/U: uses confidence/10 as win prob
      │
      ▼
Approved Pick (max 5/day)
```

---

## Pick Types & Storage

```
Approved Pick ──► save_pick()
                    │
                    ├─ pick_type: moneyline | over | under
                    ├─ confidence, win_probability, edge_score
                    ├─ ev_score
                    ├─ ml_odds / ou_odds  (send-time odds for true ROI)
                    ├─ 6 agent edge scores
                    └─ discord_sent flag

analysis_log ──► ALL 15 games logged regardless of pick threshold
                  Powers MODEL ML / MODEL O/U accuracy in --status
                  (model signal independent of pick filter)
```

---

## Discord Alert Types

| Trigger | Format |
|---------|--------|
| Approved pick | `🚨 MLB HIGH-CONFIDENCE PICK` — game, pick, conf, win prob, EV, odds block, 6-agent edge summary, lineup status |
| SP scratched | `⚠️ PITCHER SCRATCH ALERT` — old vs new pitcher, per side (away/home) |
| Pick cancelled | `⚠️ MLB PICK UPDATE` — cancel or reduce confidence |
| 11pm results | `✅ MLB DAILY RESULTS` — W/L/P recap |
| Sunday optimizer | Discord report — what changed, live vs backtest signal comparison per agent |

---

## --status Output

```
PICKS SENT:  5W - 2L - 0P  (71.4% win rate)  +1.23 units  [7 graded]
MODEL ML:    9W - 6L  (60.0% accuracy)  [15 games]
MODEL O/U:   7W - 4L  (63.6% accuracy)  [11 games]
```

- **PICKS SENT** — only approved sent picks; win rate = W/(W+L); units = true P&L using stored odds
- **MODEL ML/O/U** — all 15 daily games; measures raw pipeline signal independent of pick threshold

---

## Autonomous Optimizer Queue

Runs every Sunday at 9pm. One improvement per week. Never repeats (tracked via `COMPLETED_IMPROVEMENTS.md`).

Priority order:
```
1. Data quality issues (log errors)        ← always first if present
2. Weight rebalance (needs 20+ picks)      ← live signal vs backtest blend
3. Threshold tuning (needs 30+ picks)      ← raise/lower MIN_CONFIDENCE
4. Code queue (in order):
   ✅ Umpire expansion (43 umps)
   ✅ EV gate
   ✅ Pitcher scratch monitor
   ✅ ROI tracking
   ✅ Weather timing
   ✅ Lineup strength scoring
   ✅ Travel fatigue
   🔲 Rolling 7-day team trends
   🔲 Home/away pitcher ERA splits
   🔲 Opening line movement tracking
   🔲 API error handling & retry logic
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
| Pitching | 25% | +0.080 | Strongest signal; slightly reduced from 30% |
| Offense | 20% | +0.041 | Confirmed lineup scoring added |
| Bullpen | 17% | +0.050 | Raised from 10% — most underweighted |
| Advanced | 13% | −0.009 | Near-zero lift likely end-of-season stats artifact |
| Momentum | 10% | N/A | Travel fatigue added; streak data not in backtest |
| Market | 10% | N/A | EV gate added; not testable historically |
| Weather | 5% | N/A | Game-time forecast added; not testable historically |

---

## File Map

```
mlb-picks-engine/
├── engine.py                   orchestrator + all CLI flags
├── analysis.py                 7 agents, EV gate, risk filter, watchlist
├── data_mlb.py                 all MLB/Statcast/weather fetches + travel + lineup batting
├── data_odds.py                odds consensus (mode for lines, implied prob for ML)
├── discord_bot.py              all Discord message formatting + webhook sends
├── database.py                 SQLite — picks, analysis_log, scratch_alerts, games, teams
├── config.py                   WEIGHTS, thresholds, PARK_FACTORS (30), UMPIRE_TENDENCIES (43)
├── monitor.py                  pitcher scratch monitor (every 30 min via launchd)
├── optimizer.py                weekly autonomous improvement engine
├── backtest.py                 2024+2025 historical validator (4,855 games)
├── COMPLETED_IMPROVEMENTS.md   optimizer dedup tracking
├── PIPELINE.md                 this file
├── INSIGHTS.md                 calibration log, bias tracker, weight tuning history
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
| `mlb-picks-engine.optimizer` | Sunday 9:00 PM | `optimizer.py` |
