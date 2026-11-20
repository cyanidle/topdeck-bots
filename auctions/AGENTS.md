# AGENTS.md — правила работы в репозитории topbot

## Коммиты

* **Каждая фича — отдельный коммит.** Одна законченная вещь (новая кнопка, новая команда, рефакторинг, правка доков по фиче) — один коммит. Не сваливай несколько несвязанных изменений в один коммит.
* Сообщение коммита: первая строка — короткое повелительное описание по-русски (`feat: …`, `fix: …`, `refactor: …`, `docs: …`, `test: …`), ниже — тело: что и зачем, если из первой строки это не очевидно.
* **Любую законченную и проверенную работу коммитим и пушим сразу**: `git add -A` → коммит → `git push origin main`. Готовые изменения не оставляем локально «на потом». `git commit --amend`/`rebase`/force-push опубликованных веток — только по прямой просьбе.
* Перед коммитом прогоняй тесты: `.venv/bin/python -m unittest discover -s tests -t .` (все зелёные) и `git status` — в индекс не должны попасть `.env`, `data/`, `deploy/xray.config.json`, `*.egg-info/`.

## Деплой

* **Закончил работу — на сервере должен крутиться актуальный код.** Прод живёт на `ssh root@147.45.45.218` в `~/topdeck-bots` (бот — `~/topdeck-bots/auctions`, user-юнит только `topbot.service`: Telegram и Scryfall доступны напрямую, туннель и `xray.service` там не нужны). После пуша: `ssh root@147.45.45.218 'cd ~/topdeck-bots && git pull --ff-only && systemctl --user restart topbot.service'`, затем `systemctl --user is-active topbot.service` и `curl -sS http://127.0.0.1:8085/healthz` **на самом сервере**. Соединение до него флапает — для серии команд держите `ssh -M -S /tmp/cm-td -o ControlPersist=3600 -fN root@147.45.45.218` и дальше ходите через `-S /tmp/cm-td`.
* Бэкап с прода забирает **этот** хост: `topdeck-pull.timer` (user-юнит, каждый час в :05) запускает `deploy/pull_backup.sh`, который по scp тянет `data/topbot.sqlite3.hourly` в `/data/backups/topdeck-bots/remote/` (проверка `integrity_check`, атомарная замена, хранится 48 снимков). Ставится/переставляется `./deploy/systemd/install-pull.sh root@147.45.45.218`. Локальные `topbot.service`, `xray.service` и `topbot-backup.timer` на этом хосте остановлены и отключены — бот здесь больше не крутится.
* Правки схемы/данных (миграции в `Database._migrate`) тоже требуют рестарта: процесс держит старую схему в памяти. Перед рестартом с миграцией — бэкап: `systemctl --user start topbot-backup.service` (копии в `/data/backups/topdeck-bots/`). Свежий снимок всегда есть рядом с БД — `data/topbot.sqlite3.hourly` (часовая задача в процессе бота); внешний сервер забирает именно его, а не сырые `data/topbot.sqlite3*` (часть данных в `-wal`).
* `/healthz` отвечает не сразу (15–30 с на старт), а изменения в кэше цен видны только после первого прогона `PricePoller` (запускается сразу после старта).

## Тесты и проверки

* Юнит-тесты — только `unittest` через venv, без pytest-фикстур: `.venv/bin/python -m unittest discover -s tests -t .`
* Живые проверки внешних API — через `run_command` и heredoc с `.venv/bin/python` (не через `run_python`: он запускает системный python3 без aiogram).
* Бот локально: `./topbot.sh` — тонкая обёртка (`exec .venv/bin/python -m topbot`, проверка `.venv`/`.env`, `cd` в корень), рассчитан на супервизор: без pid-файлов и демонизации, логи в stdout/stderr. Внутри — aiogram polling + поллеры аукционов/цен + планировщик окон + web-UI на `127.0.0.1:8080`; файл-лог приложения (JSON Lines) — `data/logs/bot.jsonl`.

## Код

* Внутренние DTO (лоты, предложения, карты, строки watchlist, счётчики, опросы) — **dataclass'ы в `topbot/models.py`**, а не plain dict; `sqlite3.Row` допустим только как сырой результат SQL.
* `models.py` импортирует `matching.normalize`, поэтому `matching.py` не должен импортировать `models` во время выполнения (только `if TYPE_CHECKING`).
* В `logging`-вызовах `extra={...}` нельзя использовать имена атрибутов `LogRecord` (`name`, `msg`, `args`, `module`, `filename`, `message`, `asctime`, `levelname`, `process`, `threadName`, `taskName`, …): `Logger.makeRecord` бросает `KeyError` и хендлер молча падает. Страж — `tests/test_log_extra.py`.
* Комментарии не пишем: код и сообщения коммитов должны быть понятны без них.
* Меняешь правила матчинга лотов (`topbot/matching.py`) — подними `MATCH_VERSION`: `Database` при старте сбрасывает `lots.matched_hash` при расхождении версии, иначе уже лежащие лоты останутся со старыми привязками.

## Документация

* Поведение бота описано в `docs/spec-product.md` (команды, кнопки, тексты), поллер — в `docs/spec-poller.md`, схема БД — в `docs/spec-data-model.md`. Меняешь поведение — правь соответствующий документ тем же коммитом.
