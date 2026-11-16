#!/usr/bin/env bash
# Запуск бота (exec python -m topbot) для супервизора; `./topbot.sh vpn …` — прокси в deploy/vpn.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"

die() {
    echo "$*" >&2
    exit 1
}

if [ "${1:-}" = "vpn" ]; then
    shift
    if [ $# -eq 0 ]; then
        set -- up
    fi
    exec "$ROOT/deploy/vpn.sh" "$@"
fi

if [ $# -gt 0 ]; then
    die "usage: $0 [vpn [up|refresh|status|logs|down]]"
fi

[ -x "$PY" ] || die ".venv не найден. Подготовьте окружение:
  cd $ROOT && python3 -m venv .venv && .venv/bin/pip install -e ."

[ -f "$ROOT/.env" ] || [ -n "${BOT_TOKEN:-}" ] || die ".env не найден (и BOT_TOKEN не задан в окружении):
  cd $ROOT && cp .env.example .env, заполнить BOT_TOKEN, chmod 600 .env"

cd "$ROOT"
exec "$PY" -m topbot
