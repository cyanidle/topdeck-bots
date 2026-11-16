from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from .db import Database
from .windows import decide_quiet

log = logging.getLogger(__name__)

Sender = Callable[[int, str], Awaitable[None]]


class Notifier:
    def __init__(self, db: Database, sender: Sender | None = None, max_per_hour: int = 20):
        self.db = db
        self.sender = sender
        self.max_per_hour = max_per_hour

    async def notify(self, user, kind: str, ref_id: int, window_s: int | None,
                     text: str, payload: dict | None = None) -> bool:
        now = int(time.time())
        decision = decide_quiet(now, user, window_s)
        log.info("notify decision", extra={"user_id": user.id, "kind": kind, "ref_id": ref_id,
                                           "window_s": window_s, "decision": decision.reason})
        if not decision.send_now:
            self.db.reserve_notification(user.id, kind, ref_id, window_s, payload)
            self.db.conn.execute(
                "UPDATE notifications SET state=? WHERE user_id=? AND kind=? AND ref_id=?"
                " AND COALESCE(window_s,0)=?",
                (decision.reason, user.id, kind, ref_id, window_s or 0),
            )
            return False
        if self.db.count_sent_last_hour(user.id) >= self.max_per_hour:
            self.db.reserve_notification(user.id, kind, ref_id, window_s, payload)
            self.db.conn.execute(
                "UPDATE notifications SET state='rate_limited' WHERE user_id=? AND kind=? AND ref_id=?"
                " AND COALESCE(window_s,0)=?",
                (user.id, kind, ref_id, window_s or 0),
            )
            return False

        notification_id = self.db.reserve_notification(user.id, kind, ref_id, window_s, payload)
        if notification_id is None:
            return False
        if self.sender is None:
            self.db.mark_sent(notification_id, "skipped")
            return False
        try:
            await self.sender(user.id, text)
        except Exception:
            log.exception("failed to deliver notification", extra={"notification_id": notification_id,
                                                                    "user_id": user.id})
            self.db.mark_sent(notification_id, "failed")
            return False
        self.db.mark_sent(notification_id, "sent")
        log.info("notification delivered", extra={"notification_id": notification_id, "user_id": user.id,
                                                  "kind": kind, "ref_id": ref_id})
        return True
