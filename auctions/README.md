# topbot — слежение за картами MTG на TOPDeck.ru через Telegram

Telegram-бот, который по персональному watchlist'у пользователя присылает уведомления об аукционах TOPDeck.ru, заканчивающихся через **1 день / 1 час / 15 минут / 5 минут**. По запросу показывает минимальные предложения продавцов и магазинов по карте — чтобы сравнить с текущей ставкой.

Один фоновый поллер обслуживает всех пользователей сразу.

## Документация

| Документ | О чём |
|---|---|
| [`docs/research-topdeck-api.md`](docs/research-topdeck-api.md) | **Исследование API TOPDeck.ru**: все проверенные эндпоинты, схемы встроенного JSON, лимиты, подводные камни (2026-11-15) |
| [`docs/spec-product.md`](docs/spec-product.md) | Продукт: сущности, карточки/Scryfall, матчинг «лот ↔ карта», команды бота, тексты уведомлений, границы скоупа |
| [`docs/spec-poller.md`](docs/spec-poller.md) | Архитектура поллера: минутный цикл, математика окон, дедупликация, отказоустойчивость, бюджеты запросов |
| [`docs/spec-data-model.md`](docs/spec-data-model.md) | Схема SQLite (пользователи, карты, каталог изданий, watchlist, лоты, состав лотов, уведомления, игноры лотов) |
| [`docs/spec-infra.md`](docs/spec-infra.md) | Xray-core, подписка vless+reality+xhttp, прокси **внутри процесса** для Telegram (только telegram-трафик, остальное — direct), секреты, запуск |
| [`docs/plan.md`](docs/plan.md) | Этапы 0–2 (сделаны), открытые вопросы и риски |

## Быстрый старт бота

```bash
./deploy/setup.sh                                     # один раз: venv, зависимости, data/, .env из шаблона
$EDITOR .env                                          # BOT_TOKEN; прокси — или пусто (напрямую), или туннель
./topbot.sh                                           # запуск на переднем плане
```

`./deploy/setup.sh` можно запускать повторно: он идемпотентен (создаёт `.venv`, ставит `-e '.[bot,web]'`, каталоги, `.env` при отсутствии) и печатает итоговый конфиг — в том числе режим `telegram: direct`.
./topbot.sh vpn refresh                               # если туннель до Telegram отвалился
```

`./topbot.sh` — тонкая обёртка перед `python -m topbot`: проверяет `.venv` и `.env` (или `BOT_TOKEN` в окружении), переходит в корень репозитория и делает `exec`. Никакой демонизации, pid-файлов и своего лога: процесс живёт как чайлд супервизора (`ExecStart=/opt/topbot/topbot.sh` в systemd), логи идут в stdout/stderr → journal, SIGTERM доходит до бота.

## Быстрый старт VPN-ядра (Telegram egress)

```bash
./deploy/vpn.sh up      # отрендерить конфиг из подписки, поднять SOCKS 127.0.0.1:1080, проверить Telegram
./deploy/vpn.sh status  # бинарник, pid, порт, код ответа api.telegram.org через прокси
./deploy/vpn.sh refresh # перечитать подписку и перезапустить
```

Требуется установленный `xray` (положить в `./vendor/xray`, `/usr/local/bin/xray` или указать `XRAY_BIN`).

В туннель уходит **только Telegram** (явные домены + telegram CIDR); `topdeck.ru`, Scryfall и прочее — напрямую.

## Референсный клиент

`tools/topdeck_probe.py` — минимальный клиент публичных эндпоинтов (без зависимостей, только stdlib; намеренно не использует `topbot.models` и работает с сырыми dict, чтобы запускаться без установки пакета):

```bash
python3 tools/topdeck_probe.py search "Snapcaster Mage" "Lightning Bolt"   # предложения и минимальные цены
python3 tools/topdeck_probe.py auctions --hours 24 --limit 10               # активные лоты, ближайшие к концу
```

## Состав репозитория

```
docs/       спецификации (исследование API, продукт, поллер, БД, инфраструктура, план)
deploy/     установка и прокси: setup.sh (venv+зависимости), pull_backup.sh (снимок БД с боевого хоста), systemd/ (юниты, install.sh, install-pull.sh), vpn.sh + xray_from_sub.py
tools/      topdeck_probe.py — референсный клиент публичных эндпоинтов TOPDeck
topbot/     бот и поллеры: models.py (dataclass-DTO), clients/, db.py, poller.py, prices.py, bot.py, webui.py
tests/      unittest'ы (парсинг, матчинг, окна, поллеры, кэш цен, бот-флоу, клиенты, логи)
topbot.sh   запуск бота (exec python -m topbot, для супервизора) + прокси в deploy/vpn.sh
.env        токен бота + подписка (chmod 600, в git не попадает; шаблон — .env.example)
```

## Стек

Python 3.10+ · aiogram 3.x · SQLite (WAL) · Xray-core (egress в Telegram, встроен в deploy/) · Scryfall API (нормализация карт, отключаемая).

Зависимости не запинены сверху: на Python 3.10 `pip` сам берёт последние релизы, где 3.10 ещё поддерживается, на 3.11+ — самые свежие. Сам код от версии интерпретатора не зависит (тесты зелёные на 3.10 и 3.13).

## Статус

* Этап 0 (исследование) — выполнен: `docs/research-topdeck-api.md`, `tools/topdeck_probe.py` проверен на живом сайте.
* Этап 1 (бот + watchlist) — выполнен: `/start /menu /add /list /remove /price /settings /mute /help /admin` (плюс админский `/status`), локальный каталог Scryfall-имён с автокомплитом и дизамбигуацией.
* Этап 2 (поллер аукционов + уведомления) — выполнен: окна 1д/1ч/15м/5м, дедуп, `/mute`, матчинг лотов по названию (точное/префикс/нечёткое) и по составу, алерты админу о серии сбоев.
* Точные окна — выполнен: `WindowScheduler` просыпается ровно в момент `end_ts - окно` (минута в минуту), поллер (60 с, один запрос на цикл) только собирает лоты и матчи.
* Кэш цен — выполнен: `PricePoller` раз в 15 минут обновляет `card_offers` по объединению карт из всех watchlist'ов, `/list` и `/price` читают кэш (ответ за миллисекунды вместо 6–7 секунд).
* Часовой бэкап — выполнен: бот сразу при старте и далее по границе часа атомарно кладёт горячий снимок БД в `data/topbot.sqlite3.hourly` (постоянное имя — чтобы внешний сервер забирал его по scp), период — `BACKUP_INTERVAL_S`, `0` выключает; суточная ротация в `/data/backups/topdeck-bots` осталась на `topbot-backup.timer`.
* Тесты: 356 unittest (`.venv/bin/python -m unittest discover -s tests -t .`).
* Вне скоупа (решение 2026-11-16): суточный `OfferPoller` со снимками цен и `price_drop`, история завершённых лотов, полноценный веб-UI, деплой на сервер. Цены доступны из кэша — `/price` и ссылка «цены» в `/list`.
