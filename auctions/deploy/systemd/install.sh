#!/usr/bin/env bash
# Установка user-юнитов topbot. Ожидает репозиторий в ~/topdeck-bots (пути в юнитах — %h/topdeck-bots/...).
#   ./deploy/systemd/install.sh        — поставить и включить
#   ./deploy/systemd/install.sh --now  — плюс сразу перезапустить
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(xray.service topbot.service topbot-backup.service topbot-backup.timer)
ENABLE=(xray.service topbot.service topbot-backup.timer)

if [ $# -gt 0 ] && [ "${1:-}" != "--now" ]; then
    echo "usage: $0 [--now]" >&2
    exit 2
fi

mkdir -p "$TARGET"
for unit in "${UNITS[@]}"; do
    cp "$ROOT/deploy/systemd/$unit" "$TARGET/$unit"
done

systemctl --user daemon-reload
systemctl --user enable "${ENABLE[@]}"
systemctl --user start topbot-backup.timer
loginctl enable-linger "$USER" || echo "WARN: не удалось включить linger — юниты остановятся после logout" >&2

if [ "${1:-}" = "--now" ]; then
    systemctl --user restart xray.service
    systemctl --user restart topbot.service
fi

systemctl --user --no-pager list-units 'topbot*' 'xray*'
systemctl --user --no-pager list-timers topbot-backup.timer
