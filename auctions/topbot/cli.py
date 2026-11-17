from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict

import aiohttp

from .config import Settings
from .db import Database
from .catalog import CardCatalog
from .clients.scryfall import ScryfallClient
from .clients.topdeck import TopdeckClient
from .messages import render_auction_message, render_offer_line
from .notifier import Notifier
from .poller import AuctionPoller
from .prices import cheapest_per_card
from .scheduler import WindowScheduler


async def _client(settings: Settings) -> tuple[aiohttp.ClientSession, TopdeckClient]:
    session = aiohttp.ClientSession()
    return session, TopdeckClient(session, base=settings.topdeck_base, user_agent=settings.user_agent,
                                  throttle_s=settings.throttle_s)


async def cmd_auctions(settings: Settings, hours: float, limit: int) -> int:
    session, client = await _client(settings)
    try:
        lots = await client.fetch_auctions()
    finally:
        await session.close()
    now = int(time.time())
    upcoming = [lot for lot in lots if 0 < lot.end_ts - now <= hours * 3600]
    upcoming.sort(key=lambda lot: lot.end_ts)
    print(f"active lots: {len(lots)}  ending within {hours}h: {len(upcoming)}")
    for lot in upcoming[:limit]:
        print("—" * 60)
        print(render_auction_message(lot, lot.end_ts - now))
    return 0


async def cmd_search(settings: Settings, names: list[str]) -> int:
    session, client = await _client(settings)
    try:
        offers, truncated = await client.search_singles(names)
    finally:
        await session.close()
    best = {label: offer for label, offer in cheapest_per_card(offers).values()}
    print(f"offers: {len(offers)}, truncated: {truncated}, cards: {len(best)}")
    for name, offer in sorted(best.items(), key=lambda item: item[1].cost):
        print(f"{name:40} {render_offer_line(offer)}  {offer.url}")
    return 0


async def cmd_catalog(settings: Settings, force: bool, query: str | None) -> int:
    db = Database(settings.db_path)
    session = aiohttp.ClientSession()
    scryfall = ScryfallClient(session, base=settings.scryfall_base, user_agent=settings.user_agent,
                              proxy=settings.scryfall_proxy)
    catalog = CardCatalog(db, scryfall)
    try:
        added = await catalog.refresh(force=force)
        print(f"catalog: +{added} new, total {catalog.count()} names")
        if query:
            for name in await catalog.suggest(query, 5):
                print("  ", name)
    finally:
        await session.close()
        db.close()
    return 0


async def cmd_poll(settings: Settings) -> int:
    db = Database(settings.db_path)
    session, client = await _client(settings)

    async def sender(user_id: int, text: str) -> None:
        print(f"[notify -> {user_id}]\n{text}\n")

    try:
        poller = AuctionPoller(db, client)
        stats = await poller.poll_once()
        await WindowScheduler(db, Notifier(db, sender)).dispatch()
    finally:
        await session.close()
        db.close()
    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    settings = Settings.from_env()
    command = argv[0] if argv else "help"
    if command == "auctions":
        hours = float(argv[1]) if len(argv) > 1 else 24.0
        limit = int(argv[2]) if len(argv) > 2 else 5
        return asyncio.run(cmd_auctions(settings, hours, limit))
    if command == "search":
        return asyncio.run(cmd_search(settings, argv[1:]))
    if command == "poll":
        return asyncio.run(cmd_poll(settings))
    if command == "catalog":
        force = "--force" in argv
        query = next((item for item in argv[1:] if not item.startswith("-")), None)
        return asyncio.run(cmd_catalog(settings, force, query))
    print("usage: python -m topbot.cli {auctions [hours] [limit] | search <card>... | poll | catalog [--force] [query]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
