#!/usr/bin/env bash
# Nightly backup of the portal database and catalogue. Example cron (root):
#   15 2 * * * /opt/smartcloud-portal/deploy/backup.sh /opt/smartcloud-portal/data /var/backups/smartcloud-portal
set -euo pipefail
DATA=${1:-./data}; OUT=${2:-/var/backups/smartcloud-portal}; KEEP_DAYS=${KEEP_DAYS:-30}
mkdir -p "$OUT"
TS=$(date +%Y%m%d-%H%M%S)
sqlite3 "$DATA/portal.db" ".backup '$OUT/portal-$TS.db'"
cp "$DATA/catalogue.json" "$OUT/catalogue-$TS.json"
gzip "$OUT/portal-$TS.db"
find "$OUT" -type f -mtime +"$KEEP_DAYS" -delete
echo "Backup written to $OUT (portal-$TS.db.gz)"
