from __future__ import annotations

import json
import logging
import time

from .db import SETS_SYNCED_KEY
from .matching import normalize

log = logging.getLogger(__name__)

CATALOG_TTL_S = 7 * 86400
SETS_TTL_S = 30 * 86400
META_KEY = "card_names_synced_at"
NO_RU_KEY = "cards_without_ru"


def score(name_norm: str, query_norm: str) -> int:
    if not query_norm:
        return 0
    if name_norm == query_norm:
        return 0
    if name_norm.startswith(query_norm):
        return 1
    if any(word.startswith(query_norm) for word in name_norm.split()):
        return 2
    if query_norm in name_norm:
        return 3
    return 99


class CardCatalog:
    def __init__(self, db, scryfall=None):
        self.db = db
        self.scryfall = scryfall

    def count(self) -> int:
        return int(self.db.conn.execute("SELECT COUNT(*) AS n FROM card_names").fetchone()["n"])

    def synced_at(self) -> int:
        row = self.db.conn.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
        return int(row["value"]) if row else 0

    def is_stale(self, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        return self.count() == 0 or now - self.synced_at() > CATALOG_TTL_S

    def store_names(self, names: list[str], lang: str = "en") -> int:
        rows = [(name, normalize(name), lang) for name in names if name and name.strip()]
        try:
            self.db.conn.execute("BEGIN")
            self.db.conn.executemany("INSERT OR IGNORE INTO card_names(name, name_norm, lang) VALUES(?,?,?)", rows)
            self.db.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (META_KEY, str(int(time.time()))),
            )
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        return len(rows)

    def add_local_card(self, name_en: str, name_ru: str | None = None) -> None:
        rows = [(name_en, normalize(name_en), "en")]
        if name_ru:
            rows.append((name_ru, normalize(name_ru), "ru"))
        self.db.conn.executemany("INSERT OR IGNORE INTO card_names(name, name_norm, lang) VALUES(?,?,?)", rows)

    async def refresh(self, force: bool = False) -> int:
        if self.scryfall is None:
            raise RuntimeError("scryfall client is required to refresh the catalog")
        if not force and not self.is_stale():
            return 0
        names = await self.scryfall.card_names()
        return self.store_names(names)

    def sets_count(self) -> int:
        return int(self.db.conn.execute("SELECT COUNT(*) AS n FROM sets").fetchone()["n"])

    def sets_are_stale(self, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        return self.sets_count() == 0 or now - self.db.meta_int(SETS_SYNCED_KEY) > SETS_TTL_S

    async def refresh_sets(self, force: bool = False) -> int:
        if self.scryfall is None:
            raise RuntimeError("scryfall client is required to refresh the catalog")
        if not force and not self.sets_are_stale():
            return 0
        rows = await self.scryfall.card_sets()
        return self.db.save_sets(rows)

    async def fill_russian_names(self) -> int:
        if self.scryfall is None:
            return 0
        rows = self.db.conn.execute(
            "SELECT DISTINCT c.id, c.name_en FROM cards c JOIN watchlist w ON w.card_id = c.id"
            " WHERE c.name_ru IS NULL OR c.name_ru = '' ORDER BY c.name_en"
        ).fetchall()
        without_ru = self._without_ru()
        filled = 0
        for row in rows:
            if row["name_en"] in without_ru:
                continue
            try:
                name_ru = await self.scryfall.localized_name(row["name_en"])
            except Exception:
                log.warning("localized name lookup failed", extra={"card": row["name_en"]}, exc_info=True)
                continue
            if not name_ru:
                without_ru.add(row["name_en"])
                continue
            self.remember_card_name(row["name_en"], name_ru)
            filled += 1
        self._store_without_ru(without_ru)
        if filled:
            self.db.invalidate_lot_matches()
        return filled

    def remember_card_name(self, name_en: str, name_ru: str | None) -> None:
        if not name_ru:
            return
        self.db.conn.execute(
            "UPDATE cards SET name_ru=? WHERE name_norm=? AND (name_ru IS NULL OR name_ru='')",
            (name_ru, normalize(name_en)),
        )
        self.add_local_card(name_en, name_ru)

    def _without_ru(self) -> set[str]:
        row = self.db.conn.execute("SELECT value FROM meta WHERE key=?", (NO_RU_KEY,)).fetchone()
        return set(json.loads(row["value"])) if row else set()

    def _store_without_ru(self, names: set[str]) -> None:
        self.db.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (NO_RU_KEY, json.dumps(sorted(names), ensure_ascii=False)),
        )

    def search(self, query: str, limit: int = 8) -> list[str]:
        query_norm = normalize(query)
        if len(query_norm) < 2:
            return []
        rows = self.db.conn.execute(
            "SELECT name, name_norm FROM card_names WHERE name_norm LIKE ? LIMIT 400",
            (f"%{query_norm}%",),
        ).fetchall()
        ranked = sorted(
            ((score(row["name_norm"], query_norm), len(row["name_norm"]), row["name"]) for row in rows),
        )
        return [name for rank, _, name in ranked if rank < 99][:limit]

    async def suggest(self, query: str, limit: int = 5) -> list[str]:
        local = self.search(query, limit)
        if len(local) >= limit or not query.strip():
            return local
        if self.scryfall is not None and self.count() == 0:
            try:
                remote = await self.scryfall.autocomplete(query)
            except Exception:
                remote = []
            merged: list[str] = []
            for name in [*local, *remote]:
                if name not in merged:
                    merged.append(name)
            return merged[:limit]
        return local
