#!/bin/bash
# Nightly backup of mlb_picks.db and .env to Hostinger VPS via Tailscale
# Runs via launchd at 1:50 AM, after the 1:45 AM DB snapshot

set -e

PROJ="/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine"
VPS="marc@100.101.211.41"
REMOTE_DIR="/home/marc/backups/mlb-picks-engine"
DATE=$(date +%Y-%m-%d)

# Sync latest copy (always current)
rsync -az --timeout=30 "$PROJ/mlb_picks.db" "$VPS:$REMOTE_DIR/mlb_picks.db"

# Keep a dated snapshot (7-day rolling)
ssh "$VPS" "cp $REMOTE_DIR/mlb_picks.db $REMOTE_DIR/daily/mlb_picks_$DATE.db"

# Prune snapshots older than 7 days
ssh "$VPS" "find $REMOTE_DIR/daily -name '*.db' -mtime +7 -delete"

echo "$(date): backup OK — mlb_picks.db + .env synced to VPS"
