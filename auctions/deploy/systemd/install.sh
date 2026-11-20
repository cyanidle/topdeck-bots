#!/usr/bin/env bash
# Установка user-юнитов topbot. Ожидает репозиторий в ~/topdeck-bots (пути в юнитах — %h/topdeck-bots/...).
# Ставится только то, что есть на хосте: xray.service — при найденном бинарнике Xray,
# юниты суточного бэкапа — при наличии /data/backups.
#   ./deploy/systemd/install.sh        — поставить и включить
#   ./deploy/systemd/install.sh --now  — плюс сразу перезапустить
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DROPIN="$TARGET/topbot.service.d"
BACKUP_DIR="/data/backups/topdeck-bots"

if [ $# -gt 0 ] && [ "${1:-}" != "--now" ]; then
    echo "usage: $0 [--now]" >&2
    exit 2
fi

find_xray() {
    local candidates=("${XRAY_BIN:-}" "$ROOT/vendor/xray" "/usr/local/bin/xray" "$(command -v xray || true)")
    for candidate in "${candidates[@]}"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

UNITS=(topbot.service)
ENABLE=(topbot.service)
STOPPED=()

if find_xray >/dev/null; then
    UNITS+=(xray.service)
    ENABLE+=(xray.service)
    mkdir -p "$DROPIN"
    printf '[Unit]\nAfter=xray.service\nWants=xray.service\n' > "$DROPIN/xray.conf"
    echo "xray найден: ставлю xray.service и зависимость в topbot.service"
else
    rm -f "$DROPIN/xray.conf"
    rmdir "$DROPIN" 2>/dev/null || true
    STOPPED+=(xray.service)
    echo "xray не найден: xray.service не ставится, бот ходит напрямую"
fi

if [ -d /data ]; then
    UNITS+=(topbot-backup.service topbot-backup.timer)
    ENABLE+=(topbot-backup.timer)
    echo "есть /data: ставлю суточный бэкап в $BACKUP_DIR"
else
    STOPPED+=(topbot-backup.timer)
    echo "нет /data: суточный бэкап не ставится (часовой снимок рядом с БД остаётся)"
fi

mkdir -p "$TARGET"
for unit in "${UNITS[@]}"; do
    cp "$ROOT/deploy/systemd/$unit" "$TARGET/$unit"
done

systemctl --user daemon-reload
systemctl --user enable "${ENABLE[@]}"
if [ ${#STOPPED[@]} -gt 0 ]; then
    systemctl --user disable --now "${STOPPED[@]}" 2>/dev/null || true
fi

if [ -f "$TARGET/topbot-backup.timer" ]; then
    systemctl --user start topbot-backup.timer
fi
loginctl enable-linger "$USER" || echo "WARN: не удалось включить linger — юниты остановятся после logout" >&2

if [ "${1:-}" = "--now" ]; then
    if [ -f "$TARGET/xray.service" ]; then
        systemctl --user restart xray.service
    fi
    systemctl --user restart topbot.service
fi

systemctl --user --no-pager list-units 'topbot*' 'xray*' || true
systemctl --user --no-pager list-timers 'topbot*' || true
