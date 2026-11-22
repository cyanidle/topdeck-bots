from __future__ import annotations

import logging
from typing import Sequence

from topbot.models import Card

from .db import CollectDatabase, normalize_number

log = logging.getLogger(__name__)

BATCH_LIMIT = 75
PrintKey = tuple[str, str]


class PrintResolver:
    def __init__(self, db: CollectDatabase, scryfall=None):
        self.db = db
        self.scryfall = scryfall

    async def resolve(self, set_code: str, number: str) -> Card | None:
        if self.db.print_lookup_fresh(set_code, number):
            card = self.db.cached_print(set_code, number)
            log.info("print resolve", extra={"set": set_code, "number": number, "source": "cache",
                                             "name": card.name_en if card else None})
            return card
        cards = await self._fetch_batch([(set_code, number)])
        card = cards.get((set_code, number))
        log.info("print resolve", extra={"set": set_code, "number": number, "source": "scryfall",
                                         "name": card.name_en if card else None})
        return card

    async def resolve_missing(self, keys: Sequence[PrintKey]) -> dict[PrintKey, Card]:
        unique = sorted(set(keys))
        resolved: dict[PrintKey, Card] = {}
        pending: list[PrintKey] = []
        for key in unique:
            card = self.db.cached_print(*key)
            if card is not None:
                resolved[key] = card
                continue
            if not self.db.print_lookup_fresh(*key):
                pending.append(key)
        log.info("resolve prints", extra={"unique": len(unique), "cached": len(resolved),
                                          "pending": len(pending)})
        for start in range(0, len(pending), BATCH_LIMIT):
            fetched = await self._fetch_batch(pending[start:start + BATCH_LIMIT])
            for key, card in fetched.items():
                if card is not None:
                    resolved[key] = card
        missing = [key for key in unique if key not in resolved]
        if missing:
            log.info("prints not found on scryfall", extra={
                "missing": ",".join(f"{code}:{number}" for code, number in missing[:30])})
        return resolved

    async def _fetch_batch(self, keys: list[PrintKey]) -> dict[PrintKey, Card | None]:
        if not keys or self.scryfall is None:
            if keys:
                log.info("scryfall disabled: prints left unresolved", extra={"keys": len(keys)})
            return {}
        identifiers = [{"set": code.lower(), "collector_number": number} for code, number in keys]
        payload = await self.scryfall.collection(identifiers)
        found: dict[PrintKey, Card] = {}
        for card in payload:
            if card.set_code and card.collector_no:
                found[(card.set_code.upper(), normalize_number(card.collector_no))] = card
        log.info("scryfall batch", extra={"requested": len(keys), "found": len(found)})
        result: dict[PrintKey, Card | None] = {key: found.get(key) for key in keys}
        for key, card in result.items():
            self.db.cache_print(key[0], key[1], card)
        return result
