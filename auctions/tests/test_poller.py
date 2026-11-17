from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
import unittest

from topbot.db import WINDOWS_MIGRATION_KEY, Database, title_hash
from topbot.matching import MATCH_VERSION
from topbot.models import Lot, LotDetail, PollStats
from topbot.poller import (ADMIN_ALERT_COOLDOWN_S, EMPTY_ALERT_THRESHOLD, FAILURE_ALERT_THRESHOLD,
                           AdminAlerter, AuctionPoller, ensure_matches)

NOW = 1_795_400_000


def make_lot(lot_id: int, title: str, end_ts: int, *, start_bid: int = 500, current_bid: int = 500,
             bid_count: int = 3, bin_value: int = 0) -> Lot:
    return Lot(
        id=lot_id,
        title=title,
        end_ts=end_ts,
        start_ts=end_ts - 7 * 86400,
        start_bid=start_bid,
        current_bid=current_bid,
        bid_count=bid_count,
        bin_value=bin_value,
        seller_id=13397,
        seller_name="pineapplepen",
        seller_city="Нахабино",
        seller_refs=7,
        shipping_quick="Нахабино, почта",
        image_url=None,
        url=f"https://topdeck.ru/apps/toptrade/auctions/{lot_id}",
    )


def lot(lot_id: int, title: str, end_ts: int, current_bid: int = 500) -> Lot:
    return make_lot(lot_id, title, end_ts, current_bid=current_bid)


def matched_cards(db: Database, lot_id: int) -> list[int]:
    rows = db.conn.execute("SELECT card_id FROM lot_cards WHERE lot_id=? ORDER BY card_id", (lot_id,)).fetchall()
    return [int(row["card_id"]) for row in rows]


class FakeClient:
    def __init__(self, lots: list[Lot], compositions: dict[int, tuple[str, ...]] | None = None):
        self.lots = lots
        self.compositions = compositions or {}
        self.lot_fetches: list[int] = []

    async def fetch_auctions(self) -> list[Lot]:
        return self.lots

    async def fetch_lot(self, lot_id: int) -> LotDetail:
        self.lot_fetches.append(lot_id)
        return LotDetail(lot_id=lot_id, composition=self.compositions.get(lot_id, ()))


class PollerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.card_id = self.db.upsert_card("Bayou", "bayou", name_ru=None)
        self.db.upsert_card("Tarmogoyf", "tarmogoyf", name_ru="Тармогойф")
        self.db.add_to_watchlist(42, self.card_id)

    def tearDown(self) -> None:
        self.db.close()

    def poller(self, lots: list[Lot], compositions: dict[int, tuple[str, ...]] | None = None) -> AuctionPoller:
        return AuctionPoller(self.db, FakeClient(lots, compositions), clock=lambda: NOW)

    async def test_watched_lot_is_actually_matched(self):
        lots = [lot(1, "Bayou - Reserved List (3ED, №283)", NOW + 600, current_bid=1488)]
        stats = await self.poller(lots).poll_once()
        self.assertEqual(stats.watched_lots, 1)
        self.assertEqual(matched_cards(self.db, 1), [self.card_id])

    async def test_unwatched_card_is_not_counted(self):
        lots = [lot(2, "Mox Diamond (STH, 100)", NOW + 600)]
        stats = await self.poller(lots).poll_once()
        self.assertEqual(stats.watched_lots, 0)

    async def test_lot_far_in_future_is_still_tracked(self):
        lots = [lot(3, "Bayou (3ED, №283)", NOW + 5 * 86400)]
        stats = await self.poller(lots).poll_once()
        self.assertEqual(stats.watched_lots, 1)

    async def test_multi_card_lot_matches_from_composition(self):
        lots = [lot(20, "Полноформатные небазовые земли", NOW + 600)]
        stats = await self.poller(lots, {20: ("Necroblossom Snarl", "Bayou")}).poll_once()
        self.assertEqual(stats.watched_lots, 1)
        self.assertEqual(matched_cards(self.db, 20), [self.card_id])

    async def test_composition_is_fetched_once(self):
        lots = [lot(21, "Полноформатные Тропы KHM", NOW + 600)]
        client_lots = lots
        poller = AuctionPoller(self.db, FakeClient(client_lots, {21: ("Bayou",)}), clock=lambda: NOW)
        await poller.poll_once()
        await poller.poll_once()
        self.assertEqual(poller.client.lot_fetches, [21])
        self.assertEqual(self.db.lot_card_ids(21), [self.card_id])

    async def test_lot_already_matched_by_title_is_not_fetched(self):
        lots = [lot(22, "Bayou - Reserved List (3ED, №283)", NOW + 600)]
        poller = AuctionPoller(self.db, FakeClient(lots, {22: ("Bayou",)}), clock=lambda: NOW)
        await poller.poll_once()
        self.assertEqual(poller.client.lot_fetches, [])

    async def test_russian_title_matches(self):
        lots = [lot(4, "Фойл Тармогойф (FUT, 153, RU, NM)", NOW + 600)]
        self.db.add_to_watchlist(42, 2)
        await self.poller(lots).poll_once()
        self.assertEqual(matched_cards(self.db, 4), [2])

    async def test_expired_lot_is_finished_even_when_still_listed(self):
        await self.poller([lot(5, "Bayou (3ED, №283)", NOW - 60)]).poll_once()
        self.assertEqual(self._states()[5], "finished")

    async def test_vanished_lot_is_marked_finished(self):
        lots = [lot(60 + index, f"Bayou (3ED, №283) {index}", NOW + 3600) for index in range(10)]
        poller = self.poller(lots)
        await poller.poll_once()
        self.assertEqual(self._states()[60], "active")
        poller.client.lots = lots[1:]
        stats = await poller.poll_once()
        self.assertEqual((stats.vanished, stats.truncated), (1, False))
        self.assertEqual(self._states()[60], "finished")

    async def test_lot_gone_before_its_deadline_leaves_the_list(self):
        end_ts = int(time.time()) + 3600
        poller = AuctionPoller(self.db, FakeClient([lot(30, "Bayou (3ED, №283)", end_ts)]), clock=time.time)
        await poller.poll_once()
        card = self.db.watchlist_overview(42)[0]
        self.assertEqual((card.lots_count, card.next_lot_id), (1, 30))
        poller.client.lots = [lot(31, "Other (XYZ)", end_ts)]
        await poller.poll_once()
        card = self.db.watchlist_overview(42)[0]
        self.assertEqual((card.lots_count, card.next_lot_id, card.next_end_ts), (0, None, None))

    async def test_empty_poll_keeps_lots(self):
        poller = self.poller([lot(40, "Bayou (3ED, №283)", NOW + 3600)])
        await poller.poll_once()
        poller.client.lots = []
        stats = await poller.poll_once()
        self.assertEqual((stats.vanished, stats.truncated), (0, False))
        self.assertEqual(self._states(), {40: "active"})

    async def test_truncated_poll_keeps_lots(self):
        lots = [lot(50 + index, f"Bayou (3ED, №283) {index}", NOW + 3600) for index in range(10)]
        poller = self.poller(lots)
        await poller.poll_once()
        poller.client.lots = lots[:5]
        stats = await poller.poll_once()
        self.assertEqual(stats.vanished, 0)
        self.assertTrue(stats.truncated)
        self.assertEqual(set(self._states().values()), {"active"})
        poller.client.lots = lots[1:]
        stats = await poller.poll_once()
        self.assertEqual((stats.vanished, stats.truncated), (1, False))
        self.assertEqual(self._states()[50], "finished")

    async def test_extended_deadline_is_picked_up_and_logged(self):
        poller = self.poller([lot(95, "Bayou (3ED, №283)", NOW + 600)])
        await poller.poll_once()
        poller.client.lots = [lot(95, "Bayou (3ED, №283)", NOW + 1500, current_bid=900)]
        with self.assertLogs("topbot.poller", level="INFO") as logs:
            stats = await poller.poll_once()
        self.assertEqual(stats.extended, 1)
        self.assertEqual(self.db.lot(95).end_ts, NOW + 1500)
        moved = [record for record in logs.records if record.getMessage() == "lot end_ts moved"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].delta_s, 900)
        self.assertEqual(moved[0].new_end_ts, NOW + 1500)

    async def test_overdue_lots_close_even_in_a_truncated_poll(self):
        lots = [lot(70 + index, f"Bayou (3ED, №283) {index}", NOW + 3600) for index in range(10)]
        lots.append(lot(80, "Bayou (3ED, №283) old", NOW - 60))
        poller = self.poller(lots)
        await poller.poll_once()
        poller.client.lots = [lot(90, "Other (XYZ)", NOW + 3600)]
        stats = await poller.poll_once()
        self.assertTrue(stats.truncated)
        self.assertEqual(self._states()[80], "finished")
        self.assertEqual(self._states()[70], "active")

    def _states(self) -> dict[int, str]:
        rows = self.db.conn.execute("SELECT id, state FROM lots ORDER BY id").fetchall()
        return {int(row["id"]): row["state"] for row in rows}

    async def test_poll_run_is_recorded(self):
        await self.poller([lot(7, "Bayou (3ED, №283)", NOW + 600)]).poll_once()
        last = self.db.last_poll("auctions")
        self.assertTrue(last.ok)
        self.assertEqual(last.items, 1)


class DatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.user = self.db.ensure_user(7, "tester")
        self.card = self.db.upsert_card("Bayou", "bayou")

    def tearDown(self) -> None:
        self.db.close()

    def test_watchlist_roundtrip(self):
        self.assertTrue(self.db.add_to_watchlist(7, self.card))
        self.assertFalse(self.db.add_to_watchlist(7, self.card))
        self.assertEqual(len(self.db.watchlist(7)), 1)
        self.assertTrue(self.db.remove_from_watchlist(7, self.card))
        self.assertEqual(self.db.watchlist(7), [])

    def test_card_upsert_is_idempotent(self):
        again = self.db.upsert_card("Bayou", "bayou", name_ru="Залив")
        self.assertEqual(again, self.card)
        self.assertEqual(len(self.db.cards_index()), 1)

    def test_notification_dedupe(self):
        first = self.db.reserve_notification(7, "auction_window", 1, 900)
        retry = self.db.reserve_notification(7, "auction_window", 1, 900)
        other_window = self.db.reserve_notification(7, "auction_window", 1, 3600)
        self.assertIsNotNone(first)
        self.assertEqual(retry, first)
        self.assertIsNotNone(other_window)
        self.assertEqual(self.db.sent_windows(7, "auction_window", 1), set())
        self.db.mark_sent(first)
        self.db.mark_sent(other_window)
        self.assertEqual(self.db.sent_windows(7, "auction_window", 1), {900, 3600})
        self.assertIsNone(self.db.reserve_notification(7, "auction_window", 1, 900))

    def test_title_hash_changes_with_title(self):
        self.assertNotEqual(title_hash("A"), title_hash("B"))


class WatchlistCountsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.card = self.db.upsert_card("Bayou", "bayou")

    def tearDown(self) -> None:
        self.db.close()

    def _lot(self, lot_id: int, end_ts: int) -> None:
        self.db.upsert_lot(make_lot(lot_id, f"Bayou test {lot_id}", end_ts, start_bid=500, current_bid=700,
                                    bid_count=1), int(time.time()))
        self.db.save_lot_cards(lot_id, [(self.card, 1.0, "exact_en")], title_hash(f"Bayou test {lot_id}"))

    def _overview(self, user_id: int = 42):
        return {card.name_en: card for card in self.db.watchlist_overview(user_id)}

    def test_only_watched_active_lots_are_counted(self):
        self.db.add_to_watchlist(42, self.card)
        self._lot(1, int(time.time()) + 3600)
        self._lot(2, int(time.time()) + 7200)
        self.assertEqual(self._overview()["Bayou"].lots_count, 2)
        self.assertEqual(self._overview()["Bayou"].next_lot_id, 1)
        self.assertEqual(self.db.watchlist_overview(99), [])

    def test_finished_lots_are_not_counted(self):
        self.db.add_to_watchlist(42, self.card)
        self._lot(3, int(time.time()) - 60)
        self.assertEqual(self._overview()["Bayou"].lots_count, 0)
        self.assertIsNone(self._overview()["Bayou"].next_lot_id)

    def test_windows_update(self):
        self.db.ensure_user(42)
        self.db.update_windows(42, (3600, 900))
        self.assertEqual(self.db.get_user(42).windows, (3600, 900))

    def test_lot_ignore_is_idempotent(self):
        self.db.ensure_user(42)
        self.db.upsert_lot(make_lot(1, "Bayou (3ED, №283)", NOW + 3600), NOW)
        self.assertTrue(self.db.ignore_lot(42, 1))
        self.assertFalse(self.db.ignore_lot(42, 1))
        self.assertEqual(self.db.ignored_lot_ids(42), {1})
        self.assertEqual(self.db.ignored_lot_ids(7), set())


class LegacyUsersMigrationTest(unittest.TestCase):
    def test_quiet_columns_are_dropped_from_an_old_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.sqlite3")
            conn = sqlite3.connect(path)
            conn.executescript(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,"
                " tz TEXT NOT NULL DEFAULT 'Europe/Moscow', lang TEXT NOT NULL DEFAULT 'ru',"
                " windows_json TEXT NOT NULL DEFAULT '[86400,10800,3600,900]',"
                " quiet_from INTEGER NOT NULL DEFAULT 1380, quiet_to INTEGER NOT NULL DEFAULT 540,"
                " new_lot_alerts INTEGER NOT NULL DEFAULT 0, muted_until INTEGER,"
                " created_at INTEGER NOT NULL, last_seen_at INTEGER);"
                "INSERT INTO users(id, created_at) VALUES(7, 0);"
            )
            conn.commit()
            conn.close()
            db = Database(path)
            try:
                columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(users)")}
                self.assertNotIn("quiet_from", columns)
                self.assertNotIn("quiet_to", columns)
                user = db.get_user(7)
                self.assertEqual(user.id, 7)
                self.assertEqual(user.windows, (86400, 3600, 900, 300))
            finally:
                db.close()


class WindowRetirementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.set_meta(WINDOWS_MIGRATION_KEY, "0")

    def tearDown(self) -> None:
        self.db.close()

    def _windows(self, user_id: int) -> tuple[int, ...]:
        return self.db.get_user(user_id).windows

    def test_three_hour_window_becomes_five_minutes(self):
        self.db.ensure_user(1)
        self.db.update_windows(1, (86400, 10800, 3600, 900))
        self.db._migrate_windows()
        self.assertEqual(self._windows(1), (86400, 3600, 900, 300))

    def test_five_minutes_are_added_even_when_three_hours_were_off(self):
        self.db.ensure_user(2)
        self.db.update_windows(2, (86400, 3600))
        self.db._migrate_windows()
        self.assertEqual(self._windows(2), (86400, 3600, 300))

    def test_migration_is_one_shot_so_a_disabled_window_stays_disabled(self):
        self.db.ensure_user(3)
        self.db._migrate_windows()
        self.db.update_windows(3, (86400, 3600))
        self.db._migrate_windows()
        self.assertEqual(self._windows(3), (86400, 3600))


class CompositionStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")

    def tearDown(self) -> None:
        self.db.close()

    def test_pending_lots_shrink_after_fetch(self):
        self.db.upsert_lot(make_lot(1, "Полноформатные земли", NOW + 3600), NOW)
        self.assertEqual(self.db.lots_needing_composition(limit=5, now=NOW), [(1, "Полноформатные земли")])
        self.db.save_composition(1, ["Bayou", "Blightstep Pathway"], NOW)
        self.assertEqual(self.db.compositions(), {1: ("Bayou", "Blightstep Pathway")})
        self.assertEqual(self.db.lots_needing_composition(limit=5, now=NOW), [])

    def test_matches_follow_composition(self):
        card = self.db.upsert_card("Bayou", "bayou")
        self.db.add_to_watchlist(42, card)
        self.db.upsert_lot(make_lot(2, "Земли сборник", NOW + 3600), NOW)
        self.assertEqual(ensure_matches(self.db), 1)
        self.assertEqual(self.db.lot_card_ids(2), [])
        self.db.save_composition(2, ["Фойл Bayou"], NOW)
        self.assertEqual(ensure_matches(self.db), 1)
        self.assertEqual(self.db.lot_card_methods(2), {card: "composition:exact_en"})

    def test_finished_lots_are_not_pending(self):
        self.db.upsert_lot(make_lot(3, "Полноформатные земли", NOW - 60), NOW)
        self.assertEqual(self.db.lots_needing_composition(limit=5, now=NOW), [])


class WatchlistOverviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.bayou = self.db.upsert_card("Bayou", "bayou")
        self.goyf = self.db.upsert_card("Tarmogoyf", "tarmogoyf", name_ru="Тармогойф")
        self.db.add_to_watchlist(42, self.bayou)
        self.db.add_to_watchlist(42, self.goyf)

    def tearDown(self) -> None:
        self.db.close()

    def _lot(self, lot_id: int, card_id: int, end_ts: int, price: int, title: str) -> None:
        self.db.upsert_lot(make_lot(lot_id, title, end_ts, start_bid=price, current_bid=price, bid_count=1),
                           int(time.time()))
        self.db.save_lot_cards(lot_id, [(card_id, 1.0, "exact_en")], title_hash(title))

    def test_counts_prices_and_nearest_end(self):
        now = int(time.time())
        self._lot(1, self.bayou, now + 3600, 900, "Bayou A")
        self._lot(2, self.bayou, now + 7200, 700, "Bayou B")
        rows = {card.name_en: card for card in self.db.watchlist_overview(42)}
        bayou = rows["Bayou"]
        self.assertEqual(bayou.lots_count, 2)
        self.assertEqual(bayou.lots_min_price, 700)
        self.assertEqual(bayou.next_end_ts, now + 3600)
        self.assertEqual(bayou.next_lot_id, 1)
        self.assertEqual(rows["Tarmogoyf"].lots_count, 0)

    def test_counts_helper(self):
        counts = self.db.counts()
        self.assertEqual(counts.users, 1)
        self.assertEqual(counts.cards, 2)


class EnsureMatchesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.card = self.db.upsert_card("Yavimaya, Cradle of Growth", "yavimaya cradle of growth")
        self.db.add_to_watchlist(42, self.card)

    def tearDown(self) -> None:
        self.db.close()

    def _insert_lot(self, lot_id: int, title: str) -> None:
        now = int(time.time())
        self.db.upsert_lot(make_lot(lot_id, title, now + 3600, start_bid=500, current_bid=500, bid_count=0), now)

    def test_matches_are_computed_for_existing_lots(self):
        self._insert_lot(550068, "Yavimaya, Cradle of Growth ( MH2 480) Foil Extended ")
        self.assertEqual(matched_cards(self.db, 550068), [])
        updated = ensure_matches(self.db)
        self.assertEqual(updated, 1)
        self.assertEqual(matched_cards(self.db, 550068), [self.card])
        self.assertEqual(ensure_matches(self.db), 0)

    def test_unrelated_lot_is_not_matched(self):
        self._insert_lot(1, "Black Lotus (Unlimited, EN)")
        ensure_matches(self.db)
        self.assertEqual(matched_cards(self.db, 1), [])


class InvalidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.db.upsert_lot(make_lot(550068, "Yavimaya, Cradle of Growth ( MH2 480) Foil Extended ",
                                    int(time.time()) + 3600, start_bid=500, current_bid=500, bid_count=0),
                           int(time.time()))

    def tearDown(self) -> None:
        self.db.close()

    def test_new_card_invalidates_previous_no_match_result(self):
        self.db.upsert_card("Tarmogoyf", "tarmogoyf")
        self.assertEqual(ensure_matches(self.db), 1)
        card = self.db.upsert_card("Yavimaya, Cradle of Growth", "yavimaya cradle of growth")
        self.db.add_to_watchlist(42, card)
        self.assertEqual(matched_cards(self.db, 550068), [])
        self.assertEqual(ensure_matches(self.db), 0)
        self.assertEqual(self.db.invalidate_lot_matches(), 1)
        ensure_matches(self.db)
        self.assertEqual(matched_cards(self.db, 550068), [card])


class MoneyFormatTest(unittest.TestCase):
    def test_commas_in_titles_survive(self):
        from topbot.messages import money, render_auction_message

        self.assertEqual(money(1488), "1\u00a0488")
        lot = make_lot(1, "Yavimaya, Cradle of Growth ( MH2 480) Foil Extended", 600,
                       start_bid=500, current_bid=1488, bid_count=5, bin_value=2000)
        text = render_auction_message(lot, 600)
        self.assertIn("Yavimaya, Cradle of Growth", text)
        self.assertIn("1\u00a0488 ₽", text)
        self.assertIn("2\u00a0000 ₽", text)


class AdminAlerterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(7, "Boss")
        self.sent: list[tuple[int, str]] = []
        self.clock = [NOW]

        async def sender(user_id: int, text: str, buttons=None) -> None:
            self.sent.append((user_id, text))

        self.sender = sender
        self.alerter = AdminAlerter(self.db, sender, admin_usernames=("boss",), clock=lambda: self.clock[0])

    def tearDown(self) -> None:
        self.db.close()

    async def test_failure_streak_alerts_admin_resolved_by_username(self):
        for _ in range(FAILURE_ALERT_THRESHOLD - 1):
            await self.alerter.on_failure("boom")
        self.assertEqual(self.sent, [])
        await self.alerter.on_failure("boom")
        self.assertEqual([user_id for user_id, _ in self.sent], [7])
        self.assertIn(f"{FAILURE_ALERT_THRESHOLD} опроса", self.sent[0][1])
        self.assertIn("boom", self.sent[0][1])

    async def test_recovery_is_reported_once(self):
        for _ in range(FAILURE_ALERT_THRESHOLD):
            await self.alerter.on_failure("boom")
        await self.alerter.on_success(PollStats(lots=400))
        await self.alerter.on_success(PollStats(lots=400))
        self.assertEqual(len(self.sent), 2)
        self.assertIn("восстановился", self.sent[1][1])

    async def test_cooldown_suppresses_repeated_failure_alerts(self):
        for _ in range(2 * FAILURE_ALERT_THRESHOLD):
            await self.alerter.on_failure("boom")
        self.assertEqual(len(self.sent), 1)
        self.clock[0] += ADMIN_ALERT_COOLDOWN_S
        for _ in range(FAILURE_ALERT_THRESHOLD):
            await self.alerter.on_failure("boom")
        self.assertEqual(len(self.sent), 2)

    async def test_empty_streak_alerts_once(self):
        await self.alerter.on_success(PollStats(lots=400))
        for _ in range(EMPTY_ALERT_THRESHOLD - 1):
            await self.alerter.on_success(PollStats(lots=0))
        self.assertEqual(self.sent, [])
        await self.alerter.on_success(PollStats(lots=0))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("0 лотов", self.sent[0][1])
        await self.alerter.on_success(PollStats(lots=0))
        self.assertEqual(len(self.sent), 1)

    async def test_non_empty_poll_resets_empty_streak(self):
        await self.alerter.on_success(PollStats(lots=0))
        await self.alerter.on_success(PollStats(lots=400))
        await self.alerter.on_success(PollStats(lots=0))
        self.assertEqual(self.sent, [])

    async def test_two_truncated_polls_alert_once(self):
        await self.alerter.on_success(PollStats(lots=400))
        await self.alerter.on_success(PollStats(lots=200, truncated=True))
        self.assertEqual(self.sent, [])
        await self.alerter.on_success(PollStats(lots=200, truncated=True))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("меньше половины лотов", self.sent[0][1])
        await self.alerter.on_success(PollStats(lots=200, truncated=True))
        self.assertEqual(len(self.sent), 1)

    async def test_complete_poll_resets_truncated_streak(self):
        await self.alerter.on_success(PollStats(lots=200, truncated=True))
        await self.alerter.on_success(PollStats(lots=400))
        await self.alerter.on_success(PollStats(lots=200, truncated=True))
        self.assertEqual(self.sent, [])

    async def test_unknown_admin_username_is_skipped(self):
        alerter = AdminAlerter(self.db, self.sender, admin_usernames=("nobody",), clock=lambda: self.clock[0])
        self.assertEqual(alerter.recipients(), [])
        for _ in range(3):
            await alerter.on_failure("boom")
        self.assertEqual(self.sent, [])


class EnsureUserTest(unittest.TestCase):
    def test_username_survives_ensure_user_without_it(self):
        db = Database(":memory:")
        db.ensure_user(42, "tester", "Tester")
        db.ensure_user(42)
        row = db.conn.execute("SELECT username, first_name FROM users WHERE id=42").fetchone()
        self.assertEqual((row["username"], row["first_name"]), ("tester", "Tester"))
        self.assertEqual(db.user_id_by_username("tester"), 42)
        db.close()


class MatchVersionTest(unittest.TestCase):
    def test_new_rules_invalidate_cached_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "topbot.sqlite3")
            db = Database(path)
            card_id = db.upsert_card("Bayou", "bayou")
            db.upsert_lot(make_lot(1, "Bayou test", NOW), NOW)
            db.save_lot_cards(1, [(card_id, 1.0, "exact_en")], title_hash("Bayou test"))
            self.assertEqual(db.lot_card_ids(1), [card_id])
            db.conn.execute("UPDATE meta SET value='1' WHERE key='match_version'")
            db.close()

            db = Database(path)
            row = db.conn.execute("SELECT matched_hash FROM lots WHERE id=1").fetchone()
            self.assertIsNone(row["matched_hash"])
            current = db.conn.execute("SELECT value FROM meta WHERE key='match_version'").fetchone()
            self.assertEqual(int(current["value"]), MATCH_VERSION)
            db.close()

    def test_stored_version_keeps_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "topbot.sqlite3")
            db = Database(path)
            card_id = db.upsert_card("Bayou", "bayou")
            db.upsert_lot(make_lot(1, "Bayou test", NOW), NOW)
            db.save_lot_cards(1, [(card_id, 1.0, "exact_en")], title_hash("Bayou test"))
            db.close()

            db = Database(path)
            row = db.conn.execute("SELECT matched_hash FROM lots WHERE id=1").fetchone()
            self.assertEqual(row["matched_hash"], title_hash("Bayou test"))
            db.close()


if __name__ == "__main__":
    unittest.main()
