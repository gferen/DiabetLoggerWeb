#!/usr/bin/env bash
# Rotate deploy backups ON THE SERVER. Dry-run unless --apply.
#   ssh $DEPLOY_HOST 'bash -s' < tools/rotate_backups.sh -- [--apply] [--keep N] [--min-age-days D]
# Never touches the live data/diabet.db and never removes anything named *pre-p0* (baseline).
#  - Docker image tags  <img>:pre-*            → keep pre-p0 + newest N; remove the rest (untag)
#  - ~/deploy-backups/opt-*-pre-*.tgz          → keep pre-p0 + newest N; delete the rest
#  - data/diabet.pre-*.db snapshots            → keep pre-p0 + newest 1 always; delete others only
#                                                when older than D days (default 7) — 7-day buffer
#  - data.bak-* dirs next to data/             → delete when older than D days
#  - one-time: old root-owned DBs in data/ (diabet-2026-10-20.db, diabet.db.bak) → ~/deploy-backups/archive/
set -euo pipefail
APPLY=0; KEEP=3; AGE=7
while [ $# -gt 0 ]; do case "$1" in
  --) ;; --apply) APPLY=1 ;; --keep) KEEP=$2; shift ;; --min-age-days) AGE=$2; shift ;;
  *) echo "unknown arg $1"; exit 2 ;; esac; shift; done
APP=${DEPLOY_LIVE_DIR:-/opt/diabet-logger}; BK=~/deploy-backups; IMG=diabet-logger-diabet-logger
run() { if [ $APPLY = 1 ]; then echo "  DO  $*"; "$@"; else echo "  dry $*"; fi; }
echo "== rotate (apply=$APPLY keep=$KEEP min-age=${AGE}d)"

echo "-- archive old DBs"
mkdir -p "$BK/archive"
for f in "$APP/data/diabet-2026-10-20.db" "$APP/data/diabet.db.bak"; do
  [ -e "$f" ] && run mv "$f" "$BK/archive/" || true
done

echo "-- image tags (keep pre-p0 + newest $KEEP)"
docker images "$IMG" --format '{{.Tag}} {{.CreatedAt}}' | grep '^pre-' | grep -v '^pre-p0' \
  | sort -k2,3r | awk -v k="$KEEP" 'NR>k {print $1}' | while read -r tag; do run docker rmi "$IMG:$tag"; done

echo "-- tgz (keep pre-p0 + newest $KEEP)"
ls -1t "$BK"/opt-diabet-logger-pre-*.tgz 2>/dev/null | grep -v 'pre-p0' | tail -n +$((KEEP+1)) | while read -r f; do run rm -f "$f"; done

echo "-- DB snapshots (keep pre-p0 + newest; others only if older than ${AGE}d)"
NEWEST=$(ls -1t "$APP"/data/diabet.pre-*.db 2>/dev/null | grep -v 'pre-p0' | head -1 || true)
find "$APP/data" -maxdepth 1 -name 'diabet.pre-*.db' ! -name '*pre-p0*' -mtime +"$((AGE-1))" -print | while read -r f; do
  [ "$f" = "$NEWEST" ] && continue
  # root-owned (written by the container) → remove via the container, no sudo
  run docker exec diabet-logger rm -f "/data/$(basename "$f")"
done
echo "   buffered (younger than ${AGE}d, kept for now):"
find "$APP/data" -maxdepth 1 -name 'diabet.pre-*.db' ! -name '*pre-p0*' -mtime -"$AGE" -printf '     %f\n' | sort

echo "-- data.bak-* dirs older than ${AGE}d"
find "$APP" -maxdepth 1 -name 'data.bak-*' -type d -mtime +"$((AGE-1))" -print | while read -r d; do run rm -rf "$d"; done
find "$APP" -maxdepth 1 -name 'data.bak-*' -type d -mtime -"$AGE" -printf '   buffered: %f\n'
echo "== done"
