# AGENTS.md — правила работы в репозитории topbot

## Коммиты

* **Каждая фича — отдельный локальный коммит.** Одна законченная вещь (новая кнопка, новая команда, рефакторинг, правка доков по фиче) — один коммит. Не сваливай несколько несвязанных изменений в один коммит и не оставляй работу в рабочем дереве «на потом».
* Сообщение коммита: первая строка — короткое повелительное описание по-русски (`feat: …`, `fix: …`, `refactor: …`, `docs: …`, `test: …`), ниже — тело: что и зачем, если из первой строки это не очевидно.
* Коммиты только локальные: `git push`/`git commit --amend`/`rebase` опубликованных веток — только по прямой просьбе.
* Перед коммитом прогоняй тесты: `.venv/bin/python -m unittest discover -s tests -t .` (все зелёные) и `git status` — в индекс не должны попасть `.env`, `data/`, `deploy/xray.config.json`, `*.egg-info/`.

## Тесты и проверки

* Юнит-тесты — только `unittest` через venv, без pytest-фикстур: `.venv/bin/python -m unittest discover -s tests -t .`
* Живые проверки внешних API — через `run_command` и heredoc с `.venv/bin/python` (не через `run_python`: он запускает системный python3 без aiogram).
* Бот локально: `.venv/bin/python -m topbot` (aiogram polling + поллер раз в 840 с + web-UI на `127.0.0.1:8080`); лог — `data/logs/bot.jsonl`.

## Код

* Внутренние DTO (лоты, предложения, карты, строки watchlist, счётчики, опросы) — **dataclass'ы в `topbot/models.py`**, а не plain dict; `sqlite3.Row` допустим только как сырой результат SQL.
* `models.py` импортирует `matching.normalize`, поэтому `matching.py` не должен импортировать `models` во время выполнения (только `if TYPE_CHECKING`).
* В `logging`-вызовах `extra={...}` нельзя использовать имена атрибутов `LogRecord` (`name`, `msg`, `args`, `module`, `filename`, `message`, `asctime`, `levelname`, `process`, `threadName`, `taskName`, …): `Logger.makeRecord` бросает `KeyError` и хендлер молча падает. Страж — `tests/test_log_extra.py`.
* Комментарии не пишем: код и сообщения коммитов должны быть понятны без них.
* Меняешь правила матчинга лотов (`topbot/matching.py`) — подними `MATCH_VERSION`: `Database` при старте сбрасывает `lots.matched_hash` при расхождении версии, иначе уже лежащие лоты останутся со старыми привязками.

## Документация

* Поведение бота описано в `docs/spec-product.md` (команды, кнопки, тексты), поллер — в `docs/spec-poller.md`, схема БД — в `docs/spec-data-model.md`. Меняешь поведение — правь соответствующий документ тем же коммитом.
