from __future__ import annotations

import logging
import re
import sqlite3
import time

from topbot.db import Database as TopbotDatabase
from topbot.models import Card

from .models import CollectionEntry

COLLECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_cards(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    set_code TEXT NOT NULL,
    number TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, set_code, number)
);
CREATE INDEX IF NOT EXISTS collection_cards_user ON collection_cards(user_id);
CREATE TABLE IF NOT EXISTS prints_cache(
    set_code TEXT NOT NULL,
    number TEXT NOT NULL,
    name_en TEXT,
    name_ru TEXT,
    scryfall_id TEXT,
    image_url TEXT,
    resolved_at INTEGER NOT NULL,
    PRIMARY KEY(set_code, number)
);
"""

NEGATIVE_TTL_S = 7 * 86400
IDENTITY_SPLIT = re.compile(r"[:\s]+")
CODE_RE = re.compile(r"[A-Z0-9]{2,6}")
NUMBER_RE = re.compile(r"\d{1,4}[a-zA-Z★]?")

log = logging.getLogger(__name__)


def normalize_number(number: str) -> str:
    text = number.strip()
    head = text.partition("/")[0].strip()
    digits = head.lstrip("0")
    return digits or text


def natural_number_key(number: str) -> tuple[int, str]:
    index = 0
    while index < len(number) and number[index].isdigit():
        index += 1
    return int(number[:index] or 0), number[index:]


def normalize_identity(text: str) -> tuple[str, str] | None:
    parts = IDENTITY_SPLIT.split(text.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    code = parts[0].strip().upper()
    number = normalize_number(parts[1])
    if not CODE_RE.fullmatch(code) or not NUMBER_RE.fullmatch(number):
        return None
    return code, number


class CollectDatabase(TopbotDatabase):
    def __init__(self, path: str):
        super().__init__(path)
        self.conn.executescript(COLLECTION_SCHEMA)

    @staticmethod
    def _entry(row: sqlite3.Row) -> CollectionEntry:
        return CollectionEntry(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            set_code=row["set_code"],
            number=row["number"],
            count=int(row["count"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def get_card(self, user_id: int, set_code: str, number: str) -> CollectionEntry | None:
        row = self.conn.execute(
            "SELECT * FROM collection_cards WHERE user_id=? AND set_code=? AND number=?",
            (user_id, set_code, number),
        ).fetchone()
        return self._entry(row) if row else None

    def entry_by_id(self, user_id: int, entry_id: int) -> CollectionEntry | None:
        row = self.conn.execute(
            "SELECT * FROM collection_cards WHERE user_id=? AND id=?",
            (user_id, entry_id),
        ).fetchone()
        return self._entry(row) if row else None

    def change_count(self, user_id: int, set_code: str, number: str,
                     delta: int) -> CollectionEntry | None:
        entry = self.get_card(user_id, set_code, number)
        now = int(time.time())
        if entry is None:
            if delta <= 0:
                return None
            self.conn.execute(
                "INSERT INTO collection_cards(user_id, set_code, number, count, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (user_id, set_code, number, delta, now, now),
            )
            log.info("collection insert", extra={"user": user_id, "set": set_code,
                                                 "number": number, "count": delta})
            return self.get_card(user_id, set_code, number)
        updated = entry.count + delta
        if updated <= 0:
            self.conn.execute("DELETE FROM collection_cards WHERE id=?", (entry.id,))
            log.info("collection delete", extra={"user": user_id, "set": set_code,
                                                 "number": number, "count": entry.count,
                                                 "reason": "count reached zero"})
            return None
        self.conn.execute(
            "UPDATE collection_cards SET count=?, updated_at=? WHERE id=?",
            (updated, now, entry.id),
        )
        log.info("collection update", extra={"user": user_id, "set": set_code, "number": number,
                                             "count": updated, "delta": delta})
        return self.entry_by_id(user_id, entry.id)

    def remove_card(self, user_id: int, set_code: str, number: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM collection_cards WHERE user_id=? AND set_code=? AND number=?",
            (user_id, set_code, number),
        )
        removed = cursor.rowcount > 0
        log.info("collection delete", extra={"user": user_id, "set": set_code,
                                             "number": number, "found": removed})
        return removed

    def remove_by_id(self, user_id: int, entry_id: int) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM collection_cards WHERE user_id=? AND id=?",
            (user_id, entry_id),
        )
        removed = cursor.rowcount > 0
        log.info("collection delete", extra={"user": user_id, "entry": entry_id, "found": removed})
        return removed

    def list_cards(self, user_id: int) -> list[CollectionEntry]:
        rows = self.conn.execute(
            "SELECT * FROM collection_cards WHERE user_id=?",
            (user_id,),
        ).fetchall()
        entries = [self._entry(row) for row in rows]
        entries.sort(key=lambda entry: (entry.set_code, natural_number_key(entry.number)))
        return entries

    def collection_total(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS total FROM collection_cards WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return int(row["total"])

    def cache_print(self, set_code: str, number: str, card: Card | None) -> None:
        self.conn.execute(
            "INSERT INTO prints_cache(set_code, number, name_en, name_ru, scryfall_id, image_url, resolved_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(set_code, number) DO UPDATE SET "
            "name_en=excluded.name_en, name_ru=excluded.name_ru, "
            "scryfall_id=excluded.scryfall_id, image_url=excluded.image_url, "
            "resolved_at=excluded.resolved_at",
            (
                set_code,
                number,
                card.name_en if card else None,
                card.name_ru if card else None,
                card.scryfall_id if card else None,
                card.image_url if card else None,
                int(time.time()),
            ),
        )
        log.debug("prints cache write", extra={"set": set_code, "number": number,
                                               "hit": card is not None})

    def cached_print(self, set_code: str, number: str) -> Card | None:
        row = self.conn.execute(
            "SELECT * FROM prints_cache WHERE set_code=? AND number=? AND name_en IS NOT NULL",
            (set_code, number),
        ).fetchone()
        if row is None:
            return None
        return Card(
            name_en=row["name_en"],
            name_ru=row["name_ru"],
            scryfall_id=row["scryfall_id"],
            set_code=set_code,
            collector_no=number,
            image_url=row["image_url"],
        )

    def print_lookup_fresh(self, set_code: str, number: str, now: int | None = None) -> bool:
        row = self.conn.execute(
            "SELECT name_en, resolved_at FROM prints_cache WHERE set_code=? AND number=?",
            (set_code, number),
        ).fetchone()
        if row is None:
            return False
        if row["name_en"]:
            return True
        now = int(time.time()) if now is None else now
        return now - int(row["resolved_at"]) < NEGATIVE_TTL_S
