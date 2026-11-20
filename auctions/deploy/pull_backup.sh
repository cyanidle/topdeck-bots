#!/usr/bin/env bash
# Атомарно забирает часовой снимок БД с боевого хоста в локальный каталог.
#   REMOTE=root@host [SRC=~/topdeck-bots/auctions/data/topbot.sqlite3.hourly]
#   [DEST=/data/backups/topdeck-bots/remote] [KEEP=48] ./deploy/pull_backup.sh
set -euo pipefail

REMOTE="${REMOTE:?задайте REMOTE=user@host}"
SRC="${SRC:-~/topdeck-bots/auctions/data/topbot.sqlite3.hourly}"
DEST="${DEST:-/data/backups/topdeck-bots/remote}"
KEEP="${KEEP:-48}"
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=20}"

mkdir -p "$DEST"
incoming="$DEST/.incoming.$$"
trap 'rm -f "$incoming"' EXIT

fetch() {
    local attempt
    for attempt in 1 2 3 4 5; do
        if scp $SSH_OPTS -q "$REMOTE:$SRC" "$incoming"; then
            return 0
        fi
        echo "scp не удался (попытка $attempt)" >&2
        sleep $((attempt * 5))
    done
    return 1
}

fetch

python3 - "$incoming" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
try:
    verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    conn.close()
if verdict != "ok":
    raise SystemExit(f"integrity_check failed: {verdict}")
PY

target="$DEST/topbot-$(date +%Y%m%d-%H%M%S).sqlite3"
chmod 600 "$incoming"
mv -f "$incoming" "$target"
echo "pulled: $target ($(stat -c%s "$target") bytes)"

if [ "$KEEP" -gt 0 ]; then
    ls -1t "$DEST"/topbot-*.sqlite3 | tail -n +$((KEEP + 1)) | while read -r old; do
        rm -f "$old"
        echo "pruned: $old"
    done
fi
