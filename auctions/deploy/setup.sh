#!/usr/bin/env bash
# Идемпотентная подготовка хоста: venv, зависимости, каталоги, .env. Можно запускать повторно.
#   ./deploy/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() {
    echo "$*" >&2
    exit 1
}

command -v python3 >/dev/null || die "python3 не найден: поставьте python3 (>= 3.10) и python3-venv"

python3 - <<'PY' || die "нужен Python 3.10+ (проверьте python3 -V)"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv || die "не удалось создать .venv (поставьте пакет python3-venv)"
fi

.venv/bin/python -m pip --version >/dev/null 2>&1 || die "в .venv нет pip: удалите .venv и создайте заново (нужен python3-venv/ensurepip)"

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e '.[bot,web]'

mkdir -p data/logs

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "создан .env из .env.example: заполните BOT_TOKEN; для работы без туннеля оставьте TELEGRAM_PROXY= и SCRYFALL_PROXY= пустыми"
fi
chmod 600 .env

.venv/bin/python - <<'PY'
import sys
from topbot.config import Settings

settings = Settings.from_env()
print(f"python:          {sys.version.split()[0]}")
print(f"db:              {settings.db_path} ({'есть' if __import__('os').path.isfile(settings.db_path) else 'нет — положите снимок'})")
print(f"web:             {settings.web_bind}:{settings.web_port}")
print(f"telegram:        {settings.telegram_proxy or 'direct'}")
print(f"scryfall:        {settings.scryfall_proxy or 'direct'} (enabled={settings.scryfall_enabled})")
print(f"hourly backup:   {f'every {settings.backup_interval_s}s' if settings.backup_interval_s > 0 else 'off'}")
if not settings.bot_token:
    print("BOT_TOKEN пуст — заполните .env, иначе бот не стартует")
PY

echo
echo "дальше: ./deploy/systemd/install.sh --now  (юниты + сразу запуск бота)"
