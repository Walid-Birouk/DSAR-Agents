#!/bin/sh
# Nightly backup of the web store (sessions, runs, the append-only audit log) from the running app container.
# Sandboxes themselves are deleted after 24 hours by design and are not backed up.
#   crontab: 30 3 * * * /srv/a15/deploy/backup.sh /srv/a15/backups
set -eu
DEST="${1:?usage: backup.sh DEST_DIR}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"
docker compose -f "$(dirname "$0")/docker-compose.yml" exec -T app python -c "
import sqlite3
src = sqlite3.connect('runs/web/web.sqlite')
dst = sqlite3.connect('/tmp/web-backup.sqlite')
src.backup(dst)
dst.close()
"
docker compose -f "$(dirname "$0")/docker-compose.yml" cp app:/tmp/web-backup.sqlite "$DEST/web-$STAMP.sqlite"
gzip "$DEST/web-$STAMP.sqlite"
find "$DEST" -name 'web-*.sqlite.gz' -mtime +"$KEEP_DAYS" -delete
echo "backup written: $DEST/web-$STAMP.sqlite.gz"
