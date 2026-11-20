#!/usr/bin/env bash
# Ставит на этот (бэкап) хост таймер выгрузки часового снимка БД с боевого хоста.
#   ./deploy/systemd/install-pull.sh root@147.45.45.218 [--now]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENVFILE="${XDG_CONFIG_HOME:-$HOME/.config}/topdeck-pull.env"
REMOTE="${1:-}"

if [ -z "$REMOTE" ]; then
    echo "usage: $0 user@host [--now]" >&2
    exit 2
fi

mkdir -p "$TARGET" "$(dirname "$ENVFILE")"
cp "$ROOT/deploy/systemd/topdeck-pull.service" "$ROOT/deploy/systemd/topdeck-pull.timer" "$TARGET/"
printf 'REMOTE=%s\n' "$REMOTE" > "$ENVFILE"
chmod 600 "$ENVFILE"

systemctl --user daemon-reload
systemctl --user enable --now topdeck-pull.timer
if [ "${2:-}" = "--now" ]; then
    systemctl --user start topdeck-pull.service
fi
loginctl enable-linger "$USER" || echo "WARN: linger не включён — таймер остановится после logout" >&2
systemctl --user --no-pager list-timers topdeck-pull.timer
