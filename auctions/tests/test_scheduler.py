from __future__ import annotations

import unittest

from topbot.db import Database, title_hash
from topbot.models import Lot, PollStats
from topbot.notifier import Notifier
from topbot.poller import AuctionPoller
from topbot.scheduler import TICK_S, WindowScheduler

NOW = 1_795_400_000
WINDOWS = (86400, 10800, 3600, 900)


def make_lot(lot_id: int, title: str, end_ts: int, current_bid: int = 500, bid_count: int = 3) -> Lot:
    return Lot(
        id=lot_id,
        title=title,
        end_ts=end_ts,
        start_ts=end_ts - 7 * 86400,
        start_bid=500,
        current_bid=current_bid,
        bid_count=bid_count,
        bin_value=0,
        seller_id=13397,
        seller_name="pineapplepen",
        seller_city="Нахабино",
        seller_refs=7,
        shipping_quick="Нахабино, почта",
        image_url=None,
        url=f"https://topdeck.ru/apps/toptrade/auctions/{lot_id}",
    )


class RecordingNotifier(Notifier):
    def __init__(self, db: Database):
        self.sent: list[tuple[int, str]] = []
        super().__init__(db, sender=self._send)

    async def _send(self, user_id: int, text: str) -> None:
        self.sent.append((user_id, text))


class SchedulerTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.card = self.db.upsert_card("Bayou", "bayou")
        self.bolt = self.db.upsert_card("Lightning Bolt", "lightning bolt")
        self.db.add_to_watchlist(42, self.card)
        self.notifier = RecordingNotifier(self.db)
        self.scheduler = WindowScheduler(self.db, self.notifier, clock=lambda: NOW)

    def tearDown(self) -> None:
        self.db.close()

    def add_lot(self, lot: Lot, cards: list[int] | None = None, now: int | None = None) -> None:
        self.db.upsert_lot(lot, NOW if now is None else now)
        self.db.save_lot_cards(lot.id, [(card_id, 1.0, "exact_en") for card_id in (cards or [self.card])],
                               title_hash(lot.title))

    async def dispatch(self, now: int) -> int:
        return await self.scheduler.dispatch(now=now)


class WindowTimingTest(SchedulerTestBase):
    async def test_hour_window_fires_exactly_at_one_hour(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 3600))
        self.assertEqual(await self.dispatch(NOW - 1), 0)
        self.assertEqual(self.notifier.sent, [])

        self.assertEqual(await self.dispatch(NOW), 1)
        user_id, text = self.notifier.sent[0]
        self.assertEqual(user_id, 42)
        self.assertIn("Bayou", text)
        self.assertIn("1 ч", text)

    async def test_minute_window_fires_exactly_at_fifteen_minutes(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 900))
        self.assertEqual(await self.dispatch(NOW - 1), 0)
        self.assertEqual(await self.dispatch(NOW), 1)
        self.assertIn("15 мин", self.notifier.sent[0][1])

    async def test_each_window_fires_once(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 86400))
        self.assertEqual(await self.dispatch(NOW + 86400 - 86400), 1)
        self.assertEqual(await self.dispatch(NOW + 86400 - 86400), 0)
        self.assertEqual(await self.dispatch(NOW + 86400 - 3600), 1)
        self.assertEqual(await self.dispatch(NOW + 86400 - 900), 1)
        self.assertEqual(len(self.notifier.sent), 3)
        self.assertEqual(self.db.sent_windows(42, "auction_window", 1), {86400, 3600, 900})

    async def test_windows_already_sent_are_not_repeated_after_restart(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 900))
        await self.dispatch(NOW)
        fresh = WindowScheduler(self.db, RecordingNotifier(self.db), clock=lambda: NOW)
        self.assertEqual(await fresh.dispatch(now=NOW), 0)
        self.assertEqual(len(fresh.notifier.sent), 0)

    async def test_unwatched_card_is_silent(self):
        self.add_lot(make_lot(2, "Mox Diamond (STH, 100)", NOW + 900), cards=[self.bolt])
        self.assertEqual(await self.dispatch(NOW), 0)

    async def test_finished_lot_is_ignored(self):
        self.add_lot(make_lot(3, "Bayou (3ED, №283)", NOW + 900))
        self.db.conn.execute("UPDATE lots SET state='finished' WHERE id=3")
        self.assertEqual(await self.dispatch(NOW), 0)

    async def test_composition_card_name_is_mentioned(self):
        self.add_lot(make_lot(4, "Полноформатные небазовые земли", NOW + 900))
        self.db.save_composition(4, ["Necroblossom Snarl", "Bayou"], NOW)
        self.db.conn.execute("UPDATE lot_cards SET method='composition:exact_en' WHERE lot_id=4")
        self.assertEqual(await self.dispatch(NOW), 1)
        self.assertIn("Из вашего списка в лоте", self.notifier.sent[0][1])

    async def test_catch_up_sends_missed_window_after_downtime(self):
        self.add_lot(make_lot(5, "Bayou (3ED, №283)", NOW + 3000))
        self.assertEqual(await self.dispatch(NOW), 1)
        self.assertIn("50 мин", self.notifier.sent[0][1])

    async def test_hopeless_late_window_is_skipped(self):
        self.add_lot(make_lot(6, "Bayou (3ED, №283)", NOW + 600))
        self.assertEqual(await self.dispatch(NOW), 1)
        self.assertIn("10 мин", self.notifier.sent[0][1])
        self.assertEqual(self.db.sent_windows(42, "auction_window", 6), {900})


class DelayTest(SchedulerTestBase):
    def test_tick_when_nothing_is_planned(self):
        self.assertEqual(self.scheduler.delay(NOW), float(TICK_S))

    def test_sleeps_exactly_until_the_nearest_window(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 900 + 30))
        self.assertEqual(self.scheduler.delay(NOW), 30.0)

    def test_long_wait_is_capped_by_the_tick(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 2 * 86400))
        self.assertEqual(self.scheduler.delay(NOW), float(TICK_S))

    def test_overdue_window_asks_for_immediate_dispatch(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 3000))
        self.assertEqual(self.scheduler.delay(NOW + 100), 0.0)

    def test_sent_windows_are_not_planned(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 900 + 30))
        self.db.conn.execute(
            "INSERT INTO notifications(user_id, kind, ref_id, window_s, state, created_at)"
            " VALUES(42, 'auction_window', 1, 900, 'sent', ?)", (NOW,))
        self.assertEqual(self.scheduler.delay(NOW), float(TICK_S))


class QuietHoursDeferralTest(SchedulerTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.db.update_quiet(42, 0, 1439)

    async def test_window_is_deferred_not_lost(self):
        self.add_lot(make_lot(11, "Bayou (3ED, №283)", NOW + 2 * 3600))
        self.assertEqual(await self.dispatch(NOW + 3600), 0)
        row = self.db.conn.execute("SELECT state FROM notifications WHERE ref_id=11").fetchone()
        self.assertEqual(row["state"], "quiet_hours")
        self.assertEqual(self.db.sent_windows(42, "auction_window", 11), set())

    async def test_deferred_window_is_delivered_after_quiet_hours(self):
        self.add_lot(make_lot(12, "Bayou (3ED, №283)", NOW + 2 * 3600))
        await self.dispatch(NOW + 3600)
        self.assertEqual(self.notifier.sent, [])

        self.db.update_quiet(42, 0, 0)
        self.assertEqual(await self.dispatch(NOW + 3600 + 300), 1)
        self.assertIn("Bayou", self.notifier.sent[0][1])
        row = self.db.conn.execute("SELECT state FROM notifications WHERE ref_id=12").fetchone()
        self.assertEqual(row["state"], "sent")
        self.assertEqual(await self.dispatch(NOW + 3600 + 360), 0)


class PlannedWindowsTest(SchedulerTestBase):
    def test_plan_contains_every_enabled_window_of_watched_lots(self):
        self.add_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 86400))
        plan = self.db.planned_windows(NOW)
        self.assertEqual([window for _, _, window in plan], sorted(WINDOWS)[::-1])
        self.assertEqual({lot_id for _, lot_id, _ in plan}, {1})
        self.assertEqual(plan[0][0], NOW + 86400 - 86400)

    def test_plan_is_empty_for_lots_without_watchers(self):
        self.add_lot(make_lot(2, "Mox Diamond", NOW + 900), cards=[self.bolt])
        self.assertEqual(self.db.planned_windows(NOW), [])


class PollerCollectsOnlyTest(unittest.IsolatedAsyncioTestCase):
    async def test_poll_marks_matches_without_sending_anything(self):
        db = Database(":memory:")
        db.ensure_user(42, "tester")
        card = db.upsert_card("Bayou", "bayou")
        db.add_to_watchlist(42, card)
        notifier = RecordingNotifier(db)

        class Client:
            async def fetch_auctions(self):
                return [make_lot(1, "Bayou (3ED, №283)", NOW + 600)]

            async def fetch_lot(self, lot_id):
                raise AssertionError("composition should not be fetched for a title-matched lot")

        stats = await AuctionPoller(db, Client(), clock=lambda: NOW).poll_once()
        self.assertIsInstance(stats, PollStats)
        self.assertEqual(notifier.sent, [])
        self.assertEqual(db.lot_card_ids(1), [card])
        db.close()


if __name__ == "__main__":
    unittest.main()
