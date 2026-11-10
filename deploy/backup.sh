#!/usr/bin/env bash
# Back up the WHOLE application (accounts, original files, chunks, vectors, logs) with one command.
#
#   DATABASE_URL=postgresql://... ./deploy/backup.sh [output-dir]      # default: ./backups
#   KEEP_DAYS=30 ./deploy/backup.sh /mnt/backups                       # delete dumps older than 30 days
#
# Needs the PostgreSQL client tools (pg_dump / pg_restore). Run it from cron or a scheduled job, and
# copy the dumps OFF the database server (object storage, another machine).
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to the database to back up}"
out_dir="${1:-backups}"
keep_days="${KEEP_DAYS:-14}"
mkdir -p "$out_dir"
file="$out_dir/docqa-$(date -u +%Y%m%dT%H%M%SZ).dump"

pg_dump --format=custom --no-owner --dbname="$DATABASE_URL" --file="$file"
pg_restore --list "$file" > /dev/null   # proves the dump file is readable
find "$out_dir" -name 'docqa-*.dump' -mtime +"$keep_days" -delete
echo "$file ($(du -h "$file" | cut -f1))"
