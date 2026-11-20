# Инфраструктура: прокси для Telegram + деплой на сервер

Актуально для РФ-хостинга: **до Telegram напрямую не достучаться**, `api.telegram.org` недоступен. Торговые площадки и Scryfall — наоборот, доступны напрямую (и так быстрее).

## 1. Принцип: прокси встроен в приложение, а не «обёртка»

Требование: прокси **встраивается в сервер** (процесс), никаких `proxychains4 ./bot` в проде.

<div align="center">

```
                       ┌──────────────────────────── xray-core ────────────────────────────┐
topbot (aiogram) ──────► 127.0.0.1:1081 (http-in) ──┬─► proxy (vless+reality+xhttp) ──► api.telegram.org
topbot (Scryfall) ─────► 127.0.0.1:1081 (http-in) ──┘                                        api.scryfall.com
topbot (TOPDeck) ──────► direct ──────────────────────────────────────────────────────► topdeck.ru
curl/отладка ──────────► 127.0.0.1:1080 (socks-in, опционально)
```

* Telegram **и Scryfall** идут через туннель (правила по доменам/`telegram` CIDR).
* `topdeck.ru` — всегда напрямую (быстро и без расхода туннеля).
* Два инбаунда: `socks 127.0.0.1:1080` (для `curl --socks5-hostname` и отладки) и `http 127.0.0.1:1081` (его понимает aiohttp/httpx без доп. зависимостей — им и пользуется приложение).

</div>

* Telegram-трафик идёт через локальный SOCKS5-инбаунд Xray-core.
* `topdeck.ru` и `api.scryfall.com` — direct (правилом маршрутизации Xray или просто «не через прокси», если прокси применяется только к aiogram-сессии).

## 2. Вариант A: Xray-core (единственный, который подходит)

Конфиг **не пишется руками** — он генерируется из подписки скриптом [`deploy/xray_from_sub.py`](../deploy/xray_from_sub.py) (см. §2.2). Структура, которую он выдаёт:

```jsonc
{
  "dns": { "servers": [ { "address": "https://1.1.1.1/dns-query", "domains": [ "<telegram-домены>" ] },
                        "localhost" ], "queryStrategy": "UseIP" },
  "inbounds": [ { "tag": "socks-in", "listen": "127.0.0.1", "port": 1080,
                  "protocol": "socks", "settings": { "auth": "noauth", "udp": false },
                  "sniffing": { "enabled": true, "destOverride": ["http","tls"] } } ],
  "outbounds": [
    { "tag": "direct", "protocol": "freedom" },
    { "tag": "proxy",  "protocol": "vless",
      "settings": { "vnext": [ { "address": "<из подписки>", "port": 443,
        "users": [ { "id": "<uuid>", "encryption": "none", "flow": "" } ] } ] },
      "streamSettings": { "network": "xhttp", "security": "reality",
        "realitySettings": { "serverName": "<sni>", "fingerprint": "firefox",
                             "publicKey": "<pbk>", "shortId": "<sid>", "spiderX": "<spx>" },
        "xhttpSettings": { "path": "/", "mode": "auto",
                           "extra": { "mode": "auto", "xPaddingBytes": "100-1000" } } } },
    { "tag": "block", "protocol": "blackhole" }
  ],
  "routing": { "domainStrategy": "IPIfNonMatch", "rules": [
    { "type": "field", "domain": ["domain:telegram.org","domain:t.me","domain:telegram.me",
                                  "domain:tdesktop.com","domain:telegra.ph",
                                  "domain:telegram-cdn.org","domain:cdn-telegram.org",
                                  "domain:telesco.pe"], "outboundTag": "proxy" },
    { "type": "field", "ip": ["91.108.4.0/22","91.108.8.0/22","91.108.12.0/22","91.108.16.0/22",
                              "91.108.20.0/22","91.108.56.0/22","149.154.160.0/20"], "outboundTag": "proxy" }
  ] }
}
```

Ключевое: **по умолчанию весь трафик идёт `direct`**, в туннель уходят только telegram-домены и их CIDR-диапазоны. `topdeck.ru`, Scryfall, apt/pip — напрямую, как и требует задача.

## 2.1 Быстрый старт (всё в репозитории)

```bash
./deploy/vpn.sh up        # поставить ядро при необходимости, отрендерить конфиг, запустить, проверить Telegram
./deploy/vpn.sh status    # pid + что SOCKS слушает + код ответа api.telegram.org через прокси
./deploy/vpn.sh refresh   # заново забрать подписку (узел/sni могли смениться) и перезапустить
./deploy/vpn.sh logs      # хвост data/logs/xray.log
./deploy/vpn.sh down
```

Состав:

| Файл | Роль |
|---|---|
| `deploy/xray_from_sub.py` | `GET` подписки → base64 → `vless://` → `deploy/xray.config.json` (0600); `--print` печатает конфиг с замаскированными секретами |
| `deploy/vpn.sh` | `up/down/refresh/status/logs`, pid-файл `data/xray.pid`, лог `data/logs/xray.log`; ищет бинарник в `$XRAY_BIN` → `./vendor/xray` → `/usr/local/bin/xray` → `PATH` |

**Бинарник Xray-core в репозитории не хранится и не скачивается скриптами** — он ставится на хост вручную (официальный релиз `Xray-linux-64.zip` → `./vendor/xray` или `/usr/local/bin/xray`, `chmod +x`). Версия — свежая (нужны XHTTP + `xpadding` + REALITY). Если бинарника нет, `./deploy/vpn.sh up` печатает инструкцию и завершается с ошибкой.

Правила маршрутизации не зависят от `geoip.dat`/`geosite.dat`: в конфиг зашиты явные telegram-домены и CIDR-списки (91.108.0.0/16 частично, 149.154.160.0/20, IPv6-блоки Microsoft/Telegram), поэтому ядру не нужны скачиваемые geo-файлы.

`vendor/`, `data/`, `deploy/xray.config.json`, `.env` — в `.gitignore`.

## 3. Вариант B: sing-box — **не подходит для текущей подписки**

Подписка отдаёт `vless` + `security=reality` + **`type=xhttp`** (`mode=auto`, `xPaddingBytes=100-1000`). Транспорт XHTTP и `x_padding_bytes`/ML-KEM-768 поддерживает **Xray-core**, в sing-box XHTTP-транспорта нет → такой узел он не поднимет. Поэтому sing-box оставляем как вариант «если сменится подписка», основной путь — Xray.

## 3.1 Что проверено по факту (2026-11-15)

* Подписка `TD_PROXY_SUB_URL` отдаёт `200`, тело ~520 байт — **base64 одной строкой, без переносов**, внутри ровно один `vless://`-линк (remark — название узла, его даёт провайдер подписки).
* Параметры линка (значения приходят из подписки, в документе замаскированы): `host=<ip узла>`, `port=443`,
  `security=reality`, `sni=<домен из подписки>` (**sni приходит из подписки и может меняться**), `fp=firefox`, `pbk=<pbk>`, `sid=<sid>`, `spx=<spx>`,
  `type=xhttp`, `path=/`, `mode=auto`, `extra={"mode":"auto","xPaddingBytes":"100-1000"}`, `support-x25519mlkem768=true`, `encryption=none`.
* **Версия Xray-core должна быть свежей** (XHTTP + `x_padding_bytes` + ML-KEM-768 — это 25.x). Старые релизы упадут с «unknown transport xhttp».
* **DNS до Telegram заблокирован жёстко:** `curl https://api.telegram.org` → `Resolving timed out after 5000 ms` (резолв не проходит, соединение даже не начинается). Значит:
  * прокси обязателен **до** любого DNS-запроса к telegram-доменам;
  * клиент должен резолвить имена **на стороне прокси** (в `aiohttp_socks` это `rdns=True`, по умолчанию включён; в Xray SOCKS-инбаунде домен приходит уже строкой и разрешается на выходе);
  * в Xray `dns.servers` ставим `https://1.1.1.1/dns-query` (`domains: [<telegram-домены>]`) или `localhost` для остального; `routing.domainStrategy: IPIfNonMatch`.

## 4. Как это использует код

```python
# aiogram 3.x
from aiogram.client.session.aiohttp import AiohttpSession
session = AiohttpSession(proxy=settings.telegram_proxy)   # http://127.0.0.1:1081
Bot(token=settings.bot_token, session=session)
```

```python
# Scryfall — тоже через туннель; aiohttp понимает http-прокси нативно
card = await scryfall.named_fuzzy("Tarmogoyf")   # внутри: proxy=settings.scryfall_proxy
```

```python
# TOPDeck — напрямую, без прокси
topdeck = TopdeckClient(session, base=settings.topdeck_base)   # api без proxy
```

`proxychains4`/`graftcp` остаются инструментом отладки (например, `proxychains4 curl https://api.telegram.org`), но **не** способом запуска сервиса.

## 5. Проверка живости

```bash
curl -s --socks5-hostname 127.0.0.1:1080 https://api.telegram.org  -o /dev/null -w '%{http_code}\n'   # ждём 200/404
curl -s https://topdeck.ru/apps/toptrade/auctions -o /dev/null -w '%{http_code}\n'                     # ждём 200, напрямую
```

`./deploy/vpn.sh status` печатает результат обеих проверок; `/healthz` бота отдаёт `degraded`, если последний опрос аукционов упал или вернул `0` лотов.

## 6. Запуск

Локально:

```bash
./deploy/vpn.sh up          # конфиг из подписки + SOCKS на 127.0.0.1:1080, проверка api.telegram.org
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env        # BOT_TOKEN, TD_PROXY_SUB_URL, ADMIN_TG_IDS, chmod 600
./topbot.sh                 # запуск на переднем плане (для systemd: ExecStart=…/topbot.sh)
./topbot.sh vpn refresh     # перегенерировать туннель (обёртка над deploy/vpn.sh)
```

`./topbot.sh` — только запуск: проверяет `.venv` и `.env` (или `BOT_TOKEN` в окружении), `cd` в корень репозитория (нужен для `./data` и `.env`) и `exec python -m topbot`. Демонизации, pid-файлов и своего лога нет: процесс — чайлд супервизора, логи идут в stdout/stderr (в systemd — в journal), SIGTERM обрабатывается ботом.

Проверки: `./deploy/vpn.sh status` → `api.telegram.org via 127.0.0.1:1080 -> 200/404`; бот — `/status` (только админ), `/healthz` и `/admin`. Обновление узла: `./deploy/vpn.sh refresh` (подписка могла сменить `sni`/ключи).

### Деплой на сервер: user-юниты systemd

Как развернуть бота на отдельном хосте (шаблоны юнитов — в репозитории, `deploy/systemd/`; пути в них `%h/topdeck-bots/...`, поэтому репозиторий должен лежать в `~/topdeck-bots`, бот — в `~/topdeck-bots/auctions`):

```bash
git clone <repo> ~/topdeck-bots                                  # или rsync рабочей копии
cd ~/topdeck-bots/auctions
./deploy/setup.sh                                                # venv + -e '.[bot,web]' + data/logs + .env из шаблона, идемпотентно
$EDITOR .env                                                     # BOT_TOKEN, админы, WEB_PORT; прокси пустые = напрямую
mkdir -p ~/vendor && cp /path/to/xray ~/vendor/xray && chmod +x ~/vendor/xray   # только если нужен туннель
./deploy/vpn.sh render                                           # только отрендерить deploy/xray.config.json из подписки, без запуска ядра
./deploy/systemd/install.sh --now                                # юниты + linger + запуск; ставит только то, что есть на хосте
```

`install.sh` ставит `topbot.service` всегда, `xray.service` — если находит бинарник Xray (иначе бот ходит напрямую, а зависимость от xray не прописывается), юниты суточного бэкапа — если есть `/data`. Без туннеля в `.env` достаточно `TELEGRAM_PROXY=` и `SCRYFALL_PROXY=` (пустое значение = прямое соединение), `TD_PROXY_SUB_URL` тогда не нужен.

Если в системе нет пакета `python3-venv` (на Ubuntu 24.04+/25.04 `ensurepip` вырезан, а `sudo apt install python3.13-venv` недоступен) — venv собирается без pip, дальше pip бутстрапится официальным скриптом:

```bash
python3 -m venv --without-pip .venv
curl -sS -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py && .venv/bin/python /tmp/get-pip.py
.venv/bin/python -m pip install -e '.[bot,web]'
```

Интерпретатор — Python 3.10 и новее. Верхних границ у зависимостей нет: на 3.10 `pip` берёт последние релизы, где 3.10 ещё поддерживается (проверено: `aiogram` 3.31, `fastapi` 0.162 — `aiogram` 3.32+ и `fastapi` 0.163+ требуют 3.11+), на 3.11+ — самые свежие; тесты зелёные на 3.10 и 3.13.

* `xray.service` — `Type=simple`, `%h/vendor/xray run -c %h/topdeck-bots/auctions/deploy/xray.config.json`, `After=`/`Wants=network-online.target`, `Restart=on-failure`, `RestartSec=5`. Конфиг в git не хранится (`.gitignore`), поэтому на чистом хосте его надо отрендерить заранее (`vpn.sh render` или `vpn.sh refresh`) либо положить готовый файл. Под systemd не запускать ещё и `./deploy/vpn.sh up`: `vpn.sh` демонизирует ядро сам и пишет `data/xray.pid`, из-за чего два процесса подерутся за порты 1080/1081; `vpn.sh status`/`logs` при этом остаются рабочими, но pid-файла нет, поэтому `status` покажет `xray: stopped` (SOCKS-проверку Telegram он всё равно делает).
* `topbot.service` — `Type=simple`, `WorkingDirectory=%h/topdeck-bots/auctions`, `ExecStart=…/topbot.sh` (скрипт делает `exec python -m topbot`), `After=`/`Wants=xray.service`, `Restart=on-failure`, `RestartSec=10`, `KillSignal=SIGTERM`, `TimeoutStopSec=30`. `.env` юнит не читает: его разбирает сам бот (`Settings.from_env`), поэтому токен и прокси живут в одном месте.
* `topbot-backup.timer` — ежедневно в 04:30 (`Persistent=true`), oneshot `topbot-backup.service` вызывает `tools/backup_db.py --db data/topbot.sqlite3 --out /data/backups/topdeck-bots --keep 5`: hot-backup через `sqlite3.Connection.backup` (работает на живой WAL-базе), `PRAGMA integrity_check` на копии, атомарная замена `.part` → `.sqlite3`, права `0600`, имена `topbot-YYYYmmdd-HHMMSS.sqlite3`, хранятся 5 последних (ротация трогает только файлы этого префикса).
* Часовой бэкап — в процессе бота (`HourlyBackup`, `BACKUP_INTERVAL_S`; по умолчанию 3600 с, `0` выключает): снимок сразу при старте и далее по границе интервала, тот же hot-backup кладёт его в `data/topbot.sqlite3.hourly` рядом с основной БД — постоянное имя, `0600`, атомарная подмена `.part` → файл. Имя не меняется, поэтому внешний сервер забирает снимок по scp, не разбирая ротацию. Копировать `data/topbot.sqlite3*` напрямую нельзя: часть данных лежит в `-wal`, снимок из двух файлов несогласован.

Проверки и восстановление:

```bash
systemctl --user status topbot xray topbot-backup.timer
journalctl --user -u topbot -f
curl -sS 127.0.0.1:8085/healthz                  # порт — WEB_PORT из .env
systemctl --user start topbot-backup.service      # прогнать бэкап вручную
ls -la /data/backups/topdeck-bots/
systemctl --user stop topbot.service              # восстановление — только при остановленном боте
rm -f data/topbot.sqlite3-wal data/topbot.sqlite3-shm
cp /data/backups/topdeck-bots/topbot-YYYYmmdd-HHMMSS.sqlite3 data/topbot.sqlite3
systemctl --user start topbot.service
```

Порт `/healthz` берётся из `.env` (`WEB_BIND`/`WEB_PORT`, по умолчанию `127.0.0.1:8080`); если на хосте 8080 уже занят (например, десятком других сервисов), поставьте свободный — на боевом сервере это `WEB_PORT=8085`.

## 7. Секреты и конфигурация

`.env` (в репозитории только `.env.example`):

```
BOT_TOKEN=...
TD_PROXY_SUB_URL=...           # подписка (base64 vless/reality/xhttp); нужна только на хосте с туннелем
TELEGRAM_PROXY=socks5://127.0.0.1:1080    # пусто = напрямую
XRAY_SOCKS_PORT=1080
SCRYFALL_BASE=https://api.scryfall.com
SCRYFALL_ENABLED=1
WEB_BIND=127.0.0.1
WEB_PORT=8080
DB_PATH=./data/topbot.sqlite3
ADMIN_TG_IDS=...
USER_AGENT="topbot/0.1 (+contact)"   # кавычки обязательны: файл читается и как shell-source
POLL_INTERVAL_S=60
PRICES_INTERVAL_S=900
BACKUP_INTERVAL_S=3600         # часовой снимок рядом с БД; 0 — выключить
```

Правила: подписка и токен бота — только в `.env` (600); в логах маскируются; `git grep` перед коммитом; при утечке — перевыпуск токена у @BotFather и смена подписки.

## 8. Переезд на другой хост

1. Код: `git clone <repo> ~/topdeck-bots` (или rsync рабочей копии) — бот лежит в `~/topdeck-bots/auctions`, пути в юнитах (`%h/topdeck-bots/...`) уже рассчитаны на это.
2. Окружение: `cd ~/topdeck-bots/auctions && python3 -m venv .venv && .venv/bin/pip install -e '.[bot,web]'` (Python 3.10+, см. §6).
3. Секреты и данные, которых нет в git: `.env` (§7, `chmod 600`), бинарник Xray в `~/vendor/xray` + конфиг из подписки (`./deploy/vpn.sh render`), текущая БД (на старом хосте взять `data/topbot.sqlite3.hourly` и положить как `data/topbot.sqlite3`; `-wal`/`-shm` не переносить), по желанию — каталог `/data/backups/topdeck-bots`.
4. Автозапуск: `./deploy/systemd/install.sh --now` — ставит `xray.service`, `topbot.service`, `topbot-backup.service` и `topbot-backup.timer`, включает linger и сразу поднимает туннель и бота.
5. Проверка: `systemctl --user is-active topbot xray`, `curl -sS 127.0.0.1:$WEB_PORT/healthz`, через час — `ls -la data/topbot.sqlite3.hourly` (§6).
