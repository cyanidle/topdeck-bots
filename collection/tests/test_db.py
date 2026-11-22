from __future__ import annotations

import time
import unittest

from topbot.models import Card

from collectbot.db import CollectDatabase, normalize_identity, normalize_number


class NormalizeTest(unittest.TestCase):
    def test_colon_and_slash(self):
        self.assertEqual(normalize_identity("tmt:067"), ("TMT", "67"))
        self.assertEqual(normalize_identity("DDS:051/065"), ("DDS", "51"))
        self.assertEqual(normalize_identity("war 149"), ("WAR", "149"))
        self.assertEqual(normalize_identity("C18:158"), ("C18", "158"))

    def test_number_variants(self):
        self.assertEqual(normalize_number("187a"), "187a")
        self.assertEqual(normalize_number("206★"), "206★")
        self.assertEqual(normalize_number("0051/065"), "51")

    def test_rejects_names_and_garbage(self):
        self.assertIsNone(normalize_identity("Ninja Teen"))
        self.assertIsNone(normalize_identity("TMT:"))
        self.assertIsNone(normalize_identity(":67"))
        self.assertIsNone(normalize_identity("один длинный запрос"))
        self.assertIsNone(normalize_identity(""))


class CollectionCrudTest(unittest.TestCase):
    def setUp(self):
        self.db = CollectDatabase(":memory:")
        self.db.ensure_user(7, "tester", "Test")

    def tearDown(self):
        self.db.close()

    def test_add_accumulates(self):
        self.assertEqual(self.db.change_count(7, "TMT", "67", 1).count, 1)
        self.assertEqual(self.db.change_count(7, "TMT", "67", 2).count, 3)

    def test_negative_on_missing_is_noop(self):
        self.assertIsNone(self.db.change_count(7, "TMT", "67", -1))
        self.assertIsNone(self.db.get_card(7, "TMT", "67"))

    def test_zero_removes_entry(self):
        self.db.change_count(7, "TMT", "67", 2)
        self.assertIsNone(self.db.change_count(7, "TMT", "67", -5))
        self.assertIsNone(self.db.get_card(7, "TMT", "67"))

    def test_users_are_isolated(self):
        self.db.ensure_user(8)
        self.db.change_count(7, "TMT", "67", 1)
        self.db.change_count(8, "TMT", "67", 1)
        self.assertEqual(self.db.get_card(7, "TMT", "67").count, 1)
        self.assertFalse(self.db.remove_card(7, "XXX", "1"))
        self.assertEqual(self.db.get_card(8, "TMT", "67").count, 1)

    def test_entry_by_id_scoped_to_user(self):
        entry = self.db.change_count(7, "TMT", "67", 1)
        self.db.ensure_user(8)
        self.assertIsNone(self.db.entry_by_id(8, entry.id))
        self.assertTrue(self.db.remove_by_id(7, entry.id))
        self.assertIsNone(self.db.entry_by_id(7, entry.id))

    def test_list_orders_by_set_and_number(self):
        self.db.change_count(7, "WAR", "149", 1)
        self.db.change_count(7, "DDS", "51", 1)
        self.db.change_count(7, "DDS", "205", 1)
        self.db.change_count(7, "DDS", "187a", 1)
        entries = self.db.list_cards(7)
        self.assertEqual([(e.set_code, e.number) for e in entries],
                         [("DDS", "51"), ("DDS", "187a"), ("DDS", "205"), ("WAR", "149")])
        self.assertEqual(self.db.collection_total(7), 4)


class PrintsCacheTest(unittest.TestCase):
    def setUp(self):
        self.db = CollectDatabase(":memory:")

    def tearDown(self):
        self.db.close()

    def test_roundtrip(self):
        card = Card(name_en="Ninja Teen", scryfall_id="id1", set_code="TMT", collector_no="67")
        self.db.cache_print("TMT", "67", card)
        cached = self.db.cached_print("TMT", "67")
        self.assertEqual(cached.name_en, "Ninja Teen")
        self.assertTrue(self.db.print_lookup_fresh("TMT", "67"))

    def test_missing_is_not_fresh(self):
        self.assertFalse(self.db.print_lookup_fresh("QQQ", "9"))
        self.assertIsNone(self.db.cached_print("QQQ", "9"))

    def test_negative_expires(self):
        self.db.cache_print("XXX", "1", None)
        self.assertTrue(self.db.print_lookup_fresh("XXX", "1"))
        self.assertIsNone(self.db.cached_print("XXX", "1"))
        old = int(time.time()) - 8 * 86400
        self.db.conn.execute(
            "UPDATE prints_cache SET resolved_at=? WHERE set_code=? AND number=?",
            (old, "XXX", "1"))
        self.assertFalse(self.db.print_lookup_fresh("XXX", "1"))

    def test_positive_never_expires(self):
        self.db.cache_print("TMT", "67", Card(name_en="Ninja Teen", set_code="TMT", collector_no="67"))
        future = int(time.time()) + 365 * 86400
        self.assertTrue(self.db.print_lookup_fresh("TMT", "67", now=future))
