from __future__ import annotations

import unittest

from topbot.catalog import CardCatalog
from topbot.db import Database


class FakeScryfall:
    def __init__(self, names: list[str]):
        self.names = names
        self.autocomplete_calls = 0

    async def card_names(self) -> list[str]:
        return self.names

    async def autocomplete(self, query: str) -> list[str]:
        self.autocomplete_calls += 1
        return [name for name in self.names if query.lower() in name.lower()][:20]


class CatalogSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.catalog = CardCatalog(self.db)
        self.catalog.store_names([
            "Tarmogoyf", "Tarmogoyf Nest", "Bayou", "Snapcaster Mage", "Lightning Bolt",
            "Lightning Helix", "Boseiju, Who Endures", "Uro, Titan of Nature's Wrath",
        ])

    def tearDown(self) -> None:
        self.db.close()

    def test_prefix_match_first(self):
        self.assertEqual(self.catalog.search("tarmo")[0], "Tarmogoyf")
        self.assertIn("Tarmogoyf Nest", self.catalog.search("tarmo"))

    def test_mid_word_match(self):
        results = self.catalog.search("goyf")
        self.assertIn("Tarmogoyf", results)

    def test_exact_is_top(self):
        self.assertEqual(self.catalog.search("bayou")[0], "Bayou")

    def test_limit(self):
        self.assertLessEqual(len(self.catalog.search("light", limit=2)), 2)

    def test_short_query_returns_nothing(self):
        self.assertEqual(self.catalog.search("t"), [])

    def test_unknown_query(self):
        self.assertEqual(self.catalog.search("black lotus"), [])

    def test_russian_names_are_searchable(self):
        self.catalog.add_local_card("Lightning Bolt", "Удар Молнии")
        self.assertIn("Удар Молнии", self.catalog.search("удар"))

    def test_store_is_idempotent(self):
        self.catalog.store_names(["Bayou"])
        rows = self.db.conn.execute("SELECT COUNT(*) AS n FROM card_names WHERE name='Bayou'").fetchone()["n"]
        self.assertEqual(rows, 1)

    def test_suggest_uses_local_catalog(self):
        import asyncio

        scryfall = FakeScryfall(["Tarmogoyf"])
        catalog = CardCatalog(self.db, scryfall)
        names = asyncio.run(catalog.suggest("tarmo", 5))
        self.assertEqual(scryfall.autocomplete_calls, 0)
        self.assertIn("Tarmogoyf", names)


class CatalogRefreshTest(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_fills_and_respects_ttl(self):
        db = Database(":memory:")
        scryfall = FakeScryfall(["Tarmogoyf", "Bayou"])
        catalog = CardCatalog(db, scryfall)
        self.assertTrue(catalog.is_stale())
        added = await catalog.refresh()
        self.assertEqual(added, 2)
        self.assertEqual(catalog.count(), 2)
        self.assertFalse(catalog.is_stale())
        self.assertEqual(await catalog.refresh(), 0)
        db.close()

    async def test_suggest_falls_back_to_scryfall_when_catalog_empty(self):
        db = Database(":memory:")
        scryfall = FakeScryfall(["Tarmogoyf", "Tarmogoyf Nest"])
        catalog = CardCatalog(db, scryfall)
        names = await catalog.suggest("tarmo", 5)
        self.assertEqual(scryfall.autocomplete_calls, 1)
        self.assertEqual(names[0], "Tarmogoyf")
        db.close()


if __name__ == "__main__":
    unittest.main()
