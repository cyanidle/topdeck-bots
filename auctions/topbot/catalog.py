from __future__ import annotations

import time

from .matching import normalize

CATALOG_TTL_S = 7 * 86400
META_KEY = "card_names_synced_at"


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
