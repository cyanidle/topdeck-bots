from __future__ import annotations

import asyncio
import logging
import time

from .db import Database
from .dispatch import dispatch_lot_windows
from .notifier import Notifier

log = logging.getLogger(__name__)

TICK_S = 60


class WindowScheduler:
    """Слает окна уведомлений «минута в минуту»: просыпается в момент `end_ts - window`.

    Раз в TICK_S (или раньше, если окно наступает ближе) пересчитывает план по БД, поэтому
    новые лоты подхватываются в пределах минуты, а уже известные окна срабатывают точно.
    """

    def __init__(self, db: Database, notifier: Notifier, clock=time.time, tick_s: int = TICK_S):
        self.db = db
        self.notifier = notifier
        self.clock = clock
        self.tick_s = tick_s

    def delay(self, now: int) -> float:
        plan = self.db.planned_windows(now)
        if not plan:
            return float(self.tick_s)
        wait = plan[0][0] - now
        if wait <= 0:
            return 0.0
        return float(min(wait, self.tick_s))

    async def dispatch(self, now: int | None = None) -> int:
        now = int(self.clock()) if now is None else now
        subscribers = self.db.subscribers_by_card()
        if not subscribers:
            return 0
        cards = {card.id: card for card in self.db.cards_index()}
        sent = 0
        for lot_id in self.db.watched_lot_ids(now):
            lot = self.db.lot(lot_id)
            if lot is None or lot.end_ts <= now:
                continue
            sent += await dispatch_lot_windows(self.db, self.notifier, lot, subscribers, cards, now)
        return sent

    async def run(self) -> None:
        log.info("window scheduler started", extra={"tick_s": self.tick_s})
        while True:
            sent = 0
            try:
                wait = self.delay(int(self.clock()))
                if wait > 0:
                    await asyncio.sleep(wait)
                sent = await self.dispatch()
            except Exception:
                log.exception("window dispatch failed")
            if sent:
                log.info("windows dispatched", extra={"sent": sent})
            else:
                await asyncio.sleep(self.tick_s)
