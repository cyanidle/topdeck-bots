from __future__ import annotations

import unittest

from topbot.db import Database
from topbot.models import Card, Offer
from topbot.prices import PricePoller, cheapest_per_card, collector_misread, group_offers, match_cards


def make_offer(eng_name: str = "Tarmogoyf", rus_name: str | None = None, seller_name: str | None = "seller",
               cost: int = 100, **overrides) -> Offer:
    fields = dict(eng_name=eng_name, rus_name=rus_name, name=eng_name, source="topdeck",
                  seller_name=seller_name, seller_id=1, city="Москва", cost=cost, qty=1,
                  url="https://topdeck.ru/offer", line="", stamp=None)
    fields.update(overrides)
    return Offer(**fields)


class FakeTopdeck:
    def __init__(self, offers: list[Offer], error: Exception | None = None):
        self.offers = offers
        self.error = error
        self.batches: list[list[str]] = []

    async def search_singles(self, names):
        self.batches.append(list(names))
        if self.error is not None:
            raise self.error
        return self.offers, False


class CardOfferCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")
        self.goyf = self.db.upsert_card("Tarmogoyf", "tarmogoyf", name_ru="Тармогойф")
        self.bolt = self.db.upsert_card("Lightning Bolt", "lightning bolt")

    def tearDown(self) -> None:
        self.db.close()

    def test_cheapest_offer_per_seller_is_stored(self):
        saved = self.db.save_card_offers(self.goyf, [make_offer(seller_name="A", cost=900),
                                                     make_offer(seller_name="A", cost=700),
                                                     make_offer(seller_name="B", cost=800)], 1000)
        self.assertEqual(saved, 2)
        self.assertEqual([offer.cost for offer in self.db.card_offers(self.goyf)], [700, 800])
        self.assertTrue(self.db.price_synced(self.goyf))

    def test_zero_priced_offers_are_dropped_but_card_counts_as_synced(self):
        self.assertEqual(self.db.save_card_offers(self.goyf, [make_offer(cost=0)], 1000), 0)
        self.assertEqual(self.db.card_offers(self.goyf), [])
        self.assertTrue(self.db.price_synced(self.goyf))

    def test_offers_carry_card_names_for_detail_cleanup(self):
        self.db.save_card_offers(self.goyf, [make_offer(line="1 Tarmogoyf (FUT, NM)")], 1000)
        offer = self.db.card_offers(self.goyf)[0]
        self.assertEqual((offer.eng_name, offer.name, offer.rus_name),
                         ("Tarmogoyf", "Tarmogoyf", "Тармогойф"))

    def test_top_offers_per_card_respects_limit_and_order(self):
        self.db.save_card_offers(self.goyf, [make_offer(seller_name=f"S{index}", cost=100 + index)
                                            for index in range(5)], 1000)
        self.db.save_card_offers(self.bolt, [make_offer("Lightning Bolt", seller_name="B", cost=50)], 1000)
        top = self.db.top_offers_by_card([self.goyf, self.bolt], 3)
        self.assertEqual([offer.cost for offer in top[self.goyf]], [100, 101, 102])
        self.assertEqual([offer.cost for offer in top[self.bolt]], [50])
        self.assertEqual(self.db.top_offers_by_card([], 3), {})

    def test_missing_cards_and_oldest_sync_time(self):
        self.assertEqual(self.db.cards_without_prices([self.goyf, self.bolt]), [self.goyf, self.bolt])
        self.db.save_card_offers(self.goyf, [], 1000)
        self.db.save_card_offers(self.bolt, [], 1600)
        self.assertEqual(self.db.cards_without_prices([self.goyf, self.bolt]), [])
        self.assertEqual(self.db.prices_synced_at([self.goyf, self.bolt]), 1000)
        self.assertIsNone(self.db.prices_synced_at([]))

    def test_prune_drops_cards_outside_the_watchlist(self):
        self.db.save_card_offers(self.goyf, [make_offer()], 1000)
        self.db.add_to_watchlist(42, self.goyf)
        self.db.prune_card_offers([card.id for card in self.db.watched_cards()])
        self.assertEqual(len(self.db.card_offers(self.goyf)), 1)
        self.db.prune_card_offers([])
        self.assertEqual(self.db.card_offers(self.goyf), [])
        self.assertFalse(self.db.price_synced(self.goyf))

    def test_watched_cards_are_distinct(self):
        self.db.ensure_user(7, "other")
        self.db.add_to_watchlist(42, self.goyf)
        self.db.add_to_watchlist(7, self.goyf)
        self.db.add_to_watchlist(42, self.bolt)
        self.assertEqual([card.name_en for card in self.db.watched_cards()], ["Lightning Bolt", "Tarmogoyf"])

    def test_find_card_by_exact_name_and_prefix(self):
        self.assertEqual(self.db.find_card("tarmogoyf").id, self.goyf)
        self.assertEqual(self.db.find_card("Tarmo").id, self.goyf)
        self.assertIsNone(self.db.find_card("zzzz"))


class MatchCardsTest(unittest.TestCase):
    def test_matches_by_english_and_russian_name(self):
        cards = [Card(id=1, name_en="Tarmogoyf", name_ru="Тармогойф"), Card(id=2, name_en="Lightning Bolt")]
        found = match_cards(cards, [
            make_offer("Tarmogoyf", "Тармогойф", cost=100),
            make_offer("Тармогойф", cost=200, name="Тармогойф"),
            make_offer("Lightning Bolt", cost=50),
            make_offer("Bayou", cost=999),
            make_offer("Tarmogoyf", cost=0),
        ])
        self.assertEqual(sorted(found), [1, 2])
        self.assertEqual([offer.cost for offer in found[1]], [100, 200])
        self.assertEqual([offer.cost for offer in found[2]], [50])

    def test_echo_names_do_not_attach_a_foreign_card(self):
        cards = [Card(id=14, name_en="Shallow Grave")]
        found = match_cards(cards, [
            make_offer("Shallow Grave", cost=53, name="Curse of Shallow Graves"),
            make_offer("Shallow Grave", cost=11550, name="Shallow Grave"),
        ])
        self.assertEqual([offer.cost for offer in found[14]], [11550])

    def test_offer_without_a_card_name_is_dropped(self):
        self.assertEqual(match_cards([Card(id=1, name_en="Tarmogoyf")], [make_offer("Tarmogoyf", name="")]), {})

    def test_card_without_russian_name_ignores_foreign_echo(self):
        cards = [Card(id=3, name_en="Captain James T. Kirk")]
        found = match_cards(cards, [make_offer("Captain James T. Kirk", "Кирк", cost=700,
                                               name="Curse of Shallow Graves")])
        self.assertEqual(found, {})


    def test_russian_listing_with_another_translation_is_dropped(self):
        cards = [Card(id=1, name_en="Tarmogoyf", name_ru="Тармогойф")]
        found = match_cards(cards, [make_offer("Tarmogoyf", "Тармогойф", cost=700, name="Тармо-гойф")])
        self.assertEqual(found, {})


class CollectorMisreadTest(unittest.TestCase):
    def test_price_equal_to_the_collector_number_is_dropped(self):
        offer = make_offer("Enlightened Tutor", cost=19, name="Enlightened Tutor",
                           line="1    Enlightened Tutor #19    6ED    en        mp    1799")
        self.assertTrue(collector_misread(offer))
        self.assertEqual(match_cards([Card(id=1, name_en="Enlightened Tutor")], [offer]), {})
        self.assertEqual(group_offers([offer]), {})

    def test_hash_with_space_and_the_number_sign(self):
        for line in ("1 Otawara, Soaring City # 271 NEO en Foil", "1 Bayou №271 3ED en"):
            self.assertTrue(collector_misread(make_offer("x", cost=271, name="x", line=line)))

    def test_normal_line_keeps_the_offer(self):
        offer = make_offer("Enlightened Tutor", cost=1799, name="Enlightened Tutor",
                           line="1    Enlightened Tutor #19    6ED    en        mp    1799")
        self.assertFalse(collector_misread(offer))
        self.assertIn(1, match_cards([Card(id=1, name_en="Enlightened Tutor")], [offer]))

    def test_line_without_a_collector_number_keeps_the_offer(self):
        self.assertFalse(collector_misread(make_offer("x", cost=19, name="x", line="2 Shallow Grave MIR DE 19")))

    def test_zero_priced_offer_is_not_flagged(self):
        self.assertFalse(collector_misread(make_offer("x", cost=0, name="x", line="x #0")))


class GroupOffersTest(unittest.TestCase):
    def test_groups_by_the_card_of_the_listing(self):
        offers = [make_offer("Eladamri", cost=197, name="Eladamri's Call"),
                  make_offer("Eladamri", cost=600, name="Eladamri, Korvecdal"),
                  make_offer("Eladamri", cost=390, name="Eladamri's Vineyard"),
                  make_offer("Eladamri", cost=720, name="Eladamri, Korvecdal")]
        groups = group_offers(offers)
        self.assertEqual(sorted(group[0] for group in groups.values()),
                         ["Eladamri's Call", "Eladamri's Vineyard", "Eladamri, Korvecdal"])
        korvecdal = groups["eladamri korvecdal"]
        self.assertEqual([offer.cost for offer in korvecdal[1]], [600, 720])

    def test_nameless_and_zero_priced_offers_are_ignored(self):
        offers = [make_offer("x", cost=0, name="Shallow Grave"), make_offer("x", cost=10, name="")]
        self.assertEqual(group_offers(offers), {})

    def test_cheapest_per_card_keeps_one_offer(self):
        offers = [make_offer("tarmo", cost=9000, name="Tarmogoyf"),
                  make_offer("tarmo", cost=8500, name="Tarmogoyf"),
                  make_offer("tarmo", cost=100, name="Tarmogoyf Nest")]
        best = cheapest_per_card(offers)
        self.assertEqual(best["tarmogoyf"][0], "Tarmogoyf")
        self.assertEqual(best["tarmogoyf"][1].cost, 8500)
        self.assertEqual(best["tarmogoyf nest"][1].cost, 100)


class PricePollerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.ensure_user(42, "tester")

    def tearDown(self) -> None:
        self.db.close()

    def _watch(self, *names: str) -> list[int]:
        ids = []
        for name in names:
            card_id = self.db.upsert_card(name, name.lower())
            self.db.add_to_watchlist(42, card_id)
            ids.append(card_id)
        return ids

    async def test_poll_fetches_all_watched_cards_in_batches(self):
        names = [f"Card {index}" for index in range(9)]
        self._watch(*names)
        topdeck = FakeTopdeck([make_offer("Card 0", seller_name="A", cost=111)])
        poller = PricePoller(self.db, topdeck, clock=lambda: 5000)

        stats = await poller.poll_once()

        self.assertEqual([len(batch) for batch in topdeck.batches], [8, 1])
        self.assertEqual(stats.cards, 9)
        self.assertEqual(stats.requests, 2)
        self.assertEqual([offer.cost for offer in self.db.card_offers(self.db.find_card("Card 0").id)], [111])
        self.assertEqual(self.db.cards_without_prices([card.id for card in self.db.watched_cards()]), [])
        run = self.db.last_poll("prices")
        self.assertTrue(run.ok)
        self.assertEqual(run.items, 9)

    async def test_poll_without_watchlist_does_not_touch_network(self):
        topdeck = FakeTopdeck([])
        stats = await PricePoller(self.db, topdeck, clock=lambda: 5000).poll_once()
        self.assertEqual(topdeck.batches, [])
        self.assertEqual(stats.cards, 0)
        self.assertIsNone(self.db.last_poll("prices"))

    async def test_poll_without_watchlist_clears_the_cache(self):
        card_id = self.db.upsert_card("Tarmogoyf", "tarmogoyf")
        self.db.save_card_offers(card_id, [make_offer()], 1000)
        await PricePoller(self.db, FakeTopdeck([]), clock=lambda: 5000).poll_once()
        self.assertEqual(self.db.card_offers(card_id), [])
        self.assertFalse(self.db.price_synced(card_id))

    async def test_poll_failure_is_recorded_and_reraised(self):
        self._watch("Tarmogoyf")
        topdeck = FakeTopdeck([], error=OSError("tunnel is down"))
        poller = PricePoller(self.db, topdeck, clock=lambda: 5000)
        with self.assertRaises(OSError):
            await poller.poll_once()
        run = self.db.last_poll("prices")
        self.assertFalse(run.ok)
        self.assertIn("tunnel is down", run.error)

    async def test_poll_prunes_cards_that_left_the_watchlist(self):
        goyf, bolt = self._watch("Tarmogoyf", "Lightning Bolt")
        topdeck = FakeTopdeck([make_offer("Tarmogoyf", seller_name="A", cost=100),
                               make_offer("Lightning Bolt", seller_name="B", cost=50)])
        poller = PricePoller(self.db, topdeck, clock=lambda: 5000)
        await poller.poll_once()
        self.assertEqual(len(self.db.card_offers(bolt)), 1)

        self.db.remove_from_watchlist(42, bolt)
        await poller.poll_once()
        self.assertEqual(self.db.card_offers(bolt), [])
        self.assertEqual(len(self.db.card_offers(goyf)), 1)


if __name__ == "__main__":
    unittest.main()
