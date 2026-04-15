# MLB Picks Engine — Odds & Data Sources Reference

## Data Sources

| Source | What it provides | Cost |
|--------|-----------------|------|
| MLB Stats API | Schedule, probable pitchers, pitcher game logs, team batting/pitching/records, venue coords, HP officials, confirmed lineups, home/away SP splits | Free |
| MLB Stats API | Boxscores per game (`/game/{gamePk}/boxscore`) for pitcher_game_logs — NOT `schedule?hydrate=boxscore` (omits player stats for historical dates) | Free |
| MLB Stats API | Linescore per game (`/game/{pk}/linescore`) for F5 grading | Free |
| Baseball Savant | Team xwOBA, barrel rate, hard-hit rate, pitcher xERA (437+ pitchers) — 3 CSV endpoints cached daily | Free |
| The Odds API | Moneyline, run line, totals (full-game) | 500 req/month free |
| The Odds API | F5 moneyline + totals (`baseball_mlb_h1` sport key) | Same key, separate quota |
| Open-Meteo | Hourly weather forecast at actual game start time | Free |

---

## Consensus Odds Logic (`data_odds.py`)

| Market | Line | Price |
|--------|------|-------|
| Moneyline | Implied probability average → converted back to American | N/A |
| Run line | **Mode** (most common ±1.5 line across books) | Average across books |
| Totals | **Mode** (most common line across books) | Average across books |

**Notes:**
- ML values > ±500 excluded (outlier/prop markets)
- Only standard ±1.5 run lines used (not alternate spreads)
- Pre-game filter: games with `commence_time ≤ now_utc` are skipped (no live in-game odds)
- F5 vs full-game disambiguation: when multiple entries match a game, highest total line = full game

---

## F5 Picks (First 5 Innings)

- Sport key: `baseball_mlb_h1`
- Trigger: `|pitching_score| ≥ F5_PITCHING_THRESHOLD (0.20)` **AND** `own_bullpen_score ≤ F5_BULLPEN_THRESHOLD (-0.10)`
  - SP has clear edge AND their own bullpen is weak (can't trust pen to hold the full game)
  - own_bullpen_score = home pen score when picking home; negated score when picking away
- Direction: pitching_score > 0 → F5 Home ML; < 0 → F5 Away ML
- Confidence: score ≥ 0.40 → 9 | ≥ 0.30 → 8 | ≥ 0.20 → 7
- Grading: `/game/{pk}/linescore` — sum innings 1–5 per team; home_f5 > away_f5 → home won; tie → push
- `match_f5_odds_to_game()` matches F5 odds to scheduled game by team name

---

## EV Calculation

- **ML:** `win_prob × payout − loss_prob × 1.0`
- **O/U:** `(confidence / 10) × payout − (1 − confidence/10) × 1.0`
- **F5:** same as O/U formula
- `MIN_EV = -0.02` in config.py (gate 3 of pick filter)

## Kelly Sizing

Half-Kelly formula in `kelly_stake()` (analysis.py):
```
b = payout per $1  (−150 → 0.667;  +130 → 1.30)
full_kelly = (b × p − q) / b
half_kelly = full_kelly × 0.5
stake = max(0.25, min(half_kelly, 2.0))
```
Returns 1.0 when odds unavailable.
