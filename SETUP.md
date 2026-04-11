# MLB Picks Engine — Setup Guide

## Quick Start

### 1. Create Your Discord Webhook

1. Open **Discord** and go to your private server
2. Click the **gear icon** next to your picks channel → **Integrations**
3. Click **Webhooks** → **New Webhook**
4. Name it something like `MLB Picks Bot`
5. Copy the **Webhook URL**
6. Paste it into `config.py` → `DISCORD_WEBHOOK_URL`

### 2. Get Your Odds API Key (Free)

1. Go to [the-odds-api.com](https://the-odds-api.com)
2. Sign up for a **free account** (500 requests/month)
3. Copy your **API Key**
4. Paste it into `config.py` → `ODDS_API_KEY`

### 3. Configure Settings

Open `config.py` and review:

- `MAX_PICKS_PER_DAY` — max picks sent per day (default: 5)
- `MIN_CONFIDENCE` — minimum confidence threshold (default: 7/10)
- `MIN_EDGE_SCORE` — minimum edge score to approve (default: 0.12)
- `SEASON_YEAR` — current MLB season (default: 2026)

### 4. Run the Engine

```bash
# Full analysis + send picks to Discord
python3 engine.py

# Dry run (analyze only, no Discord messages)
python3 engine.py --test

# Grade today's picks after games finish
python3 engine.py --results

# Check your tracking snapshot
python3 engine.py --status
```

## Project Structure

```
mlb-picks-engine/
├── config.py        — All settings, API keys, webhook URL
├── engine.py        — Main orchestrator (run this)
├── data_mlb.py      — MLB Stats API + web scraping
├── data_odds.py     — The Odds API integration
├── analysis.py      — 7-agent weighted analysis engine
├── discord_bot.py   — Discord webhook message sender
├── database.py      — SQLite tracking database
├── mlb_picks.db     — Auto-created database file
└── SETUP.md         — This file
```

## How It Works

The engine runs 7 specialized analysis agents on each game:

| Agent | Weight | What It Does |
|-------|--------|-------------|
| Pitching | 30% | SP ERA, WHIP, K/BB, handedness matchup, pitcher rest days |
| Offense | 20% | Team OPS, OBP, SLG, runs per game |
| Advanced | 15% | Statcast xwOBA luck diff, barrel/hard-hit rate, pitcher xERA regression |
| Market | 10% | Model probability vs market implied probability edge |
| Bullpen | 10% | Team pitching ERA/WHIP, save reliability |
| Momentum | 10% | Win/loss streaks, win percentage differential |
| Weather/Environment | 5% | Temperature, wind, rain, park factors, HP umpire tendencies |

Only picks that pass the **Risk Agent** (confidence ≥ 7, edge ≥ 0.12) get sent to Discord.
