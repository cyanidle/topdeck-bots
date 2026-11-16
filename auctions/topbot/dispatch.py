from __future__ import annotations

import logging

from .db import Database
from .messages import INLINE_OFFERS, render_auction_message
from .models import Card, Lot
from .notifier import Notifier
from .windows import due_window

log = logging.getLogger(__name__)


async def dispatch_lot_windows(db: Database, notifier: Notifier, lot: Lot,
                               subscribers: dict[int, list[int]], cards: dict[int, Card],
                               now: int) -> int:
    """Отправляет все назревшие окна по одному лоту. Возвращает число доставленных сообщений."""
    card_ids = db.lot_card_ids(lot.id)
    watched = [card_id for card_id in card_ids if card_id in subscribers]
    if not watched:
        return 0
    methods = db.lot_card_methods(lot.id)
    offers_by_card = None
    remaining = lot.end_ts - now
    sent = 0
    for card_id in watched:
        card = cards.get(card_id)
        from_composition = methods.get(card_id, "").startswith("composition:")
        card_name = card.name_en if card and from_composition else None
        for user_id in subscribers[card_id]:
            user = db.get_user(user_id)
            window = due_window(remaining, user.windows,
                                db.sent_windows(user_id, "auction_window", lot.id))
            if window is None:
                continue
            if offers_by_card is None:
                offers_by_card = db.top_offers_by_card(watched, INLINE_OFFERS)
            text = render_auction_message(lot, remaining, card_name=card_name,
                                          offers=offers_by_card.get(card_id, []))
            delivered = await notifier.notify(user, "auction_window", lot.id, window, text,
                                              {"lot": lot.id, "card_id": card_id})
            log.info("auction notification", extra={"user_id": user_id, "lot_id": lot.id,
                                                    "window_s": window, "remaining_s": remaining,
                                                    "delivered": delivered})
            if delivered:
                sent += 1
    return sent
