"""Telegram-бот коллекций MTG: медиа на входе, CRUD коллекции на выходе.

Переиспользует `topbot` из `auctions/` (БД, Scryfall-клиент, конфиг, логи) и
ctypes-биндинг движка распознавания из `cards/python/cardscan`; оба соседних
пути добавляются в `sys.path` здесь, чтобы бот и тесты запускались из любого cwd.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AUCTIONS_PYTHON = REPO_ROOT / "auctions"
CARDS_PYTHON = REPO_ROOT / "cards" / "python"


def _ensure_importable(name: str, path: Path) -> None:
    if importlib.util.find_spec(name) is None:
        sys.path.insert(0, str(path))


_ensure_importable("topbot", AUCTIONS_PYTHON)
_ensure_importable("cardscan", CARDS_PYTHON)
