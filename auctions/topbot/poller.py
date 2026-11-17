from __future__ import annotations

import logging
import time

from aiohttp import ClientError

from . import matching
from .composition import CLEAN_VERSION, clean_composition
from .db import Database
from .models import PollStats

log = logging.getLogger(__name__)

COMPOSITION_BUDGET = 20
COMPOSITION_INTERVAL_S = 600
TRUNCATED_POLL_RATIO = 0.9
COMPOSITION_CLEAN_KEY = "composition_clean_version"
POLL_RUNS_TTL_S = 7 * 86400
POLL_RUNS_PRUNE_S = 86400
FAILURE_ALERT_THRESHOLD = 5
EMPTY_ALERT_THRESHOLD = 3
TRUNCATED_ALERT_THRESHOLD = 2
ADMIN_ALERT_COOLDOWN_S = 3600


class AuctionPoller:
    def __init__(self, db: Database, client, clock=time.time):
        self.db = db
        self.client = client
        self.clock = clock
        self._name_index: frozenset[str] | None = None
        self._compositions_at = 0
        self._pruned_at = 0

    async def poll_once(self) -> PollStats:
        started_at = int(self.clock())
        started_perf = time.perf_counter()
        self._clean_stored_compositions()
        try:
            lots = await self.client.fetch_auctions()
        except Exception as exc:
            self.db.record_poll("auctions", started_at, int((time.perf_counter() - started_perf) * 1000),
                                False, 0, repr(exc))
            log.error("auction poll failed", extra={"error": repr(exc)}, exc_info=True)
            raise

        now = int(self.clock())
        log.info("auction poll received", extra={"lots": len(lots)})
        stats = PollStats(lots=len(lots))
        active_before = self.db.active_lots_count()
        known_ends = self.db.lot_end_ts_map([lot.id for lot in lots])
        for lot in lots:
            previous_end = known_ends.get(lot.id)
            if previous_end is not None and lot.end_ts > previous_end:
                stats.extended += 1
                log.info("lot end_ts moved", extra={"lot_id": lot.id, "old_end_ts": previous_end,
                                                    "new_end_ts": lot.end_ts,
                                                    "delta_s": lot.end_ts - previous_end,
                                                    "bids": lot.bid_count})
            self.db.upsert_lot(lot, now)
        stats.vanished = self.db.mark_overdue(now)
        if _poll_is_complete(len(lots), active_before):
            stats.vanished += self.db.mark_vanished([lot.id for lot in lots])
        elif lots:
            stats.truncated = True
            log.warning("auction poll looks truncated, vanished lots kept",
                        extra={"lots": len(lots), "active": active_before})
        self._prune_poll_runs(now)

        subscribers = self.db.subscribers_by_card()
        if not subscribers:
            self.db.record_poll("auctions", started_at, int((time.perf_counter() - started_perf) * 1000),
                                True, len(lots))
            return stats

        ensure_matches(self.db)
        if now - self._compositions_at >= COMPOSITION_INTERVAL_S:
            self._compositions_at = now
            stats.compositions = await self._refresh_compositions(now)
            if stats.compositions:
                ensure_matches(self.db)
        for lot in lots:
            card_ids = self.db.lot_card_ids(lot.id)
            if card_ids:
                stats.matched_lots += 1
            watched = [card_id for card_id in card_ids if card_id in subscribers]
            if not watched:
                continue
            stats.watched_lots += 1
            log.info("lot matches watchlist", extra={"lot_id": lot.id, "cards": len(card_ids),
                                                     "watched": len(watched),
                                                     "ends_in_min": (lot.end_ts - now) // 60})

        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        self.db.record_poll("auctions", started_at, duration_ms, True, len(lots))
        log.info("auction poll done", extra={"lots": stats.lots, "matched_lots": stats.matched_lots,
                                             "watched_lots": stats.watched_lots,
                                             "vanished": stats.vanished,
                                             "compositions": stats.compositions,
                                             "extended": stats.extended,
                                             "requests": 1 + stats.compositions, "ms": duration_ms})
        return stats

    def _prune_poll_runs(self, now: int) -> None:
        if now - self._pruned_at < POLL_RUNS_PRUNE_S:
            return
        self._pruned_at = now
        pruned = self.db.prune_poll_runs(now - POLL_RUNS_TTL_S)
        if pruned:
            log.info("poll runs pruned", extra={"rows": pruned})

    def _card_names(self) -> frozenset[str]:
        if self._name_index is None:
            names = self.db.card_name_index()
            if names:
                self._name_index = names
        return self._name_index or frozenset()

    def _clean_stored_compositions(self) -> None:
        if self.db.meta_int(COMPOSITION_CLEAN_KEY) >= CLEAN_VERSION:
            return
        sets = self.db.set_index()
        if not sets.names:
            return
        cleaned = self.db.clean_compositions(sets, self._card_names())
        self.db.set_meta(COMPOSITION_CLEAN_KEY, str(CLEAN_VERSION))
        log.info("stored compositions cleaned", extra={"lots": cleaned})

    async def _refresh_compositions(self, now: int) -> int:
        pending = self.db.lots_needing_composition(limit=COMPOSITION_BUDGET * 4, now=now)
        if not pending:
            return 0
        sets = self.db.set_index()
        matched = self.db.lots_with_matches()
        fetched = 0
        for lot_id, title in pending:
            if fetched >= COMPOSITION_BUDGET:
                break
            if lot_id in matched and not matching.is_multi_lot(title):
                continue
            try:
                detail = await self.client.fetch_lot(lot_id)
            except (TimeoutError, OSError, ClientError):
                log.warning("lot composition fetch failed", extra={"lot_id": lot_id}, exc_info=True)
                continue
            self.db.save_composition(lot_id, clean_composition(detail.composition, title, sets,
                                                               self._card_names()), now)
            fetched += 1
        log.info("lot compositions refreshed", extra={"fetched": fetched, "pending": len(pending)})
        return fetched


class AdminAlerter:
    def __init__(self, db: Database, sender, admin_ids: tuple[int, ...] = (),
                 admin_usernames: tuple[str, ...] = (), threshold: int = FAILURE_ALERT_THRESHOLD,
                 empty_threshold: int = EMPTY_ALERT_THRESHOLD,
                 truncated_threshold: int = TRUNCATED_ALERT_THRESHOLD,
                 cooldown_s: int = ADMIN_ALERT_COOLDOWN_S, clock=time.time):
        self.db = db
        self.sender = sender
        self.admin_ids = admin_ids
        self.admin_usernames = admin_usernames
        self.threshold = threshold
        self.empty_threshold = empty_threshold
        self.truncated_threshold = truncated_threshold
        self.cooldown_s = cooldown_s
        self.clock = clock
        self.failures = 0
        self.empty_streak = 0
        self.truncated_streak = 0
        self.alerted = False
        self.alerted_at: dict[str, int] = {}

    def recipients(self) -> list[int]:
        ids = list(dict.fromkeys(self.admin_ids))
        for username in self.admin_usernames:
            user_id = self.db.user_id_by_username(username)
            if user_id is None:
                log.warning("admin username is unknown", extra={"username": username})
            elif user_id not in ids:
                ids.append(user_id)
        return ids

    def _cooldown_passed(self, kind: str) -> bool:
        now = int(self.clock())
        if now - self.alerted_at.get(kind, 0) < self.cooldown_s:
            return False
        self.alerted_at[kind] = now
        return True

    async def _send(self, text: str) -> None:
        recipients = self.recipients()
        if not recipients:
            log.warning("admin alert has no recipients", extra={"text": text[:80]})
            return
        for user_id in recipients:
            try:
                await self.sender(user_id, text)
            except Exception:
                log.exception("admin alert failed", extra={"user_id": user_id})

    async def on_failure(self, error: str) -> None:
        self.failures += 1
        if self.failures < self.threshold or not self._cooldown_passed("failures"):
            return
        self.alerted = True
        await self._send(f"topbot: {self.failures} опроса аукционов подряд с ошибкой.\n{error}")

    async def on_success(self, stats: PollStats) -> None:
        recovered = self.alerted
        self.failures = 0
        self.alerted = False
        if recovered:
            await self._send("topbot: опрос аукционов восстановился.")
        self.empty_streak = self.empty_streak + 1 if stats.lots == 0 else 0
        if self.empty_streak == self.empty_threshold and self._cooldown_passed("empty"):
            await self._send("topbot: два опроса подряд вернули 0 лотов — похоже, сайт изменил вёрстку "
                             "или недоступен.")
        self.truncated_streak = self.truncated_streak + 1 if stats.truncated else 0
        if self.truncated_streak == self.truncated_threshold and self._cooldown_passed("truncated"):
            await self._send("topbot: два опроса подряд вернули меньше половины лотов — выкупленные и снятые "
                             "лоты не закрываются, /list может показывать завершённые аукционы.")


def _poll_is_complete(seen: int, active_before: int) -> bool:
    return seen > 0 and seen >= active_before * TRUNCATED_POLL_RATIO


def _merge(*groups: list[tuple[int, float, str]]) -> list[tuple[int, float, str]]:
    best: dict[int, tuple[int, float, str]] = {}
    for group in groups:
        for card_id, confidence, method in group:
            current = best.get(card_id)
            if current is None or confidence > current[1]:
                best[card_id] = (card_id, confidence, method)
    return sorted(best.values(), key=lambda item: (-item[1], item[0]))


def ensure_matches(db: Database) -> int:
    """Пересчитывает привязку лот↔карта по уже лежащим в БД лотам (по названию и составу, без сети)."""
    cards = db.cards_index()
    if not cards:
        return 0
    compositions = db.compositions()
    rows = db.conn.execute(
        "SELECT id, title, title_hash, matched_hash FROM lots WHERE state='active'"
    ).fetchall()
    updated = 0
    for row in rows:
        if row["matched_hash"] == row["title_hash"]:
            continue
        lot_id = int(row["id"])
        matches = matching.match_title(row["title"], cards)
        items = compositions.get(lot_id)
        if items:
            matches = _merge(matches, matching.match_names(items, cards))
        db.save_lot_cards(lot_id, matches, row["title_hash"])
        updated += 1
    return updated
