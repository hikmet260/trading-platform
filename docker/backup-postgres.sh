#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

while :; do
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  raw="$BACKUP_DIR/.trading-$stamp.dump"
  archive="$BACKUP_DIR/trading-$stamp.dump.gz"
  echo "Starting PostgreSQL backup: $archive"
  if pg_dump --host="${PGHOST:-postgres}" --port="${PGPORT:-5432}" \
      --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
      --no-owner --format=custom --file="$raw"; then
    gzip -c "$raw" > "$archive.tmp"
    test -s "$archive.tmp"
    mv "$archive.tmp" "$archive"
    rm -f "$raw"
    find "$BACKUP_DIR" -type f -name 'trading-*.dump.gz' \
      -mtime "+$BACKUP_RETENTION_DAYS" -delete
    echo "PostgreSQL backup complete: $archive"
    if [ "${BACKUP_ONCE:-false}" = true ]; then
      exit 0
    fi
  else
    rm -f "$raw" "$archive.tmp"
    echo "PostgreSQL backup failed" >&2
    exit 1
  fi
  sleep 86400
done
