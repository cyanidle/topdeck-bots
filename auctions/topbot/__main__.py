from __future__ import annotations

import asyncio
import contextlib
import logging

from .bot import TelegramSender, build_notifier, create_bot, create_dispatcher, setup_bot_ui
from .catalog import CardCatalog
from .logs import setup_logging
from .clients.scryfall import ScryfallClient
from .clients.topdeck import TopdeckClient
from .config import Settings
from .db import Database
from .poller import AdminAlerter, AuctionPoller
from .webui import make_session, serve


async def main() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.log_format, settings.log_file or None)
    log = logging.getLogger("topbot")
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is empty: fill .env")
    log.info("starting topbot", extra={
        "db": settings.db_path,
        "web": f"{settings.web_bind}:{settings.web_port}",
        "poll_interval_s": settings.poll_interval_s,
        "telegram_proxy": settings.telegram_proxy or "direct",
        "scryfall_proxy": settings.scryfall_proxy or "direct",
        "scryfall": settings.scryfall_enabled,
        "admins": list(settings.admin_usernames) or list(settings.admin_ids),
    })

    db = Database(settings.db_path)
    session = make_session()
    topdeck = TopdeckClient(session, base=settings.topdeck_base, user_agent=settings.user_agent,
                            throttle_s=settings.throttle_s)
    scryfall = ScryfallClient(session, base=settings.scryfall_base, user_agent=settings.user_agent,
                              proxy=settings.scryfall_proxy)

    bot = create_bot(settings)
    await setup_bot_ui(bot)
    catalog = CardCatalog(db, scryfall)
    dispatcher = create_dispatcher(settings, db, topdeck, scryfall, catalog)
    notifier = build_notifier(db, bot)
    poller = AuctionPoller(db, topdeck, notifier)
    alerter = AdminAlerter(db, TelegramSender(bot), admin_ids=settings.admin_ids,
                           admin_usernames=settings.admin_usernames)

    tasks = [asyncio.create_task(_poll_loop(settings, poller, alerter), name="poller"),
             asyncio.create_task(_catalog_sync(catalog), name="catalog")]
    if settings.web_port:
        tasks.append(asyncio.create_task(serve(settings, db), name="webui"))

    try:
        await dispatcher.start_polling(bot)
    except Exception:
        log.exception("polling crashed")
    finally:
        log.info("stopping topbot")
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await bot.session.close()
        await session.close()
        db.close()
        log.info("topbot stopped")


async def _catalog_sync(catalog: CardCatalog) -> None:
    logger = logging.getLogger("catalog")
    try:
        added = await catalog.refresh()
        logger.info("card name catalog: %s new names, total %s", added, catalog.count())
    except Exception:
        logger.exception("catalog refresh failed")
    try:
        filled = await catalog.fill_russian_names()
        if filled:
            logger.info("russian names filled", extra={"cards": filled})
    except Exception:
        logger.exception("russian names backfill failed")


async def _alert(logger: logging.Logger, action, *args) -> None:
    try:
        await action(*args)
    except Exception:
        logger.exception("admin alert failed")


async def _poll_loop(settings: Settings, poller: AuctionPoller, alerter: AdminAlerter) -> None:
    logger = logging.getLogger("poller")
    while True:
        try:
            stats = await poller.poll_once()
        except Exception as exc:
            logger.exception("auction poll failed")
            await _alert(logger, alerter.on_failure, repr(exc))
        else:
            await _alert(logger, alerter.on_success, stats)
        await asyncio.sleep(settings.poll_interval_s)


if __name__ == "__main__":
    asyncio.run(main())
