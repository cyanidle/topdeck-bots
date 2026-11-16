#!/usr/bin/env bash
# Управление встроенным VPN-ядром (Xray). Бинарник ставится вручную на хосте.
# Поиск бинарника: $XRAY_BIN → ./vendor/xray → /usr/local/bin/xray → PATH.
#   ./deploy/vpn.sh up        — отрендерить конфиг (если нужно) и запустить
#   ./deploy/vpn.sh refresh   — заново получить подписку и перезапустить
#   ./deploy/vpn.sh status    — состояние процесса + проверка api.telegram.org через SOCKS
#   ./deploy/vpn.sh logs      — хвост лога
#   ./deploy/vpn.sh down      — остановить
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XRAY_CONFIG:-$ROOT/deploy/xray.config.json}"
PIDFILE="$ROOT/data/xray.pid"
LOG="$ROOT/data/logs/xray.log"
SOCKS="127.0.0.1:${XRAY_SOCKS_PORT:-1080}"

load_env() {
    [ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a
}

find_bin() {
    local candidates=("${XRAY_BIN:-}" "$ROOT/vendor/xray" "/usr/local/bin/xray" "$(command -v xray || true)")
    for candidate in "${candidates[@]}"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

require_bin() {
    if BIN="$(find_bin)"; then
        return 0
    fi
    cat >&2 <<'MSG'
xray binary not found.
Установите Xray-core на хост одним из способов и повторите:
  • распакуйте релиз Xray-linux-64.zip в ./vendor/xray   (chmod +x)
  • либо /usr/local/bin/xray
  • либо задайте XRAY_BIN=/path/to/xray в .env
MSG
    return 1
}

render_config() {
    python3 "$ROOT/deploy/xray_from_sub.py" --out "$CONFIG"
}

wait_socks() {
    for _ in $(seq 1 40); do
        if ss -ltn 2>/dev/null | grep -q "127.0.0.1:${SOCKS##*:} "; then
            return 0
        fi
        sleep 0.25
    done
    return 1
}

telegram_check() {
    local code
    code="$(curl -sS --max-time 12 --socks5-hostname "$SOCKS" -o /dev/null -w '%{http_code}' https://api.telegram.org/ || true)"
    echo "api.telegram.org via $SOCKS -> ${code:-no-answer}"
    case "$code" in
        200|302|404) return 0 ;;
        *) return 1 ;;
    esac
}

cmd_up() {
    load_env
    require_bin
    [ -f "$CONFIG" ] || render_config
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "xray already running (pid $(cat "$PIDFILE"))"
    else
        mkdir -p "$(dirname "$LOG")" "$(dirname "$PIDFILE")"
        nohup "$BIN" run -c "$CONFIG" >>"$LOG" 2>&1 &
        echo $! >"$PIDFILE"
        echo "xray started (pid $(cat "$PIDFILE")), log: $LOG"
    fi
    wait_socks && echo "socks listening on $SOCKS" || { echo "socks did not come up; see $LOG" >&2; return 1; }
    telegram_check || echo "WARN: api.telegram.org недоступен через прокси — проверьте подписку/узел" >&2
    return 0
}

cmd_refresh() {
    load_env
    render_config
    cmd_down || true
    cmd_up
}

cmd_down() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        kill "$(cat "$PIDFILE")" 2>/dev/null || true
        rm -f "$PIDFILE"
        echo "xray stopped"
    else
        rm -f "$PIDFILE"
        echo "nothing to stop"
    fi
    return 0
}

cmd_status() {
    if BIN="$(find_bin)"; then
        echo "binary: $BIN ($("$BIN" version 2>/dev/null | head -1))"
    else
        echo "binary: NOT FOUND"
    fi
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "xray: running (pid $(cat "$PIDFILE"))"
    else
        echo "xray: stopped"
    fi
    ss -ltn 2>/dev/null | grep "127.0.0.1:${SOCKS##*:} " || true
    [ -f "$CONFIG" ] && python3 -c "import json;json.load(open('$CONFIG'));print('config: valid json')" || echo "config: absent"
    telegram_check || true
    return 0
}

case "${1:-status}" in
    up) cmd_up ;;
    down) cmd_down ;;
    refresh) cmd_refresh ;;
    status) cmd_status ;;
    logs) tail -n "${2:-50}" "$LOG" 2>/dev/null || echo "no log yet: $LOG" ;;
    *) echo "usage: $0 {up|down|refresh|status|logs}" >&2; exit 2 ;;
esac
