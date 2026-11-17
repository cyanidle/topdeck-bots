from __future__ import annotations

import re
import time
import unittest

try:
    import aiogram  # noqa: F401
    HAS_AIOGRAM = True
except ImportError:  # pragma: no cover
    HAS_AIOGRAM = False

from topbot.catalog import CardCatalog
from topbot.db import Database, title_hash
from topbot.matching import normalize
from topbot.models import Card, Lot, Offer


class FakeScryfall:
    def __init__(self, multilingual: dict[str, list[Card]] | None = None, autocomplete: list[str] | None = None,
                 fuzzy: dict[str, Card] | None = None, localized: dict[str, str] | None = None):
        self.multilingual = multilingual or {}
        self.autocomplete_names = autocomplete or []
        self.fuzzy_cards = fuzzy or {}
        self.localized = localized or {}
        self.multilingual_calls: list[str] = []
        self.autocomplete_calls = 0
        self.fuzzy_calls: list[str] = []
        self.localized_calls: list[str] = []

    async def named_fuzzy(self, query: str) -> Card | None:
        self.fuzzy_calls.append(query)
        return self.fuzzy_cards.get(query)

    async def named_exact(self, query: str) -> Card | None:
        self.fuzzy_calls.append(query)
        return self.fuzzy_cards.get(query)

    async def search_multilingual(self, query: str, limit: int = 5) -> list[Card]:
        self.multilingual_calls.append(query)
        return self.multilingual.get(query, [])[:limit]

    async def localized_name(self, name_en: str) -> str | None:
        self.localized_calls.append(name_en)
        return self.localized.get(name_en)

    async def autocomplete(self, query: str) -> list[str]:
        self.autocomplete_calls += 1
        prefix = query.lower()[:4]
        return [name for name in self.autocomplete_names if prefix in name.lower()]


class FakeTopdeck:
    def __init__(self, offers: list[Offer], error: Exception | None = None,
                 truncate_over: int | None = None):
        self.offers = offers
        self.error = error
        self.truncate_over = truncate_over
        self.calls = 0
        self.batches: list[list[str]] = []

    async def search_singles(self, names):
        self.calls += 1
        self.batches.append(list(names))
        if self.error is not None:
            raise self.error
        truncated = self.truncate_over is not None and len(names) > self.truncate_over
        return self.offers, truncated


def make_offer(eng_name: str, rus_name: str | None = None, **overrides) -> Offer:
    fields = dict(eng_name=eng_name, rus_name=rus_name, name=eng_name, source="topdeck", seller_name="seller",
                  seller_id=1, city="Москва", cost=100, qty=1, url="https://topdeck.ru/offer", line="",
                  stamp=None)
    fields.update(overrides)
    return Offer(**fields)


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class CandidatesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.catalog = CardCatalog(self.db)
        self.catalog.store_names(["Tarmogoyf", "Tarmogoyf Nest", "Bayou", "Snapcaster Mage"])
        bot_module.ctx.db = self.db
        bot_module.ctx.catalog = self.catalog
        bot_module.ctx.scryfall = None
        bot_module.ctx.topdeck = None

    def tearDown(self) -> None:
        self.db.close()
        self.bot.ctx.scryfall = None
        self.bot.ctx.topdeck = None

    async def test_english_suggestions_from_catalog(self):
        names = await self.bot._candidates("tarmo")
        self.assertEqual(names[0], "Tarmogoyf")
        self.assertIn("Tarmogoyf Nest", names)

    async def test_russian_query_resolved_through_scryfall(self):
        self.bot.ctx.scryfall = FakeScryfall(multilingual={
            "удар молнии*": [Card(name_en="Lightning Bolt", name_ru="Удар Молнии")],
        })
        names = await self.bot._candidates("удар молнии")
        self.assertEqual(names, ["Lightning Bolt"])
        self.assertEqual(self.bot.ctx.scryfall.multilingual_calls[0], "удар молнии*")
        self.assertIn("Удар Молнии", self.catalog.search("удар"))

    async def test_scryfall_localized_prefix_matches_partial_name(self):
        self.bot.ctx.scryfall = FakeScryfall(multilingual={
            "тармо*": [Card(name_en="Tarmogoyf", name_ru="Тармогойф")],
        })
        self.assertEqual(await self.bot._candidates("тармо"), ["Tarmogoyf"])

    async def test_topdeck_is_last_resort_only(self):
        self.bot.ctx.scryfall = FakeScryfall(multilingual={}, fuzzy={})
        self.bot.ctx.topdeck = FakeTopdeck([make_offer("Lightning Bolt", "Удар Молнии")])
        self.assertEqual(await self.bot._candidates("удар молнии"), ["Lightning Bolt"])
        self.assertEqual(self.bot.ctx.topdeck.calls, 1)
        self.assertIn("Lightning Bolt", self.catalog.search("lightning"))

    async def test_topdeck_fallback_ignores_the_query_echo(self):
        self.bot.ctx.scryfall = FakeScryfall(multilingual={}, fuzzy={})
        self.bot.ctx.topdeck = FakeTopdeck([make_offer("удар молнии", "Удар Молнии", name="Lightning Bolt")])
        self.assertEqual(await self.bot._candidates("удар молнии"), ["Lightning Bolt"])
        self.assertIn("Lightning Bolt", self.catalog.search("lightning"))
        self.assertNotIn("удар", [name.lower() for name in self.catalog.search("lightning")])

    async def test_topdeck_not_used_when_scryfall_found_something(self):
        self.bot.ctx.scryfall = FakeScryfall(multilingual={
            "удар*": [Card(name_en="Lightning Bolt", name_ru="Удар Молнии")],
        })
        self.bot.ctx.topdeck = FakeTopdeck([make_offer("Lightning Bolt", "Удар Молнии")])
        await self.bot._candidates("удар")
        self.assertEqual(self.bot.ctx.topdeck.calls, 0)

    async def test_latin_typo_uses_scryfall_autocomplete(self):
        self.bot.ctx.scryfall = FakeScryfall(autocomplete=["Snapcaster Mage"])
        self.assertEqual(await self.bot._candidates("snapcastr"), ["Snapcaster Mage"])
        self.assertEqual(self.bot.ctx.scryfall.autocomplete_calls, 1)

    async def test_heavier_typo_falls_back_to_fuzzy(self):
        self.bot.ctx.scryfall = FakeScryfall(fuzzy={"tarmogoyff": Card(name_en="Tarmogoyf", name_ru="Тармогойф")})
        self.assertEqual(await self.bot._candidates("tarmogoyff"), ["Tarmogoyf"])
        self.assertEqual(self.bot.ctx.scryfall.fuzzy_calls, ["tarmogoyff"])

    async def test_russian_fuzzy_when_multilingual_is_empty(self):
        self.bot.ctx.scryfall = FakeScryfall(fuzzy={"удар молнии": Card(name_en="Lightning Bolt",
                                                                        name_ru="Удар Молнии")})
        self.assertEqual(await self.bot._candidates("удар молнии"), ["Lightning Bolt"])

    async def test_exact_match_detection(self):
        names = await self.bot._candidates("bayou")
        exact = next((name for name in names if normalize(name) == normalize("bayou")), None)
        self.assertEqual(exact, "Bayou")

    async def test_partial_query_has_no_exact(self):
        names = await self.bot._candidates("tarmo")
        exact = next((name for name in names if normalize(name) == normalize("tarmo")), None)
        self.assertIsNone(exact)

    async def test_empty_everything(self):
        self.bot.ctx.scryfall = FakeScryfall()
        self.assertEqual(await self.bot._candidates("zzzz"), [])


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class WatchlistViewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.card = self.db.upsert_card("Tarmogoyf", "tarmogoyf", name_ru="Тармогойф")
        self.db.add_to_watchlist(42, self.card)
        self.topdeck = FakeTopdeck([])
        bot_module.ctx.db = self.db
        bot_module.ctx.topdeck = self.topdeck
        bot_module.ctx.bot_username = "topdeck_test_bot"

    def tearDown(self) -> None:
        self.db.close()
        self.bot.ctx.bot_username = ""
        self.bot.ctx.topdeck = None

    async def test_view_uses_the_requested_user_not_the_sender(self):
        text = await self.bot._watchlist_view(42)
        self.assertIn("Tarmogoyf", text)
        self.assertIn("📋 <b>Watchlist</b>", text)
        self.assertNotIn("•", text)

    async def test_card_is_bold_title_with_actions_on_their_own_line(self):
        block = (await self.bot._watchlist_view(42)).split("\n\n")[1].splitlines()
        self.assertEqual(block[0], "<b>Tarmogoyf</b>")
        self.assertEqual(block[1], "лотов нет")
        self.assertEqual(block[2], "💰 Цены у продавцов: нет предложений")
        self.assertTrue(block[3].startswith("› "), block[3])

    async def test_actions_are_text_links(self):
        text = await self.bot._watchlist_view(42)
        self.assertIn('href="https://t.me/topdeck_test_bot?start=price_', text)
        self.assertIn('href="https://t.me/topdeck_test_bot?start=rm_', text)
        self.assertIn(f"?start=price_{self.card}", text)
        self.assertIn(f"?start=rm_{self.card}", text)
        self.assertIn(">цены</a>", text)
        self.assertIn(">удалить</a>", text)

    async def test_card_name_links_to_the_nearest_lot(self):
        self.db.upsert_lot(Lot(id=7, title="Bayou test", end_ts=int(time.time()) + 3600,
                               start_ts=int(time.time()) - 3600, start_bid=500, current_bid=500, bid_count=1,
                               bin_value=0, seller_id=1, seller_name="seller", seller_city="Москва",
                               seller_refs=1, shipping_quick="Москва, почта", image_url=None,
                               url="https://topdeck.ru/lot/7"), int(time.time()))
        self.db.save_lot_cards(7, [(self.card, 1.0, "exact_en")], title_hash("Bayou test"))
        text = await self.bot._watchlist_view(42)
        self.assertIn('<b><a href="https://topdeck.ru/apps/toptrade/auctions/7">Tarmogoyf</a></b>', text)

    async def test_card_names_are_html_escaped(self):
        risky = self.db.upsert_card("R&D's Secret Lair", "r d s secret lair")
        self.db.add_to_watchlist(42, risky)
        text = await self.bot._watchlist_view(42)
        self.assertIn("R&amp;D's Secret Lair", text)
        self.assertNotIn("&amp;amp;", text)

    async def test_view_for_unknown_user_is_empty(self):
        text = await self.bot._watchlist_view(777)
        self.assertIn("Watchlist пуст", text)
        self.assertNotIn("t.me", text)
        self.assertEqual(self.topdeck.calls, 0)


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class WatchlistInlinePricesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.goyf = self.db.upsert_card("Tarmogoyf", "tarmogoyf", name_ru="Тармогойф")
        self.bolt = self.db.upsert_card("Lightning Bolt", "lightning bolt")
        self.db.add_to_watchlist(42, self.goyf)
        self.db.add_to_watchlist(42, self.bolt)
        self.topdeck = FakeTopdeck([])
        bot_module.ctx.db = self.db
        bot_module.ctx.topdeck = self.topdeck
        bot_module.ctx.bot_username = "topdeck_test_bot"

    def tearDown(self) -> None:
        self.bot.ctx.db = None
        self.bot.ctx.topdeck = None
        self.bot.ctx.bot_username = ""
        self.db.close()

    def _use(self, offers: list[Offer], error: Exception | None = None,
             truncate_over: int | None = None) -> FakeTopdeck:
        self.topdeck = FakeTopdeck(offers, error=error, truncate_over=truncate_over)
        self.bot.ctx.topdeck = self.topdeck
        return self.topdeck

    async def test_cached_prices_are_rendered_without_network(self):
        synced_at = int(time.time()) - 120
        self.db.save_card_offers(self.goyf, [make_offer("Tarmogoyf", seller_name="Alphonse", cost=700),
                                            make_offer("Tarmogoyf", seller_name="Bernard", cost=800)], synced_at)
        self.db.save_card_offers(self.bolt, [], synced_at)
        self._use([])
        text = await self.bot._watchlist_view(42)
        self.assertIn("Цены у продавцов: 700 ₽ Alphonse · 800 ₽ Bernard", text)
        self.assertEqual(self.topdeck.calls, 0)
        self.assertIn("💰 Прайсы из кэша: 2 мин назад", text)

    async def test_only_cards_missing_from_the_cache_are_requested(self):
        self.db.save_card_offers(self.goyf, [make_offer("Tarmogoyf", seller_name="Alphonse", cost=700)], 0)
        self._use([make_offer("Lightning Bolt", seller_name="Bernard", cost=50)])
        text = await self.bot._watchlist_view(42)
        self.assertEqual(self.topdeck.batches, [["Lightning Bolt"]])
        self.assertIn("700 ₽ Alphonse", text)
        self.assertIn("50 ₽ Bernard", text)

    async def test_at_most_three_cheapest_per_card_in_price_order(self):
        self._use([make_offer("Tarmogoyf", "Тармогойф", seller_name="Alphonse", cost=900),
                   make_offer("Tarmogoyf", "Тармогойф", seller_name="Bernard", cost=700),
                   make_offer("Tarmogoyf", "Тармогойф", seller_name="Cornelius", cost=800),
                   make_offer("Tarmogoyf", "Тармогойф", seller_name="Desmond", cost=1200)])
        text = await self.bot._watchlist_view(42)
        self.assertIn("700 ₽ Bernard", text)
        self.assertIn("Цены у продавцов: 700 ₽ Bernard", text)
        self.assertIn("800 ₽ Cornelius", text)
        self.assertIn("900 ₽ Alphonse", text)
        self.assertNotIn("Desmond", text)
        self.assertLess(text.index("Bernard"), text.index("Cornelius"))
        self.assertLess(text.index("Cornelius"), text.index("Alphonse"))

    async def test_offer_is_matched_by_the_card_name_of_the_listing(self):
        self._use([make_offer("Тармогойф", seller_name="Rus", cost=650, name="Тармогойф")])
        text = await self.bot._watchlist_view(42)
        self.assertIn("650 ₽ Rus", text)

    async def test_foreign_card_from_a_fuzzy_search_is_not_shown(self):
        self._use([make_offer("Tarmogoyf", seller_name="Curse", cost=53, name="Curse of Shallow Graves")])
        text = await self.bot._watchlist_view(42)
        self.assertNotIn("Curse", text)

    async def test_prices_sit_on_their_own_line_between_lots_and_actions(self):
        self._use([make_offer("Tarmogoyf", seller_name="Alphonse", cost=700)])
        block = next(part for part in (await self.bot._watchlist_view(42)).split("\n\n")
                     if "Tarmogoyf" in part).splitlines()
        self.assertEqual(block[0], "<b>Tarmogoyf</b>")
        self.assertEqual(block[1], "лотов нет")
        self.assertEqual(block[2], "💰 Цены у продавцов: 700 ₽ Alphonse")
        self.assertTrue(block[3].startswith("› "), block[3])

    async def test_cheapest_offer_per_seller_is_kept_once(self):
        self._use([make_offer("Tarmogoyf", seller_name="Alphonse", cost=900),
                   make_offer("Tarmogoyf", seller_name="Alphonse", cost=700)])
        text = await self.bot._watchlist_view(42)
        self.assertEqual(text.count("Alphonse"), 1)
        self.assertIn("700 ₽ Alphonse", text)
        self.assertNotIn("900", text)

    async def test_card_without_offers_has_no_price_line(self):
        self._use([make_offer("Tarmogoyf", seller_name="Alphonse", cost=700)])
        text = await self.bot._watchlist_view(42)
        self.assertEqual(text.count("₽"), 1)

    async def test_zero_and_foreign_offers_are_ignored(self):
        self._use([make_offer("Tarmogoyf", seller_name="Free", cost=0),
                   make_offer("Bayou", seller_name="Other", cost=100)])
        text = await self.bot._watchlist_view(42)
        self.assertNotIn("₽", text)
        self.assertNotIn("Other", text)

    async def test_long_seller_name_is_truncated(self):
        self._use([make_offer("Tarmogoyf", seller_name="ОченьДлинноеИмяПродавца", cost=700)])
        text = await self.bot._watchlist_view(42)
        label = [line for line in text.splitlines() if "700" in line][0].split("700 ₽")[1].strip()
        self.assertTrue(label.endswith("…"), label)
        self.assertLessEqual(len(label), 18)

    async def test_cards_are_requested_in_batches(self):
        for index in range(7):
            card_id = self.db.upsert_card(f"Filler {index}", f"filler {index}")
            self.db.add_to_watchlist(42, card_id)
        await self.bot._watchlist_view(42)
        self.assertEqual(len(self.topdeck.batches), 2)
        self.assertEqual([len(batch) for batch in self.topdeck.batches], [8, 1])

    async def test_network_failure_keeps_the_list_and_warns(self):
        self._use([], error=OSError("tunnel is down"))
        with self.assertLogs("topbot.bot", level="WARNING") as logs:
            text = await self.bot._watchlist_view(42)
        self.assertIn("⚠️ цены у продавцов сейчас недоступны", text)
        self.assertIn("Tarmogoyf", text)
        self.assertTrue(any("card prices fetch failed" in line for line in logs.output))

    async def test_truncated_batch_is_split_in_halves(self):
        for index in range(6):
            card_id = self.db.upsert_card(f"Filler {index}", f"filler {index}")
            self.db.add_to_watchlist(42, card_id)
        self._use([make_offer("Tarmogoyf", seller_name="Alphonse", cost=700)], truncate_over=4)
        with self.assertLogs("topbot.prices", level="WARNING") as logs:
            text = await self.bot._watchlist_view(42)
        self.assertEqual([len(batch) for batch in self.topdeck.batches], [8, 4, 4])
        self.assertIn("700 ₽ Alphonse", text)
        self.assertTrue(any("splitting batch" in line for line in logs.output))

    async def test_truncated_single_card_is_not_split(self):
        self.db.remove_from_watchlist(42, self.bolt)
        self._use([make_offer("Tarmogoyf", seller_name="Alphonse", cost=700)], truncate_over=0)
        with self.assertLogs("topbot.prices", level="WARNING"):
            text = await self.bot._watchlist_view(42)
        self.assertEqual(self.topdeck.calls, 1)
        self.assertIn("700 ₽ Alphonse", text)

    async def test_cmd_list_sends_typing_before_prices(self):
        actions: list[tuple[int, str]] = []
        sent: list[str] = []

        class FakeBot:
            async def send_chat_action(self, chat_id, action):
                actions.append((chat_id, action))

        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester"})()
            chat = type("C", (), {"id": 5})()
            bot = FakeBot()

            async def answer(self, text, **kwargs):
                sent.append(text)

        self._use([make_offer("Tarmogoyf", seller_name="Alphonse", cost=700)])
        await self.bot.cmd_list(FakeMessage())
        self.assertEqual(actions, [(5, "typing")])
        self.assertIn("700 ₽ Alphonse", sent[0])

    async def test_typing_failure_does_not_break_the_list(self):
        from aiogram.exceptions import TelegramBadRequest

        class FakeBot:
            async def send_chat_action(self, chat_id, action):
                raise TelegramBadRequest(method=None, message="chat not found")

        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester"})()
            chat = type("C", (), {"id": 5})()
            bot = FakeBot()

            async def answer(self, text, **kwargs):
                self.sent = text

        message = FakeMessage()
        with self.assertLogs("topbot.bot", level="INFO") as logs:
            await self.bot.cmd_list(message)
        self.assertIn("Tarmogoyf", message.sent)
        self.assertTrue(any("chat action failed" in line for line in logs.output))


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class ExpandItemsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.catalog = CardCatalog(self.db)
        self.catalog.store_names([
            "Yavimaya, Cradle of Growth", "Lightning Bolt", "Snapcaster Mage", "Uro, Titan of Nature's Wrath",
        ])
        bot_module.ctx.db = self.db
        bot_module.ctx.catalog = self.catalog
        bot_module.ctx.scryfall = None
        bot_module.ctx.topdeck = None

    def tearDown(self) -> None:
        self.db.close()

    async def test_comma_inside_card_name_is_kept(self):
        expanded, cache = await self.bot._expand_items(["yavimaya, cradle of growth"])
        self.assertEqual(expanded, ["yavimaya, cradle of growth"])
        self.assertIn("Yavimaya, Cradle of Growth", cache["yavimaya, cradle of growth"])

    async def test_uro_comma_name_is_kept(self):
        expanded, _ = await self.bot._expand_items(["uro, titan of nature's wrath"])
        self.assertEqual(len(expanded), 1)

    async def test_comma_list_is_split_when_no_combined_match(self):
        expanded, _ = await self.bot._expand_items(["Lightning Bolt, Snapcaster Mage"])
        self.assertEqual(expanded, ["Lightning Bolt", "Snapcaster Mage"])

    def test_add_split_handles_newlines_and_semicolons_but_not_commas(self):
        parts = [part.strip() for part in self.bot.ADD_SPLIT.split("Lightning Bolt\nSnapcaster Mage; Bayou") if part.strip()]
        self.assertEqual(parts, ["Lightning Bolt", "Snapcaster Mage", "Bayou"])
        self.assertEqual(self.bot.ADD_SPLIT.split("Yavimaya, Cradle of Growth"), ["Yavimaya, Cradle of Growth"])

    async def test_plain_item_passes_through(self):
        expanded, cache = await self.bot._expand_items(["tarmo"])
        self.assertEqual(expanded, ["tarmo"])
        self.assertEqual(cache, {})


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class PickCancelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        bot_module.ctx.db = self.db
        self.state = FSMContext(storage=MemoryStorage(),
                                key=StorageKey(bot_id=1, chat_id=1, user_id=42))

        class FakeMessage:
            def __init__(self) -> None:
                self.edited: tuple[str, object] | None = None
                self.sent: list[str] = []

            async def edit_text(self, text, reply_markup=None, disable_web_page_preview=None):
                self.edited = (text, reply_markup)

            async def answer(self, text, **kwargs):
                self.sent.append(text)

        class FakeCallback:
            def __init__(self, data: str) -> None:
                self.data = data
                self.from_user = type("U", (), {"id": 42, "username": "tester"})()
                self.message = FakeMessage()
                self.answers: list[str | None] = []

            async def answer(self, text=None, **kwargs):
                self.answers.append(text)

        self.FakeCallback = FakeCallback

    def tearDown(self) -> None:
        self.db.close()

    async def test_cancel_removes_suggestion_list(self):
        await self.state.update_data(candidates=["Tarmogoyf", "Tarmogoyf Nest"])
        callback = self.FakeCallback("pick:cancel")
        await self.bot.cb_pick(callback, self.state)
        self.assertIsNotNone(callback.message.edited)
        text, markup = callback.message.edited
        self.assertIn("Отменено", text)
        self.assertIsNone(markup)
        self.assertIsNone(await self.state.get_state())

    async def test_stale_choice_removes_keyboard(self):
        await self.state.update_data(candidates=[])
        callback = self.FakeCallback("pick:3")
        await self.bot.cb_pick(callback, self.state)
        text, markup = callback.message.edited
        self.assertIn("устарел", text)
        self.assertIsNone(markup)


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class MenuCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_menu_command_restores_reply_keyboard(self):
        from topbot import bot as bot_module

        sent: list[tuple[str, object]] = []

        class FakeMessage:
            async def answer(self, text, **kwargs):
                sent.append((text, kwargs.get("reply_markup")))

        await bot_module.cmd_menu(FakeMessage())
        text, markup = sent[0]
        self.assertIn("/menu", text)
        self.assertTrue(markup.is_persistent)
        self.assertEqual([button.text for row in markup.keyboard for button in row],
                         list(bot_module.MENU_TEXTS))

    def test_menu_command_is_published(self):
        from topbot import bot as bot_module

        self.assertIn("menu", [command.command for command in bot_module.COMMANDS])


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class UpdateLogMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_logs_and_passes_the_update_through(self):
        from aiogram.types import Chat, Message, Update, User
        from topbot import bot as bot_module

        seen: list[object] = []

        async def handler(event, data):
            seen.append(event)
            return "ok"

        update = Update(update_id=1, message=Message(
            message_id=1, date=0, chat=Chat(id=1, type="private"),
            from_user=User(id=42, is_bot=False, first_name="t"), text="/menu"))

        with self.assertLogs("topbot.bot", level="INFO") as logs:
            result = await bot_module.UpdateLog()(handler, update, {})

        self.assertEqual(result, "ok")
        self.assertEqual(seen, [update])
        self.assertTrue(any("update received" in line for line in logs.output))
        self.assertTrue(any("update handled" in line for line in logs.output))


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class CommandTextTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_help_only_mention_published_commands(self):
        from topbot import bot as bot_module

        sent: list[str] = []

        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester", "first_name": "Tester"})()
            text = "/start"

            async def answer(self, text, **kwargs):
                sent.append(text)

        db = Database(":memory:")
        bot_module.ctx.db = db
        try:
            await bot_module.cmd_start(FakeMessage())
            await bot_module.cmd_help(FakeMessage())
        finally:
            bot_module.ctx.db = None
            db.close()

        self.assertEqual(len(sent), 2)
        known = {command.command for command in bot_module.COMMANDS}
        for text in sent:
            mentioned = set(re.findall(r"/([a-z_]+)", text))
            self.assertTrue(mentioned <= known, f"unknown commands {mentioned - known} in {text!r}")


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class AddCardRussianNameTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.catalog = CardCatalog(self.db)
        bot_module.ctx.db = self.db
        bot_module.ctx.catalog = self.catalog
        bot_module.ctx.topdeck = None

    def tearDown(self) -> None:
        self.bot.ctx.scryfall = None
        self.bot.ctx.topdeck = None
        self.db.close()

    async def test_english_lookup_stores_russian_name(self):
        self.bot.ctx.scryfall = FakeScryfall(
            fuzzy={"ranger captain of eos": Card(name_en="Ranger-Captain of Eos")},
            localized={"Ranger-Captain of Eos": "Капитан Лесничих Эоса"},
        )
        is_new, name = await self.bot._add_card(42, "ranger captain of eos")
        self.assertTrue(is_new)
        self.assertEqual(name, "Ranger-Captain of Eos")
        row = self.db.conn.execute("SELECT name_ru FROM cards WHERE name_norm='ranger captain of eos'").fetchone()
        self.assertEqual(row["name_ru"], "Капитан Лесничих Эоса")
        self.assertEqual(self.bot.ctx.scryfall.localized_calls, ["Ranger-Captain of Eos"])

    async def test_existing_russian_name_is_not_requested_again(self):
        self.bot.ctx.scryfall = FakeScryfall(
            fuzzy={"tarmogoyf": Card(name_en="Tarmogoyf", name_ru="Тармогойф")},
        )
        await self.bot._add_card(42, "tarmogoyf")
        self.assertEqual(self.bot.ctx.scryfall.localized_calls, [])


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class MuteCommandTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.bot.ctx.db = self.db

    def tearDown(self) -> None:
        self.bot.ctx.db = None
        self.db.close()

    def _message(self, text: str, sent: list[str]):
        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester", "first_name": "Tester"})()

            async def answer(self, answer_text, **kwargs):
                sent.append(answer_text)

        message = FakeMessage()
        message.text = text
        return message

    async def test_period_sets_mute(self):
        sent: list[str] = []
        await self.bot.cmd_mute(self._message("/mute 1h", sent))
        muted_until = self.db.get_user(42).muted_until
        self.assertLessEqual(muted_until, int(time.time()) + 3600)
        self.assertGreaterEqual(muted_until, int(time.time()) + 3600 - 5)
        self.assertIn("на паузе", sent[0])

    async def test_off_clears_mute(self):
        self.db.update_mute(42, int(time.time()) + 3600)
        sent: list[str] = []
        await self.bot.cmd_mute(self._message("/mute off", sent))
        self.assertIsNone(self.db.get_user(42).muted_until)
        self.assertIn("Пауза снята", sent[0])

    async def test_unknown_period_keeps_state(self):
        sent: list[str] = []
        await self.bot.cmd_mute(self._message("/mute 3h", sent))
        self.assertIsNone(self.db.get_user(42).muted_until)
        self.assertIn("/mute 1h", sent[0])

    async def test_empty_argument_shows_help(self):
        sent: list[str] = []
        await self.bot.cmd_mute(self._message("/mute", sent))
        self.assertIsNone(self.db.get_user(42).muted_until)
        self.assertIn("/mute off", sent[0])

    async def test_settings_show_pause(self):
        self.db.update_mute(42, int(time.time()) + 3600)
        text, _ = self.bot._settings_view(42)
        self.assertIn("на паузе до", text)


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class LivePricesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.bot.ctx.db = self.db
        self.topdeck = FakeTopdeck([])
        self.bot.ctx.topdeck = self.topdeck

    def tearDown(self) -> None:
        self.bot.ctx.db = None
        self.bot.ctx.topdeck = None
        self.db.close()

    def _message(self):
        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester", "first_name": "Tester"})()

            def __init__(self):
                self.sent: list[str] = []

            async def answer(self, text, **kwargs):
                self.sent.append(text)

        return FakeMessage()

    async def test_exact_group_is_shown_for_a_card_outside_the_catalogue(self):
        self.topdeck.offers = [make_offer("Shallow Grave", seller_name="Sol", cost=11550, name="Shallow Grave"),
                               make_offer("Shallow Grave", seller_name="Curse", cost=53,
                                          name="Curse of Shallow Graves")]
        message = self._message()
        await self.bot._send_prices(message, "Shallow Grave")
        self.assertIn("11\u00a0550 ₽ ×1 — Sol", message.sent[0])
        self.assertNotIn("Curse", message.sent[0])

    async def test_ambiguous_query_lists_candidates_instead_of_foreign_prices(self):
        self.topdeck.offers = [make_offer("Eladamri", seller_name="A", cost=197, name="Eladamri's Call"),
                               make_offer("Eladamri", seller_name="B", cost=600, name="Eladamri, Korvecdal"),
                               make_offer("Eladamri", seller_name="C", cost=180, name="Eladamri, Lord of Leaves")]
        message = self._message()
        await self.bot._send_prices(message, "Eladamri")
        text = message.sent[0]
        self.assertIn("Точного совпадения по «Eladamri» нет", text)
        self.assertIn("• Eladamri, Lord of Leaves — от 180 ₽", text)
        self.assertIn("• Eladamri's Call — от 197 ₽", text)
        self.assertIn("• Eladamri, Korvecdal — от 600 ₽", text)
        self.assertLess(text.index("180 ₽"), text.index("197 ₽"))
        self.assertLess(text.index("197 ₽"), text.index("600 ₽"))

    async def test_candidate_list_is_capped(self):
        self.topdeck.offers = [make_offer("x", seller_name=f"S{index}", cost=100 + index,
                                          name=f"Card {index}") for index in range(8)]
        message = self._message()
        await self.bot._send_prices(message, "x")
        self.assertEqual(message.sent[0].count("• "), self.bot.SUGGEST_LIMIT)

    async def test_empty_answer_says_nothing_found(self):
        message = self._message()
        await self.bot._send_prices(message, "Shallow Grave")
        self.assertIn("Предложений по «Shallow Grave» не нашлось", message.sent[0])


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class CardPriceButtonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.card = self.db.upsert_card("Tarmogoyf", "tarmogoyf", name_ru="Тармогойф")
        self.db.add_to_watchlist(42, self.card)
        bot_module.ctx.db = self.db
        bot_module.ctx.topdeck = FakeTopdeck([
            make_offer("Tarmogoyf", "Тармогойф", seller_name="Umezawa", cost=300),
        ])

    def tearDown(self) -> None:
        self.bot.ctx.topdeck = None
        self.bot.ctx.db = None
        self.db.close()

    async def test_list_has_price_link_for_every_card(self):
        self.bot.ctx.bot_username = "topdeck_test_bot"
        try:
            text = await self.bot._watchlist_view(42)
        finally:
            self.bot.ctx.bot_username = ""
        self.assertIn("Tarmogoyf", text)
        self.assertIn(f"https://t.me/topdeck_test_bot?start=price_{self.card}", text)
        self.assertIn(f"https://t.me/topdeck_test_bot?start=rm_{self.card}", text)

    async def test_price_button_asks_topdeck_for_seller_offers(self):
        sent: list[str] = []
        answers: list[str | None] = []

        class FakeMessage:
            async def answer(self, text, **kwargs):
                sent.append(text)

        class FakeCallback:
            def __init__(self) -> None:
                self.data = f"price:{self_card_id}"
                self.from_user = type("U", (), {"id": 42, "username": "tester"})()
                self.message = FakeMessage()

            async def answer(self, text=None, **kwargs):
                answers.append(text)

        self_card_id = self.card
        callback = FakeCallback()
        await self.bot.cb_card_price(callback)

        self.assertEqual(self.bot.ctx.topdeck.calls, 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("Tarmogoyf", sent[0])
        self.assertIn("300 ₽", sent[0])
        self.assertIn("Umezawa", sent[0])
        self.assertEqual(answers, ["Смотрю цены у продавцов…"])
        self.assertNotIn("Сравните с текущей ставкой", sent[0])

    async def test_price_command_is_served_from_cache(self):
        self.db.save_card_offers(self.card, [make_offer("Tarmogoyf", "Тармогойф", seller_name="Umezawa",
                                                        cost=300)], 0)
        sent: list[str] = []

        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester"})()
            text = "/price tarmogoyf"

            async def answer(self, text, **kwargs):
                sent.append(text)

        await self.bot.cmd_price(FakeMessage())

        self.assertEqual(self.bot.ctx.topdeck.calls, 0)
        self.assertIn("300 ₽", sent[0])
        self.assertIn("Забрано из кэша", sent[0])

    async def test_price_command_for_card_without_offers_does_not_hit_the_network(self):
        self.db.save_card_offers(self.card, [], 0)
        sent: list[str] = []

        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester"})()
            text = "/price tarmogoyf"

            async def answer(self, text, **kwargs):
                sent.append(text)

        await self.bot.cmd_price(FakeMessage())

        self.assertEqual(self.bot.ctx.topdeck.calls, 0)
        self.assertIn("у продавцов нет", sent[0])

    async def test_unknown_card_is_reported(self):
        answers: list[str | None] = []
        edited: list[str] = []

        class FakeMessage:
            async def edit_text(self, text, reply_markup=None, disable_web_page_preview=None):
                edited.append(text)

            async def answer(self, text, **kwargs):
                edited.append(text)

        class FakeCallback:
            data = "price:999"
            from_user = type("U", (), {"id": 42, "username": "tester"})()
            message = FakeMessage()

            async def answer(self, text=None, **kwargs):
                answers.append(text)

        await self.bot.cb_card_price(FakeCallback())
        self.assertEqual(self.bot.ctx.topdeck.calls, 0)
        self.assertTrue(any("нет в списке" in text for text in edited))

    def test_number_of_buttons_matches_menu(self):
        from topbot import bot as bot_module

        labels = [button.text for row in bot_module.main_menu().keyboard for button in row]
        self.assertEqual(labels, list(bot_module.MENU_TEXTS))
        self.assertNotIn("💰 Цена", labels)
        self.assertNotIn("ℹ️ Статус", labels)


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class StartDeepLinkTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.card = self.db.upsert_card("Tarmogoyf", "tarmogoyf", name_ru="Тармогойф")
        self.db.add_to_watchlist(42, self.card)
        self.topdeck = FakeTopdeck([make_offer("Tarmogoyf", "Тармогойф", seller_name="Umezawa", cost=300)])
        bot_module.ctx.db = self.db
        bot_module.ctx.topdeck = self.topdeck
        bot_module.ctx.catalog = CardCatalog(self.db)
        bot_module.ctx.scryfall = None
        self.sent: list[str] = []
        self.deleted: list[int] = []

    def tearDown(self) -> None:
        self.bot.ctx.db = None
        self.bot.ctx.topdeck = None
        self.bot.ctx.catalog = None
        self.db.close()

    def _message(self, text: str, delete_error: Exception | None = None):
        sent = self.sent
        deleted = self.deleted

        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester", "first_name": "Tester"})()
            message_id = 11

            async def answer(self, answer_text, **kwargs):
                sent.append(answer_text)

            async def delete(self):
                if delete_error is not None:
                    raise delete_error
                deleted.append(self.message_id)

        message = FakeMessage()
        message.text = text
        return message

    async def test_price_deep_link_shows_offers(self):
        await self.bot.cmd_start(self._message(f"/start price_{self.card}"))
        self.assertEqual(self.topdeck.calls, 1)
        self.assertTrue(any("Минимальные предложения" in text for text in self.sent))
        self.assertFalse(any("Этот бот хранит" in text for text in self.sent))
        self.assertEqual(self.deleted, [11])

    async def test_remove_deep_link_deletes_card(self):
        await self.bot.cmd_start(self._message(f"/start rm_{self.card}"))
        self.assertEqual(self.db.watchlist(42), [])
        self.assertIn("Карта удалена", self.sent[0])
        self.assertEqual(self.deleted, [11])

    async def test_remove_of_missing_card_is_explained(self):
        await self.bot.cmd_start(self._message("/start rm_999"))
        self.assertIn("Карты уже нет в списке", self.sent[0])

    async def test_undeletable_start_message_is_logged(self):
        from aiogram.exceptions import TelegramBadRequest

        message = self._message(f"/start price_{self.card}",
                                delete_error=TelegramBadRequest(method=None, message="message can't be deleted"))
        with self.assertLogs("topbot.bot", level="INFO") as logs:
            await self.bot.cmd_start(message)
        self.assertTrue(any("start message is not deletable" in line for line in logs.output))
        self.assertTrue(any("Минимальные предложения" in text for text in self.sent))

    async def test_unknown_payload_shows_welcome(self):
        await self.bot.cmd_start(self._message("/start something"))
        self.assertTrue(any("Этот бот хранит" in text for text in self.sent))
        self.assertEqual(len(self.db.watchlist(42)), 1)
        self.assertEqual(self.deleted, [])


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class IgnoreLotButtonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        now = int(time.time())
        self.db.upsert_lot(Lot(id=5, title="Bayou (3ED, №283)", end_ts=now + 3600, start_ts=now - 3600,
                               start_bid=500, current_bid=700, bid_count=2, bin_value=0, seller_id=1,
                               seller_name="seller", seller_city="Москва", seller_refs=3,
                               shipping_quick=None, image_url=None,
                               url="https://topdeck.ru/apps/toptrade/auctions/5"), now)
        bot_module.ctx.db = self.db

    def tearDown(self) -> None:
        self.db.close()

    def _callback(self, data: str):
        answers: list[str | None] = []
        edits: list[object] = []

        class FakeMessage:
            async def edit_reply_markup(self, reply_markup=None):
                edits.append(reply_markup)

        class FakeCallback:
            from_user = type("U", (), {"id": 42, "username": "tester"})()
            message = FakeMessage()

            async def answer(self, text=None, **kwargs):
                answers.append(text)

        callback = FakeCallback()
        callback.data = data
        callback.answers = answers
        callback.edits = edits
        return callback

    async def test_ignore_button_hides_the_lot(self):
        callback = self._callback("skip:5")
        await self.bot.cb_skip(callback)
        self.assertEqual(self.db.ignored_lot_ids(42), {5})
        self.assertEqual(callback.answers, ["Больше не показываю этот лот"])
        self.assertEqual(callback.edits, [None])

    async def test_repeated_ignore_is_reported(self):
        self.db.ignore_lot(42, 5)
        callback = self._callback("skip:5")
        await self.bot.cb_skip(callback)
        self.assertEqual(callback.answers, ["Этот лот уже скрыт"])


class CallbackAnswerTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_callback_answer_does_not_break_handler(self):
        from aiogram.exceptions import TelegramBadRequest
        from topbot import bot as bot_module

        class FakeCallback:
            data = "price:1"
            from_user = type("U", (), {"id": 42, "username": "tester"})()

            async def answer(self, text=None, **kwargs):
                raise TelegramBadRequest(method=None, message="query is too old")

        with self.assertLogs("topbot.bot", level="INFO") as logs:
            await bot_module._answer(FakeCallback(), "Смотрю цены…")
        self.assertTrue(any("callback answer rejected" in line for line in logs.output))


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class FreeTextTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.catalog = CardCatalog(self.db)
        self.catalog.store_names(["Mystic Remora"])
        bot_module.ctx.db = self.db
        bot_module.ctx.catalog = self.catalog
        bot_module.ctx.scryfall = None
        bot_module.ctx.topdeck = None
        self.state = FSMContext(storage=MemoryStorage(),
                                key=StorageKey(bot_id=1, chat_id=1, user_id=42))

    def tearDown(self) -> None:
        self.bot.ctx.db = None
        self.db.close()

    def _message(self, text: str, sent: list[str]):
        class FakeMessage:
            from_user = type("U", (), {"id": 42, "username": "tester", "first_name": "Tester"})()

            async def answer(self, answer_text, **kwargs):
                sent.append(answer_text)

        message = FakeMessage()
        message.text = text
        return message

    async def test_plain_text_is_treated_as_add_request(self):
        sent: list[str] = []
        await self.bot.on_free_text(self._message("mystic remora", sent), self.state)
        self.assertTrue(any("Добавлено" in text for text in sent), sent)
        self.assertEqual([item.name_en for item in self.db.watchlist(42)], ["Mystic Remora"])

    async def test_unknown_text_explains_what_to_do(self):
        sent: list[str] = []
        await self.bot.on_free_text(self._message("zzzz qqqq", sent), self.state)
        self.assertTrue(any("Попробуйте другое написание" in text for text in sent), sent)
        self.assertEqual(self.db.watchlist(42), [])


@unittest.skipUnless(HAS_AIOGRAM, "aiogram is not installed")
class StatusCommandTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from topbot import bot as bot_module

        self.bot = bot_module
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.db.ensure_user(1, "Boss")
        bot_module.ctx.db = self.db
        bot_module.ctx.admin_ids = ()
        bot_module.ctx.admin_usernames = ()

    def tearDown(self) -> None:
        self.bot.ctx.db = None
        self.bot.ctx.admin_ids = ()
        self.db.close()

    def _message(self, user_id: int, sent: list[str]):
        class FakeMessage:
            from_user = type("U", (), {"id": user_id, "username": "tester", "first_name": "Tester"})()

            async def answer(self, answer_text, **kwargs):
                sent.append(answer_text)

        return FakeMessage()

    async def test_status_is_admin_only(self):
        sent: list[str] = []
        await self.bot.cmd_status(self._message(42, sent))
        self.assertEqual(sent, ["Команда только для администратора"])

    async def test_status_shows_poll_state_for_admin(self):
        self.bot.ctx.admin_ids = (1,)
        self.db.record_poll("auctions", int(time.time()) - 60, 100, True, 42)
        sent: list[str] = []
        await self.bot.cmd_status(self._message(1, sent))
        self.assertIn("Лотов активно", sent[0])
        self.assertIn("Последний опрос", sent[0])

    def test_status_is_not_published_in_commands(self):
        self.assertNotIn("status", [command.command for command in self.bot.COMMANDS])


if __name__ == "__main__":
    unittest.main()
