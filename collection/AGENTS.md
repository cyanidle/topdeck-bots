# AGENTS.md — правила работы в `collection/`

## Запуск и проверки

* Бот локально: `./collection.sh` — venv по умолчанию берётся из
  `../auctions/.venv` (aiogram/aiohttp уже там), переопределяется `COLLECTION_VENV`.
  Рассчитан на супервизор: логи в stdout/stderr.
* Юнит-тесты — только unittest через venv: из `collection/` —
  `../auctions/.venv/bin/python -m unittest discover -s tests -t .`.
* Живые проверки движка — `CARDSCAN_SAMPLE_IMAGE=~/Documents/cards.jpg` даёт
  live-скан в `tests/test_scanner.py` (скипается без файла).
* В индекс не должны попасть `.env`, `data/`, `*.sqlite3*`.

## Код

* Максимальный шаринг с соседями: `topbot` (БД-базовый класс, Scryfall-клиент,
  `Card`, `plural`, логи, env-парсер) и ctypes-биндинг `cardscan` — пути до них
  добавляет `collectbot/__init__.py`, дублировать их код здесь нельзя.
* Внутренние DTO — dataclass'ы в `models.py`.
* Пользовательские медиа не храним: `TemporaryDirectory` → скан → удаление;
  в БД только идентичности печатей.
* Комментарии не пишем.

## Коммиты

Как в корневых правилах: одна законченная вещь — один коммит, сообщение
по-русски (`feat:`, `fix:`, `docs:`, `test:`), перед коммитом тесты зелёные.
