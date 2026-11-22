from __future__ import annotations

import unittest

from topbot.models import Card

from collectbot.db import CollectDatabase, normalize_number
from collectbot.resolver import BATCH_LIMIT, PrintResolver


class FakeScryfall:
    def __init__(self, found: dict | None = None, fail: bool = False):
        self.found = found or {}
        self.fail = fail
        self.calls: list[list[dict]] = []

    async def collection(self, identifiers: list[dict]) -> list[Card]:
        self.calls.append(identifiers)
        if self.fail:
            raise RuntimeError("network down")
        cards = []
        for identifier in identifiers:
            key = (identifier["set"].upper(), normalize_number(identifier["collector_number"]))
            card = self.found.get(key)
            if card is not None:
                cards.append(card)
        return cards


def card_for(set_code: str, number: str, name: str) -> Card:
    return Card(name_en=name, set_code=set_code, collector_no=number)


class PrintResolverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = CollectDatabase(":memory:")

    def tearDown(self):
        self.db.close()

    async def test_resolve_caches_positive(self):
        scryfall = FakeScryfall({("TMT", "67"): card_for("TMT", "67", "Ninja Teen")})
        resolver = PrintResolver(self.db, scryfall)
        card = await resolver.resolve("TMT", "67")
        self.assertEqual(card.name_en, "Ninja Teen")
        again = await resolver.resolve("TMT", "67")
        self.assertEqual(again.name_en, "Ninja Teen")
        self.assertEqual(len(scryfall.calls), 1)

    async def test_negative_is_cached_until_ttl(self):
        scryfall = FakeScryfall()
        resolver = PrintResolver(self.db, scryfall)
        self.assertIsNone(await resolver.resolve("XXX", "1"))
        self.assertIsNone(await resolver.resolve("XXX", "1"))
        self.assertEqual(len(scryfall.calls), 1)

    async def test_network_failure_is_not_cached(self):
        resolver = PrintResolver(self.db, FakeScryfall(fail=True))
        with self.assertRaises(RuntimeError):
            await resolver.resolve("TMT", "67")
        self.assertFalse(self.db.print_lookup_fresh("TMT", "67"))

    async def test_no_client_means_cache_only(self):
        self.db.cache_print("TMT", "67", card_for("TMT", "67", "Ninja Teen"))
        resolver = PrintResolver(self.db, None)
        self.assertIsNone(await resolver.resolve("XXX", "1"))
        self.assertEqual((await resolver.resolve("TMT", "67")).name_en, "Ninja Teen")

    async def test_resolve_missing_batches_requests(self):
        keys = [(f"S{index:03d}", str(index)) for index in range(BATCH_LIMIT + 5)]
        scryfall = FakeScryfall({("S000", "0"): card_for("S000", "0", "Zero")})
        resolver = PrintResolver(self.db, scryfall)
        resolved = await resolver.resolve_missing(keys)
        self.assertEqual(len(scryfall.calls), 2)
        self.assertEqual(len(scryfall.calls[0]), BATCH_LIMIT)
        self.assertIn(("S000", "0"), resolved)
        self.assertEqual(len(resolved), 1)

    async def test_resolve_missing_skips_fresh_negatives(self):
        resolver = PrintResolver(self.db, FakeScryfall())
        self.db.cache_print("XXX", "1", None)
        resolved = await resolver.resolve_missing([("XXX", "1"), ("TMT", "67")])
        self.assertNotIn(("XXX", "1"), resolved)
        self.assertNotIn(("TMT", "67"), resolved)
