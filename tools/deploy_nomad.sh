#!/usr/bin/env bash
# Deploy the current git HEAD to a Diabet Logger instance on NOMAD.
#   tools/deploy_nomad.sh test            → ~/diabet-logger-test (port 8766, project diabet-logger-test)
#   tools/deploy_nomad.sh live <label>    → /opt/diabet-logger  (port 8765) — backup first, always
# Static is rsync'ed INTO the existing dir (bind mount follows the dir inode — never mv it).
# app.py / Python / Dockerfile changes trigger a rebuild; static-only changes do not.
set -euo pipefail
TARGET=${1:?test|live}; LABEL=${2:-deploy}
HOST=gferen@192.168.8.42
case "$TARGET" in
  test) DIR='~/diabet-logger-test'; PROJ='-p diabet-logger-test'; PORT=8766; CT=diabet-logger-test ;;
  live) DIR=/opt/diabet-logger;     PROJ='';                       PORT=8765; CT=diabet-logger ;;
  *) echo "usage: $0 test|live [label]"; exit 2 ;;
esac
cd "$(git rev-parse --show-toplevel)"
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "uncommitted changes — commit first"; exit 1; }
REV=$(git rev-parse --short HEAD); TS=$(date +%F-%H%M)
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
git archive HEAD app.py juggluco_pull.py dedupe.py requirements.txt Dockerfile static | tar x -C "$STAGE"

if [ "$TARGET" = live ]; then
  echo "== backup ($LABEL-$TS)"
  ssh -o BatchMode=yes $HOST "set -e; umask 077; mkdir -p ~/deploy-backups; cd $DIR
    docker exec $CT python -c \"import sqlite3; s=sqlite3.connect('/data/diabet.db'); d=sqlite3.connect('/data/diabet.pre-$LABEL-$TS.db'); s.backup(d); d.close(); print('db snapshot rows:', sqlite3.connect('/data/diabet.pre-$LABEL-$TS.db').execute('select count(*) from entries').fetchone()[0])\"
    IMG=\$(docker inspect $CT --format '{{.Config.Image}}'); docker tag \"\$IMG\" \"\$IMG:pre-$LABEL-$TS\"; echo \"image tag: \$IMG:pre-$LABEL-$TS\"
    tar czf ~/deploy-backups/opt-diabet-logger-pre-$LABEL-$TS.tgz --exclude=./data --exclude='./data.bak-*' -C $DIR . && echo 'tgz: ~/deploy-backups/opt-diabet-logger-pre-$LABEL-$TS.tgz'"
  PREV=$(ssh -o BatchMode=yes $HOST "cat $DIR/.deployed-rev 2>/dev/null" || true)
  git cat-file -e "${PREV:-x}^{commit}" 2>/dev/null || PREV=main   # before the first scripted deploy live == main
  git tag -a "pre-$LABEL-$TS" "$PREV" -m "live before $LABEL ($TS); rollback: docker tag <img>:pre-$LABEL-$TS <img>:latest && docker compose up -d --no-build"
  echo "git tag: pre-$LABEL-$TS -> $PREV"
fi

echo "== sync $REV → $TARGET"
if [ "$TARGET" = live ]; then git archive HEAD docker-compose.yml | tar x -C "$STAGE"; COMPOSE="$STAGE/docker-compose.yml"; else COMPOSE=; fi
OUT=$(rsync -rlci -e "ssh -o BatchMode=yes" "$STAGE"/app.py "$STAGE"/juggluco_pull.py "$STAGE"/dedupe.py "$STAGE"/requirements.txt "$STAGE"/Dockerfile $COMPOSE "$HOST:$DIR/")
echo "$OUT"
rsync -rlci --delete --exclude='*.bak*' --exclude='*.OLD' --exclude='*.pre-*' -e "ssh -o BatchMode=yes" "$STAGE"/static/ "$HOST:$DIR/static/"
if [ -n "$OUT" ] || [ "${FORCE_BUILD:-0}" = 1 ]; then
  echo "== python/Dockerfile changed → rebuild"
  ssh -o BatchMode=yes $HOST "cd $DIR && docker compose $PROJ up -d --build 2>&1 | tail -2"
else
  echo "== static only → no rebuild (bind mount)"
  ssh -o BatchMode=yes $HOST "cd $DIR && docker compose $PROJ up -d 2>&1 | tail -1"
fi
ssh -o BatchMode=yes $HOST "echo $REV > $DIR/.deployed-rev; sleep 4; curl -s localhost:$PORT/api/health; echo; docker exec $CT md5sum /app/app.py /app/static/index.html" 
echo "local: $(git show HEAD:app.py | md5sum | cut -c1-32)  app.py | $(git show HEAD:static/index.html | md5sum | cut -c1-32)  index.html"
