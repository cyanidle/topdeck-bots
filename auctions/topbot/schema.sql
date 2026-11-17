PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    username       TEXT,
    first_name     TEXT,
    tz             TEXT NOT NULL DEFAULT 'Europe/Moscow',
    lang           TEXT NOT NULL DEFAULT 'ru',
    windows_json   TEXT NOT NULL DEFAULT '[86400,3600,900,300]',
    new_lot_alerts INTEGER NOT NULL DEFAULT 0,
    muted_until    INTEGER,
    created_at     INTEGER NOT NULL,
    last_seen_at   INTEGER
);

CREATE TABLE IF NOT EXISTS cards (
    id           INTEGER PRIMARY KEY,
    oracle_id    TEXT UNIQUE,
    scryfall_id  TEXT,
    name_en      TEXT NOT NULL,
    name_ru      TEXT,
    name_norm    TEXT NOT NULL UNIQUE,
    set_code     TEXT,
    collector_no TEXT,
    image_url    TEXT,
    scryfall_ts  INTEGER,
    source       TEXT NOT NULL DEFAULT 'scryfall'
);

CREATE TABLE IF NOT EXISTS card_names (
    name      TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    lang      TEXT NOT NULL DEFAULT 'en',
    PRIMARY KEY (name, lang)
);

CREATE INDEX IF NOT EXISTS card_names_norm_idx ON card_names(name_norm);

CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    note            TEXT,
    created_at      INTEGER NOT NULL,
    UNIQUE (user_id, card_id)
);

CREATE TABLE IF NOT EXISTS lots (
    id             INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    title_hash     TEXT NOT NULL,
    matched_hash   TEXT,
    end_ts         INTEGER NOT NULL,
    start_ts       INTEGER NOT NULL,
    start_bid      INTEGER,
    current_bid    INTEGER,
    bid_count      INTEGER,
    bin_value      INTEGER NOT NULL DEFAULT 0,
    seller_id      INTEGER,
    seller_name    TEXT,
    seller_city    TEXT,
    seller_refs    INTEGER,
    shipping_quick TEXT,
    image_url      TEXT,
    composition_ts INTEGER,
    state          TEXT NOT NULL DEFAULT 'active',
    final_price    INTEGER,
    winner_name    TEXT,
    first_seen     INTEGER NOT NULL,
    last_seen      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS lots_end_idx ON lots(end_ts);

CREATE TABLE IF NOT EXISTS lot_items (
    lot_id    INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    PRIMARY KEY (lot_id, name_norm)
);

CREATE TABLE IF NOT EXISTS lot_cards (
    lot_id     INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    card_id    INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    confidence REAL NOT NULL,
    method     TEXT NOT NULL,
    PRIMARY KEY (lot_id, card_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    ref_id       INTEGER NOT NULL,
    window_s     INTEGER,
    state        TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    sent_at      INTEGER,
    payload_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS notifications_uniq
    ON notifications(user_id, kind, ref_id, COALESCE(window_s, 0));

CREATE TABLE IF NOT EXISTS poll_runs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    started_at  INTEGER NOT NULL,
    duration_ms INTEGER,
    ok          INTEGER NOT NULL,
    items       INTEGER,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS card_offers (
    card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    seller_key  TEXT NOT NULL,
    source      TEXT NOT NULL,
    seller_name TEXT,
    cost        INTEGER NOT NULL,
    qty         INTEGER NOT NULL DEFAULT 1,
    url         TEXT,
    line        TEXT NOT NULL DEFAULT '',
    stamp       TEXT,
    fetched_at  INTEGER NOT NULL,
    PRIMARY KEY (card_id, seller_key)
);

CREATE INDEX IF NOT EXISTS card_offers_cost_idx ON card_offers(card_id, cost);

CREATE TABLE IF NOT EXISTS card_price_sync (
    card_id    INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    fetched_at INTEGER NOT NULL,
    offers     INTEGER NOT NULL
);
