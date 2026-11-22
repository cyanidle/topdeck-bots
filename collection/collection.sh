#!/usr/bin/env bash
# Тонкая обёртка для супервизора: без pid-файлов и демонизации, логи в stdout/stderr.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

venv="${COLLECTION_VENV:-$(dirname "$PWD")/auctions/.venv}"
if [[ ! -x "$venv/bin/python" ]]; then
  echo "python not found: $venv/bin/python (создай venv или задай COLLECTION_VENV)" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo ".env not found (см. .env.example)" >&2
  exit 1
fi

exec "$venv/bin/python" -m collectbot
