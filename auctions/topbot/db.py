from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from typing import Iterable, Sequence

from .matching import MATCH_VERSION, normalize
from .models import Card, Counts, Lot, Offer, PollRun, User, WatchlistCard, WatchlistItem

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

log = logging.getLogger(__name__)


def title_hash(title: str) -> str:
    return hashlib.sha1(title.strip().lower().encode("utf-8")).hexdigest()


def seller_key(offer: Offer) -> str:
    return normalize(offer.seller_name or offer.source or "")


def offer_from_row(row: sqlite3.Row) -> Offer:
    return Offer(eng_name=row["name_en"], rus_name=row["name_ru"], name=row["name_en"],
                 source=row["source"], seller_name=row["seller_name"], seller_id=None, city=None,
                 cost=int(row["cost"]), qty=int(row["qty"]), url=row["url"], line=row["line"] or "",
                 stamp=row["stamp"])


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with open(SCHEMA_PATH, encoding="utf-8") as schema:
            self.conn.executescript(schema.read())
        self._migrate()
        self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', '1')")
        self._sync_match_version()

    def _migrate(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(lots)")}
        if "composition_ts" not in columns:
            self.conn.execute("ALTER TABLE lots ADD COLUMN composition_ts INTEGER")
        watchlist_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(watchlist)")}
        if "price_threshold" in watchlist_columns:
            self.conn.execute("ALTER TABLE watchlist DROP COLUMN price_threshold")
        self.conn.execute("DROP TABLE IF EXISTS offers_snapshot")
        self.conn.execute("DROP TABLE IF EXISTS card_price_stats")

    def _sync_match_version(self) -> None:
        row = self.conn.execute("SELECT value FROM meta WHERE key='match_version'").fetchone()
        if row is not None and int(row["value"]) == MATCH_VERSION:
            return
        invalidated = self.invalidate_lot_matches()
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES('match_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(MATCH_VERSION),),
        )
        if invalidated:
            log.info("match cache invalidated", extra={"lots": invalidated, "match_version": MATCH_VERSION})

    def close(self) -> None:
        self.conn.close()

    def ensure_user(self, user_id: int, username: str | None = None, first_name: str | None = None) -> User:
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO users(id, username, first_name, created_at, last_seen_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=COALESCE(excluded.username, users.username), "
            "first_name=COALESCE(excluded.first_name, users.first_name), last_seen_at=excluded.last_seen_at",
            (user_id, username, first_name, now, now),
        )
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> User:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown user {user_id}")
        return User(
            id=row["id"],
            username=row["username"],
            windows=tuple(json.loads(row["windows_json"])),
            quiet_from=row["quiet_from"],
            quiet_to=row["quiet_to"],
            muted_until=row["muted_until"],
        )

    def upsert_card(self, name_en: str, name_norm: str, *, name_ru: str | None = None,
                    oracle_id: str | None = None, scryfall_id: str | None = None,
                    set_code: str | None = None, collector_no: str | None = None,
                    image_url: str | None = None, source: str = "scryfall") -> int:
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO cards(name_en, name_ru, name_norm, oracle_id, scryfall_id, set_code, collector_no,"
            " image_url, scryfall_ts, source) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name_norm) DO UPDATE SET name_en=excluded.name_en, "
            "name_ru=COALESCE(excluded.name_ru, cards.name_ru), image_url=COALESCE(excluded.image_url, cards.image_url)",
            (name_en, name_ru, name_norm, oracle_id, scryfall_id, set_code, collector_no, image_url, now, source),
        )
        row = self.conn.execute("SELECT id FROM cards WHERE name_norm=?", (name_norm,)).fetchone()
        return int(row["id"])

    def add_to_watchlist(self, user_id: int, card_id: int) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO watchlist(user_id, card_id, created_at) VALUES(?,?,?)",
                (user_id, card_id, int(time.time())),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_from_watchlist(self, user_id: int, card_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM watchlist WHERE user_id=? AND card_id=?", (user_id, card_id))
        return cur.rowcount > 0

    def watchlist(self, user_id: int) -> list[WatchlistItem]:
        rows = self.conn.execute(
            "SELECT w.id, c.id AS card_id, c.name_en, c.name_ru, c.image_url "
            "FROM watchlist w JOIN cards c ON c.id = w.card_id WHERE w.user_id=? ORDER BY c.name_en",
            (user_id,),
        ).fetchall()
        return [
            WatchlistItem(
                id=int(row["id"]),
                card_id=int(row["card_id"]),
                name_en=row["name_en"],
                name_ru=row["name_ru"],
                image_url=row["image_url"],
            )
            for row in rows
        ]

    def update_windows(self, user_id: int, windows: tuple[int, ...]) -> None:
        self.conn.execute("UPDATE users SET windows_json=? WHERE id=?",
                          (json.dumps(sorted(windows, reverse=True)), user_id))

    def update_quiet(self, user_id: int, quiet_from: int, quiet_to: int) -> None:
        self.conn.execute("UPDATE users SET quiet_from=?, quiet_to=? WHERE id=?",
                          (quiet_from, quiet_to, user_id))

    def update_mute(self, user_id: int, muted_until: int | None) -> None:
        self.conn.execute("UPDATE users SET muted_until=? WHERE id=?", (muted_until, user_id))

    def user_id_by_username(self, username: str) -> int | None:
        row = self.conn.execute("SELECT id FROM users WHERE lower(username)=?", (username.lstrip("@").lower(),)
                                ).fetchone()
        return None if row is None else int(row["id"])

    def watchlist_overview(self, user_id: int) -> list[WatchlistCard]:
        now = int(time.time())
        rows = self.conn.execute(
            """
            SELECT c.id AS card_id, c.name_en, c.name_ru,
                   (SELECT COUNT(*) FROM lot_cards lc JOIN lots l ON l.id = lc.lot_id
                     WHERE lc.card_id = c.id AND l.state = 'active' AND l.end_ts > :now) AS lots_count,
                   (SELECT MIN(MAX(COALESCE(l.current_bid, 0), COALESCE(l.start_bid, 0)))
                      FROM lot_cards lc JOIN lots l ON l.id = lc.lot_id
                     WHERE lc.card_id = c.id AND l.state = 'active' AND l.end_ts > :now) AS lots_min_price,
                   (SELECT MIN(l.end_ts) FROM lot_cards lc JOIN lots l ON l.id = lc.lot_id
                     WHERE lc.card_id = c.id AND l.state = 'active' AND l.end_ts > :now) AS next_end_ts,
                   (SELECT l.id FROM lot_cards lc JOIN lots l ON l.id = lc.lot_id
                     WHERE lc.card_id = c.id AND l.state = 'active' AND l.end_ts > :now
                     ORDER BY l.end_ts LIMIT 1) AS next_lot_id
              FROM watchlist w JOIN cards c ON c.id = w.card_id
             WHERE w.user_id = :user_id
             ORDER BY c.name_en
            """,
            {"now": now, "user_id": user_id},
        ).fetchall()
        return [
            WatchlistCard(
                card_id=int(row["card_id"]),
                name_en=row["name_en"],
                name_ru=row["name_ru"],
                lots_count=int(row["lots_count"] or 0),
                lots_min_price=row["lots_min_price"],
                next_end_ts=row["next_end_ts"],
                next_lot_id=row["next_lot_id"],
            )
            for row in rows
        ]

    def counts(self) -> Counts:
        return Counts(
            users=self._count("SELECT COUNT(*) AS n FROM users"),
            cards=self._count("SELECT COUNT(*) AS n FROM cards"),
            lots_active=self._count("SELECT COUNT(*) AS n FROM lots WHERE state='active'"),
            notifications_sent=self._count("SELECT COUNT(*) AS n FROM notifications WHERE state='sent'"),
            card_names=self._count("SELECT COUNT(*) AS n FROM card_names"),
        )

    def _count(self, query: str) -> int:
        return int(self.conn.execute(query).fetchone()["n"])

    def cards_index(self) -> list[Card]:
        return [
            Card(id=int(row["id"]), name_en=row["name_en"], name_ru=row["name_ru"])
            for row in self.conn.execute("SELECT id, name_en, name_ru FROM cards")
        ]

    def subscribers_by_card(self) -> dict[int, list[int]]:
        result: dict[int, list[int]] = {}
        for row in self.conn.execute("SELECT card_id, user_id FROM watchlist"):
            result.setdefault(int(row["card_id"]), []).append(int(row["user_id"]))
        return result

    def lot(self, lot_id: int) -> Lot | None:
        row = self.conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
        if row is None:
            return None
        return Lot(
            id=int(row["id"]),
            title=row["title"],
            end_ts=int(row["end_ts"]),
            start_ts=int(row["start_ts"]),
            start_bid=int(row["start_bid"] or 0),
            current_bid=int(row["current_bid"] or 0),
            bid_count=int(row["bid_count"] or 0),
            bin_value=int(row["bin_value"] or 0),
            seller_id=row["seller_id"],
            seller_name=row["seller_name"],
            seller_city=row["seller_city"],
            seller_refs=row["seller_refs"],
            shipping_quick=row["shipping_quick"],
            image_url=row["image_url"],
            url=f"https://topdeck.ru/apps/toptrade/auctions/{row['id']}",
        )

    def upsert_lot(self, lot: Lot, now: int) -> None:
        self.conn.execute(
            "INSERT INTO lots(id, title, title_hash, end_ts, start_ts, start_bid, current_bid, bid_count,"
            " bin_value, seller_id, seller_name, seller_city, seller_refs, shipping_quick, image_url,"
            " state, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET end_ts=excluded.end_ts, title=excluded.title,"
            " title_hash=excluded.title_hash, current_bid=excluded.current_bid, bid_count=excluded.bid_count,"
            " bin_value=excluded.bin_value, state='active', last_seen=excluded.last_seen",
            (
                int(lot.id), lot.title, title_hash(lot.title), int(lot.end_ts), int(lot.start_ts),
                lot.start_bid, lot.current_bid, lot.bid_count, lot.bin_value,
                lot.seller_id, lot.seller_name, lot.seller_city, lot.seller_refs,
                lot.shipping_quick, lot.image_url, now, now,
            ),
        )

    def mark_vanished(self, seen_ids: Sequence[int], now: int) -> int:
        if not seen_ids:
            return 0
        placeholders = ",".join("?" for _ in seen_ids)
        cur = self.conn.execute(
            f"UPDATE lots SET state='finished' WHERE state='active' AND end_ts<=? AND id NOT IN ({placeholders})",
            (now, *seen_ids),
        )
        return cur.rowcount

    def invalidate_lot_matches(self) -> int:
        cur = self.conn.execute("UPDATE lots SET matched_hash=NULL WHERE matched_hash IS NOT NULL")
        return cur.rowcount

    def lots_needing_composition(self, limit: int, now: int) -> list[tuple[int, str]]:
        rows = self.conn.execute(
            "SELECT id, title FROM lots WHERE state='active' AND end_ts > ? AND composition_ts IS NULL"
            " ORDER BY end_ts LIMIT ?",
            (now, limit),
        ).fetchall()
        return [(int(row["id"]), row["title"]) for row in rows]

    def lots_with_matches(self) -> set[int]:
        return {int(row["lot_id"]) for row in self.conn.execute("SELECT DISTINCT lot_id FROM lot_cards")}

    def save_composition(self, lot_id: int, names: Sequence[str], now: int) -> None:
        self.conn.execute("DELETE FROM lot_items WHERE lot_id=?", (lot_id,))
        self.conn.executemany(
            "INSERT OR REPLACE INTO lot_items(lot_id, name, name_norm) VALUES(?,?,?)",
            [(lot_id, name, normalize(name)) for name in names if name.strip()],
        )
        self.conn.execute("UPDATE lots SET composition_ts=?, matched_hash=NULL WHERE id=?", (now, lot_id))

    def compositions(self) -> dict[int, tuple[str, ...]]:
        result: dict[int, list[str]] = {}
        for row in self.conn.execute("SELECT lot_id, name FROM lot_items ORDER BY name"):
            result.setdefault(int(row["lot_id"]), []).append(row["name"])
        return {lot_id: tuple(names) for lot_id, names in result.items()}

    def lot_card_ids(self, lot_id: int) -> list[int]:
        rows = self.conn.execute("SELECT card_id FROM lot_cards WHERE lot_id=? ORDER BY card_id", (lot_id,)).fetchall()
        return [int(row["card_id"]) for row in rows]

    def lot_card_methods(self, lot_id: int) -> dict[int, str]:
        rows = self.conn.execute("SELECT card_id, method FROM lot_cards WHERE lot_id=?", (lot_id,)).fetchall()
        return {int(row["card_id"]): row["method"] for row in rows}

    def save_lot_cards(self, lot_id: int, matches: Iterable[tuple[int, float, str]], title_hash_value: str) -> None:
        self.conn.execute("DELETE FROM lot_cards WHERE lot_id=?", (lot_id,))
        self.conn.executemany(
            "INSERT OR REPLACE INTO lot_cards(lot_id, card_id, confidence, method) VALUES(?,?,?,?)",
            [(lot_id, card_id, confidence, method) for card_id, confidence, method in matches],
        )
        self.conn.execute("UPDATE lots SET matched_hash=? WHERE id=?", (title_hash_value, lot_id))

    def already_notified(self, user_id: int, kind: str, ref_id: int, window_s: int | None) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM notifications WHERE user_id=? AND kind=? AND ref_id=? AND COALESCE(window_s,0)=?",
            (user_id, kind, ref_id, window_s or 0),
        ).fetchone()
        return row is not None

    def sent_windows(self, user_id: int, kind: str, ref_id: int) -> set[int]:
        rows = self.conn.execute(
            "SELECT window_s FROM notifications WHERE user_id=? AND kind=? AND ref_id=?"
            " AND window_s IS NOT NULL AND state='sent'",
            (user_id, kind, ref_id),
        ).fetchall()
        return {int(row["window_s"]) for row in rows}

    def reserve_notification(self, user_id: int, kind: str, ref_id: int, window_s: int | None,
                             payload: dict | None = None) -> int | None:
        existing = self.conn.execute(
            "SELECT id, state FROM notifications WHERE user_id=? AND kind=? AND ref_id=?"
            " AND COALESCE(window_s,0)=?",
            (user_id, kind, ref_id, window_s or 0),
        ).fetchone()
        if existing is not None:
            if existing["state"] == "sent":
                return None
            self.conn.execute("UPDATE notifications SET state='pending' WHERE id=?", (existing["id"],))
            return int(existing["id"])
        cur = self.conn.execute(
            "INSERT INTO notifications(user_id, kind, ref_id, window_s, state, created_at, payload_json)"
            " VALUES(?,?,?,?, 'pending', ?, ?)",
            (user_id, kind, ref_id, window_s, int(time.time()), json.dumps(payload or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def mark_sent(self, notification_id: int, state: str = "sent") -> None:
        self.conn.execute("UPDATE notifications SET state=?, sent_at=? WHERE id=?",
                          (state, int(time.time()), notification_id))

    def count_sent_last_hour(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND state='sent' AND sent_at>=?",
            (user_id, int(time.time()) - 3600),
        ).fetchone()
        return int(row["n"])

    def record_poll(self, kind: str, started_at: int, duration_ms: int, ok: bool,
                    items: int | None = None, error: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO poll_runs(kind, started_at, duration_ms, ok, items, error) VALUES(?,?,?,?,?,?)",
            (kind, started_at, duration_ms, int(ok), items, error),
        )

    def last_poll(self, kind: str) -> PollRun | None:
        row = self.conn.execute(
            "SELECT * FROM poll_runs WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
        if row is None:
            return None
        return PollRun(
            id=int(row["id"]),
            kind=row["kind"],
            started_at=int(row["started_at"]),
            duration_ms=row["duration_ms"],
            ok=bool(row["ok"]),
            items=row["items"],
            error=row["error"],
        )

    def watched_lot_ids(self, now: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT DISTINCT l.id FROM lots l"
            " JOIN lot_cards lc ON lc.lot_id = l.id"
            " JOIN watchlist w ON w.card_id = lc.card_id"
            " WHERE l.state='active' AND l.end_ts > ?"
            " ORDER BY l.end_ts",
            (now,),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def planned_windows(self, now: int) -> list[tuple[int, int, int]]:
        """(fire_at, lot_id, window_s) для ещё не отправленных окон — по возрастанию fire_at."""
        rows = self.conn.execute(
            "SELECT u.id AS user_id, u.windows_json, l.id AS lot_id, l.end_ts FROM lots l"
            " JOIN lot_cards lc ON lc.lot_id = l.id"
            " JOIN watchlist w ON w.card_id = lc.card_id"
            " JOIN users u ON u.id = w.user_id"
            " WHERE l.state='active' AND l.end_ts > ?",
            (now,),
        ).fetchall()
        sent: set[tuple[int, int, int]] = set()
        lot_ids = sorted({int(row["lot_id"]) for row in rows})
        if lot_ids:
            placeholders = ",".join("?" for _ in lot_ids)
            sent = {(int(row["user_id"]), int(row["ref_id"]), int(row["window_s"]))
                    for row in self.conn.execute(
                        "SELECT user_id, ref_id, window_s FROM notifications"
                        " WHERE kind='auction_window' AND state='sent' AND window_s IS NOT NULL"
                        f" AND ref_id IN ({placeholders})", tuple(lot_ids))}
        plan: list[tuple[int, int, int]] = []
        for row in rows:
            lot_id = int(row["lot_id"])
            end_ts = int(row["end_ts"])
            for window in json.loads(row["windows_json"]):
                window = int(window)
                if (int(row["user_id"]), lot_id, window) in sent:
                    continue
                fire_at = end_ts - window
                if fire_at < now - window // 2:
                    continue
                plan.append((fire_at, lot_id, window))
        return sorted(plan)

    def watched_cards(self) -> list[Card]:
        rows = self.conn.execute(
            "SELECT DISTINCT c.id, c.name_en, c.name_ru FROM cards c JOIN watchlist w ON w.card_id = c.id"
            " ORDER BY c.name_en"
        ).fetchall()
        return [Card(id=int(row["id"]), name_en=row["name_en"], name_ru=row["name_ru"]) for row in rows]

    def find_card(self, query: str) -> Card | None:
        norm = normalize(query)
        if not norm:
            return None
        for sql in ("SELECT id, name_en, name_ru FROM cards WHERE name_norm=?",
                    "SELECT id, name_en, name_ru FROM cards WHERE name_norm LIKE ? ORDER BY length(name_norm)"):
            row = self.conn.execute(sql, (norm,) if "=?" in sql else (norm + "%",)).fetchone()
            if row is not None:
                return Card(id=int(row["id"]), name_en=row["name_en"], name_ru=row["name_ru"])
        return None

    def save_card_offers(self, card_id: int, offers: Sequence[Offer], now: int) -> int:
        best: dict[str, Offer] = {}
        for offer in offers:
            if offer.cost <= 0:
                continue
            key = seller_key(offer)
            current = best.get(key)
            if current is None or offer.cost < current.cost:
                best[key] = offer
        self.conn.execute("DELETE FROM card_offers WHERE card_id=?", (card_id,))
        self.conn.executemany(
            "INSERT OR REPLACE INTO card_offers(card_id, seller_key, source, seller_name, cost, qty, url, line,"
            " stamp, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(card_id, key, offer.source, offer.seller_name, offer.cost, offer.qty, offer.url, offer.line,
              offer.stamp, now) for key, offer in best.items()],
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO card_price_sync(card_id, fetched_at, offers) VALUES(?,?,?)",
            (card_id, now, len(best)),
        )
        return len(best)

    def card_offers(self, card_id: int) -> list[Offer]:
        return [offer_from_row(row) for row in self.conn.execute(
            "SELECT o.*, c.name_en, c.name_ru FROM card_offers o JOIN cards c ON c.id = o.card_id"
            " WHERE o.card_id=? ORDER BY o.cost", (card_id,))]

    def top_offers_by_card(self, card_ids: Sequence[int], limit: int) -> dict[int, list[Offer]]:
        if not card_ids:
            return {}
        placeholders = ",".join("?" for _ in card_ids)
        rows = self.conn.execute(
            f"SELECT card_id, seller_name, source, cost, qty, url, line, stamp, name_en, name_ru FROM ("
            f"  SELECT o.*, c.name_en, c.name_ru,"
            f"         ROW_NUMBER() OVER (PARTITION BY o.card_id ORDER BY o.cost) AS rn"
            f"  FROM card_offers o JOIN cards c ON c.id = o.card_id"
            f"  WHERE o.card_id IN ({placeholders})"
            f") WHERE rn <= ? ORDER BY card_id, cost",
            (*card_ids, limit),
        ).fetchall()
        result: dict[int, list[Offer]] = {}
        for row in rows:
            result.setdefault(int(row["card_id"]), []).append(offer_from_row(row))
        return result

    def cards_without_prices(self, card_ids: Sequence[int]) -> list[int]:
        if not card_ids:
            return []
        placeholders = ",".join("?" for _ in card_ids)
        synced = {int(row["card_id"]) for row in self.conn.execute(
            f"SELECT card_id FROM card_price_sync WHERE card_id IN ({placeholders})", tuple(card_ids))}
        return [card_id for card_id in card_ids if card_id not in synced]

    def price_synced(self, card_id: int) -> bool:
        row = self.conn.execute("SELECT 1 FROM card_price_sync WHERE card_id=?", (card_id,)).fetchone()
        return row is not None

    def prices_synced_at(self, card_ids: Sequence[int]) -> int | None:
        if not card_ids:
            return None
        placeholders = ",".join("?" for _ in card_ids)
        row = self.conn.execute(
            f"SELECT MIN(fetched_at) AS ts FROM card_price_sync WHERE card_id IN ({placeholders})",
            tuple(card_ids),
        ).fetchone()
        return None if row is None or row["ts"] is None else int(row["ts"])

    def prune_card_offers(self, card_ids: Sequence[int]) -> None:
        if not card_ids:
            self.conn.execute("DELETE FROM card_offers")
            self.conn.execute("DELETE FROM card_price_sync")
            return
        placeholders = ",".join("?" for _ in card_ids)
        self.conn.execute(f"DELETE FROM card_offers WHERE card_id NOT IN ({placeholders})", tuple(card_ids))
        self.conn.execute(f"DELETE FROM card_price_sync WHERE card_id NOT IN ({placeholders})", tuple(card_ids))
