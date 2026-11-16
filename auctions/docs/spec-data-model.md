# Модель данных (SQLite, WAL)

Локальная разработка — SQLite; на сервере допустим тот же файл (один процесс) либо Postgres (схема совместима, `JSONB` вместо `TEXT`).

```sql
-- 1. Пользователи и настройки ------------------------------------------------
CREATE TABLE users (
    id              INTEGER PRIMARY KEY,            -- telegram user id
    username        TEXT,
    first_name      TEXT,
    tz              TEXT NOT NULL DEFAULT 'Europe/Moscow',
    lang            TEXT NOT NULL DEFAULT 'ru',
    windows_json    TEXT NOT NULL DEFAULT '[86400,10800,3600,900]',
    quiet_from      INTEGER NOT NULL DEFAULT 1380,  -- 23:00 в минутах от полуночи
    quiet_to        INTEGER NOT NULL DEFAULT 540,   -- 09:00
    new_lot_alerts  INTEGER NOT NULL DEFAULT 0,
    muted_until     INTEGER,                        -- epoch
    created_at      INTEGER NOT NULL,
    last_seen_at    INTEGER
);

-- 2. Карточки карт (canonical — Scryfall) ------------------------------------
CREATE TABLE cards (
    id              INTEGER PRIMARY KEY,
    oracle_id       TEXT UNIQUE,                    -- идентичность карты
    scryfall_id     TEXT,
    name_en         TEXT NOT NULL,
    name_ru         TEXT,
    name_norm       TEXT NOT NULL UNIQUE,           -- lower, ё→е, без пунктуации
    set_code        TEXT,
    collector_no    TEXT,
    image_url       TEXT,
    scryfall_ts     INTEGER,                        -- когда обновляли метаданные
    source          TEXT NOT NULL DEFAULT 'scryfall' -- scryfall | topdeck | manual
);
CREATE INDEX cards_norm_idx ON cards(name_norm);

-- 3. Watchlist ---------------------------------------------------------------
CREATE TABLE watchlist (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_id         INTEGER NOT NULL REFERENCES cards(id),
    note            TEXT,
    created_at      INTEGER NOT NULL,
    UNIQUE (user_id, card_id)
);

-- 4. Лоты аукционов ---------------------------------------------------------
CREATE TABLE lots (
    id              INTEGER PRIMARY KEY,            -- topdeck lot id
    title           TEXT NOT NULL,
    title_hash      TEXT NOT NULL,                  -- для инвалидации матчинга
    end_ts          INTEGER NOT NULL,               -- date_estimated (epoch UTC)
    start_ts        INTEGER NOT NULL,               -- date_published
    start_bid       INTEGER,
    current_bid     INTEGER,
    bid_count       INTEGER,
    bin_value       INTEGER NOT NULL DEFAULT 0,
    seller_id       INTEGER,
    seller_name     TEXT,
    seller_city     TEXT,
    seller_refs     INTEGER,
    shipping_quick  TEXT,
    image_url       TEXT,
    composition_ts  INTEGER,                        -- когда скачали состав лота (NULL = ещё нет)
    state           TEXT NOT NULL DEFAULT 'active', -- active | finished | vanished
    final_price     INTEGER,
    winner_name     TEXT,
    first_seen      INTEGER NOT NULL,
    last_seen       INTEGER NOT NULL
);
CREATE INDEX lots_end_idx ON lots(end_ts) WHERE state = 'active';

CREATE TABLE lot_items (                             -- состав лота со страницы аукциона
    lot_id    INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,                         -- имя как написал продавец
    name_norm TEXT NOT NULL,
    PRIMARY KEY (lot_id, name_norm)
);

CREATE TABLE lot_cards (                             -- результат матчинга, кэш
    lot_id          INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    card_id         INTEGER NOT NULL REFERENCES cards(id),
    confidence      REAL NOT NULL,
    method          TEXT NOT NULL,                  -- exact_en | exact_ru | prefix_en | prefix_ru | fuzzy | composition:* | manual
    PRIMARY KEY (lot_id, card_id)
);

-- 5. Уведомления -------------------------------------------------------------
CREATE TABLE notifications (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                  -- auction_window
    ref_id          INTEGER NOT NULL,               -- lot id | card id
    window_s        INTEGER,                        -- 86400|10800|3600|900|NULL
    state           TEXT NOT NULL,                  -- pending | sent | skipped | failed
    created_at      INTEGER NOT NULL,
    sent_at         INTEGER,
    payload_json    TEXT,
    UNIQUE (user_id, kind, ref_id, window_s)
);

-- 7. Наблюдаемость и служебное ----------------------------------------------
CREATE TABLE poll_runs (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,                  -- auctions | offers
    started_at      INTEGER NOT NULL,
    duration_ms     INTEGER,
    ok              INTEGER NOT NULL,
    items           INTEGER,
    error           TEXT
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- schema_version, match_version, last_offer_poll, ...
```

## Замечания

* `window_s = NULL` в `notifications` — для событий без окна; в UNIQUE это допускается (SQLite считает NULL-комбинации различными), поэтому для таких событий ключ дедупликации складываем в `payload_json.dedupe_key` и проверяем отдельно, либо используем `COALESCE(window_s, 0)` в индексе:
  `CREATE UNIQUE INDEX notifications_uniq ON notifications(user_id, kind, ref_id, COALESCE(window_s, 0));`
* `title_hash` = sha1 от `title`: матчинг пересчитывается только при изменении заголовка.
* `meta.match_version` хранит версию правил матчинга (`MATCH_VERSION` в `topbot/matching.py`): при старте `Database` сверяет её и при расхождении сбрасывает `lots.matched_hash` — иначе уже лежащие лоты остались бы со старыми привязками после изменения эвристик. Меняя правила матчинга, поднимай версию.
* `lots` держим 180 дней (статистика и история уведомлений), дальше — архив.
* Персональные данные: храним только `tg_id`, `username`, `first_name`; никаких телефонов/адресов/переписок.
