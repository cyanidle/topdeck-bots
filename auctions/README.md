# topbot — слежение за картами MTG на TOPDeck.ru через Telegram

Telegram-бот, который по персональному watchlist'у пользователя присылает уведомления об аукционах TOPDeck.ru, заканчивающихся через **1 день / 3 часа / 1 час / 15 минут**. По запросу показывает минимальные предложения продавцов и магазинов по карте — чтобы сравнить с текущей ставкой.

Один фоновый поллер обслуживает всех пользователей сразу.

## Документация

| Документ | О чём |
|---|---|
| [`docs/research-topdeck-api.md`](docs/research-topdeck-api.md) | **Исследование API TOPDeck.ru**: все проверенные эндпоинты, схемы встроенного JSON, лимиты, подводные камни (2026-11-15) |
| [`docs/spec-product.md`](docs/spec-product.md) | Продукт: сущности, карточки/Scryfall, матчинг «лот ↔ карта», команды бота, тексты уведомлений, границы скоупа |
| [`docs/spec-poller.md`](docs/spec-poller.md) | Архитектура поллера: 14 минут, математика окон, дедупликация, отказоустойчивость, бюджеты запросов |
| [`docs/spec-data-model.md`](docs/spec-data-model.md) | Схема SQLite (пользователи, карты, watchlist, лоты, состав лотов, уведомления) |
| [`docs/spec-infra.md`](docs/spec-infra.md) | Xray-core, подписка vless+reality+xhttp, прокси **внутри процесса** для Telegram (только telegram-трафик, остальное — direct), секреты, запуск |
| [`docs/plan.md`](docs/plan.md) | Этапы 0–2 (сделаны), открытые вопросы и риски |

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
deploy/     VPN-ядро: xray_from_sub.py (конфиг из подписки), vpn.sh
tools/      topdeck_probe.py — референсный клиент публичных эндпоинтов TOPDeck
topbot/     бот и поллер: models.py (dataclass-DTO), clients/, db.py, poller.py, bot.py, webui.py
tests/      unittest'ы (парсинг, матчинг, окна, поллер, бот-флоу, клиенты, логи)
.env        токен бота + подписка (chmod 600, в git не попадает; шаблон — .env.example)
```

## Стек

Python 3.12+ · aiogram 3.x · SQLite (WAL) · Xray-core (egress в Telegram, встроен в deploy/) · Scryfall API (нормализация карт, отключаемая).

## Статус

* Этап 0 (исследование) — выполнен: `docs/research-topdeck-api.md`, `tools/topdeck_probe.py` проверен на живом сайте.
* Этап 1 (бот + watchlist) — выполнен: `/start /menu /add /list /remove /price /status /settings /mute /help /admin`, локальный каталог Scryfall-имён с автокомплитом и дизамбигуацией.
* Этап 2 (поллер аукционов + уведомления) — выполнен: окна 1д/3ч/1ч/15м, дедуп, тихие часы, `/mute`, матчинг лотов по названию (точное/префикс/нечёткое) и по составу, алерты админу о серии сбоев.
* Тесты: 174 unittest (`.venv/bin/python -m unittest discover -s tests -t .`).
* Вне скоупа (решение 2026-11-16): суточный `OfferPoller` со снимками цен и `price_drop`, история завершённых лотов, полноценный веб-UI, деплой на сервер. Цены доступны по запросу — `/price` и кнопка «💰 Цена у продавцов» в `/list`.
