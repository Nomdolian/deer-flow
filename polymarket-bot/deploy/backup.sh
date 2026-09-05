#!/usr/bin/env bash
# Nightly SQLite backup to a second disk. Run from cron:
#   0 3 * * * /opt/pmbot/deploy/backup.sh
set -euo pipefail
DB="${1:-/opt/pmbot/pmbot.sqlite}"
DEST="${2:-/backup/pmbot}"
mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/pmbot-$(date -u +%Y%m%d).sqlite'"
find "$DEST" -name 'pmbot-*.sqlite' -mtime +30 -delete
