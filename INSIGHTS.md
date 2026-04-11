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
| — | — | — | — |

### Known Ongoing Issues
- Statcast data thin in April (small sample size for team stats early season)
- FanGraphs scraping non-functional — wRC+ not available
- Home/away pitcher splits not fetched — model uses season ERA only
- Bullpen fatigue (last 3-7 days usage) not yet implemented

---

## Weight Tuning Log

Record any changes to `config.py WEIGHTS` here with reasoning.

| Date | Change | Reason | Result |
|------|--------|--------|--------|
| 2026-04-10 | Initial weights set | Baseline — pitching 30%, offense 20%, advanced 15%, market 10%, bullpen 10%, momentum 10%, weather 5% | TBD |

---

## Season Summary (update monthly)

| Month | Picks | W-L-P | Win Rate | ROI | Notes |
|-------|-------|-------|----------|-----|-------|
| April 2026 | 0 | 0-0-0 | — | — | Engine launched |
