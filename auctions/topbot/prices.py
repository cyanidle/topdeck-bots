from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .db import Database
from .matching import normalize
from .models import Card, Offer

log = logging.getLogger(__name__)

PRICE_BATCH = 8


@dataclass(slots=True)
class PriceStats:
    cards: int = 0
    offers: int = 0
    requests: int = 0


def match_cards(cards: list[Card], offers: list[Offer]) -> dict[int, list[Offer]]:
    by_name: dict[str, int] = {}
    for card in cards:
        if card.id is None:
            continue
        by_name.setdefault(normalize(card.name_en), card.id)
        if card.name_ru:
            by_name.setdefault(normalize(card.name_ru), card.id)
    found: dict[int, list[Offer]] = {}
    for offer in offers:
        card_id = by_name.get(normalize(offer.eng_name))
        if card_id is None and offer.rus_name:
            card_id = by_name.get(normalize(offer.rus_name))
        if card_id is None or offer.cost <= 0:
            continue
        found.setdefault(card_id, []).append(offer)
    return found


async def search_offers(client, names: list[str]) -> list[Offer]:
    offers, truncated = await client.search_singles(names)
    if not truncated:
        return offers
    if len(names) == 1:
        log.warning("singles search truncated", extra={"cards": len(names), "offers": len(offers)})
        return offers
    log.warning("singles search truncated, splitting batch", extra={"cards": len(names), "offers": len(offers)})
    middle = len(names) // 2
    return await search_offers(client, names[:middle]) + await search_offers(client, names[middle:])


async def refresh_cards(client, db: Database, cards: list[Card], now: int) -> PriceStats:
    stats = PriceStats()
    for start in range(0, len(cards), PRICE_BATCH):
        batch = cards[start:start + PRICE_BATCH]
        offers = await search_offers(client, [card.name_en for card in batch])
        found = match_cards(batch, offers)
        stats.requests += 1
        for card in batch:
            stats.cards += 1
            stats.offers += db.save_card_offers(card.id, found.get(card.id, []), now)
    return stats


class PricePoller:
    def __init__(self, db: Database, client, clock=time.time):
        self.db = db
        self.client = client
        self.clock = clock

    async def poll_once(self) -> PriceStats:
        cards = self.db.watched_cards()
        if not cards:
            self.db.prune_card_offers([])
            log.info("price poll skipped", extra={"cards": 0})
            return PriceStats()
        started_at = int(self.clock())
        started = time.perf_counter()
        try:
            stats = await refresh_cards(self.client, self.db, cards, started_at)
        except Exception as exc:
            self.db.record_poll("prices", started_at, int((time.perf_counter() - started) * 1000), False, 0,
                                repr(exc))
            log.error("price poll failed", extra={"error": repr(exc)}, exc_info=True)
            raise
        self.db.prune_card_offers([card.id for card in cards])
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.db.record_poll("prices", started_at, duration_ms, True, stats.cards)
        log.info("price poll done", extra={"cards": stats.cards, "requests": stats.requests,
                                           "offers": stats.offers, "ms": duration_ms})
        return stats
