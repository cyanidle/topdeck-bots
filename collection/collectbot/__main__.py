from __future__ import annotations

import asyncio
import contextlib
import logging

from aiohttp import ClientSession

from aiogram import Dispatcher

from topbot.clients.scryfall import ScryfallClient
from topbot.logs import setup_logging

from .bot import create_bot, init_context, router, setup_bot_commands
from .config import Settings
from .db import CollectDatabase
from .resolver import PrintResolver
from .scanner import load_set_codes


async def main() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.log_format, settings.log_file or None)
    log = logging.getLogger("collectbot")
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is empty: fill .env")
    set_codes = load_set_codes(settings.set_codes_path)
    log.info("starting collectbot", extra={
        **settings.redacted(),
        "set_codes": len(set_codes),
    })

    db = CollectDatabase(settings.db_path)
    session = ClientSession()
    scryfall = (ScryfallClient(session, base=settings.scryfall_base,
                               user_agent=settings.user_agent,
                               proxy=settings.scryfall_proxy)
                if settings.scryfall_enabled else None)
    resolver = PrintResolver(db, scryfall)
    init_context(settings, db, resolver, set_codes)

    bot = create_bot(settings)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        await setup_bot_commands(bot)
        await dispatcher.start_polling(bot)
    except Exception:
        log.exception("polling crashed")
    finally:
        log.info("stopping collectbot")
        await bot.session.close()
        with contextlib.suppress(Exception):
            await session.close()
        db.close()
        log.info("collectbot stopped")


if __name__ == "__main__":
    asyncio.run(main())
