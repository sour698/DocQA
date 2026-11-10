#!/usr/bin/env bash
# Restore a dump made by backup.sh into the database in DATABASE_URL, REPLACING what is there.
#
#   DATABASE_URL=postgresql://.../docqa ./deploy/restore.sh backups/docqa-20260101T000000Z.dump
#
# Stop the application first. The target database must exist (empty is fine); the role needs
# permission to create the "vector" extension. TEST YOUR BACKUPS: restore one into a scratch
# database now and then.
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to the database to restore INTO}"
dump="${1:?usage: restore.sh <dump-file>}"
[ -f "$dump" ] || { echo "No such file: $dump" >&2; exit 1; }
if [ "${FORCE:-0}" != "1" ]; then
  read -r -p "This REPLACES the contents of the target database. Type 'restore' to continue: " answer
  [ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
fi
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$dump"
echo "Restored $dump"
