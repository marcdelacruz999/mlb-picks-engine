# MLB Picks Engine — Infrastructure

## Backup

`mlb_picks.db` is gitignored and backed up nightly to a Hostinger VPS via Tailscale.

**Script:** `scripts/backup_to_vps.sh`
**Schedule:** 1:50 AM daily (launchd: `com.marc.mlb-picks-engine.backup`)
**Destination:** `marc@100.101.211.41:/home/marc/backups/mlb-picks-engine/`
- `mlb_picks.db` — always-current copy
- `daily/mlb_picks_YYYY-MM-DD.db` — 7-day rolling snapshots

**VPS access:** `ssh marc@100.101.211.41` (Tailscale must be connected)

**Disaster recovery:**
```bash
git clone https://github.com/marcdelacruz999/mlb-picks-engine
scp marc@100.101.211.41:/home/marc/backups/mlb-picks-engine/mlb_picks.db mlb-picks-engine/
```

## API Keys

No `.env` file. Keys are hardcoded in `config.py` and pushed to GitHub:
- `ODDS_API_KEY` — The Odds API
- `DISCORD_WEBHOOK_URL` — Discord webhook for picks + alerts
