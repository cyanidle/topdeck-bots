from __future__ import annotations

import unittest

from topbot.composition import SetIndex, clean_composition, is_junk, is_metadata_group, metadata_names
from topbot.db import Database

SETS = SetIndex(names=frozenset({"urza s saga", "time spiral", "heaven sent marvel"}),
                codes=frozenset({"mar", "2x2", "tle"}))


class JunkTest(unittest.TestCase):
    def test_real_names_are_kept(self):
        for name in ("Goblin Cadets", "Sephiroth, Fabled SOLDIER // Sephiroth, One-Winged Angel",
                     "Сохранить // В Памяти", "B.F.M. (Big Furry Monster)", "Сезон Удвоения"):
            self.assertFalse(is_junk(name), name)

    def test_urls_and_prose_are_dropped(self):
        for name in ("https:", "scryfall.com/card/2x2/10/emiel-the-blessed", "disk.yandex.ru/d/chvZBlqsGAYPMg",
                     "Состояние NM/EX", "Название: Кирин Облачной Стали Издание: Kamigawa Состояние: NM", "—"):
            self.assertTrue(is_junk(name), name)


class MetadataGroupTest(unittest.TestCase):
    def test_groups_with_numbers_are_metadata(self):
        self.assertTrue(is_metadata_group("Urza's Saga, #189", SETS))
        self.assertTrue(is_metadata_group("MAR#0041", SETS))
        self.assertTrue(is_metadata_group("2X2, 10, EN", SETS))

    def test_known_set_names_and_codes_are_metadata(self):
        self.assertTrue(is_metadata_group("Urza's Saga", SETS))
        self.assertTrue(is_metadata_group("MAR", SETS))
        self.assertTrue(is_metadata_group("EN, NM", SETS))

    def test_card_list_in_brackets_is_not_metadata(self):
        self.assertFalse(is_metadata_group("Bayou, Tarmogoyf", SETS))
        self.assertFalse(is_metadata_group("Goblin Cadets", SETS))


class CleanCompositionTest(unittest.TestCase):
    def test_set_name_from_the_title_brackets_is_dropped(self):
        names = ("Goblin Cadets", "Urza's Saga")
        self.assertEqual(clean_composition(names, "4X Goblin Cadets (Urza's Saga, #189)", SETS),
                         ("Goblin Cadets",))

    def test_card_that_merely_shares_a_set_name_is_kept_without_metadata(self):
        names = ("Goblin Cadets", "Urza's Saga")
        self.assertEqual(clean_composition(names, "4X Goblin Cadets", SETS), names)

    def test_junk_and_duplicates_are_dropped(self):
        names = ("Bayou", "bayou", "https:", "Состояние NM/EX")
        self.assertEqual(clean_composition(names, "Земли", SETS), ("Bayou",))

    def test_set_name_that_is_not_a_card_is_dropped(self):
        sets = SetIndex(names=frozenset({"visions", "time spiral"}))
        cards = frozenset({"time spiral"})
        self.assertEqual(clean_composition(("Visions", "Time Spiral", "Bayou"), "Земли", sets, cards),
                         ("Time Spiral", "Bayou"))

    def test_set_name_rule_is_skipped_without_a_card_catalog(self):
        sets = SetIndex(names=frozenset({"visions"}))
        self.assertEqual(clean_composition(("Visions",), "Земли", sets), ("Visions",))


class StoredCompositionCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.card = self.db.upsert_card("Urza's Saga", "urza s saga")
        self.lot = 550016
        self.db.upsert_lot(_lot(self.lot, "4X Goblin Cadets (Urza's Saga, #189)"), 1_000)
        self.db.save_composition(self.lot, ["Goblin Cadets", "Urza's Saga", "https:", "scryfall.com/card/2x2/10"],
                                 1_000)

    def tearDown(self) -> None:
        self.db.close()

    def test_cleanup_removes_metadata_and_junk_items(self):
        self.assertEqual(self.db.clean_compositions(SetIndex(names=frozenset({"urza s saga"}),
                                                             codes=frozenset({"2x2"}))), 1)
        self.assertEqual(self.db.compositions()[self.lot], ("Goblin Cadets",))

    def test_cleanup_invalidates_matches(self):
        self.db.save_lot_cards(self.lot, [(self.card, 1.0, "composition:exact_en")], "hash")
        self.db.clean_compositions(SetIndex(names=frozenset({"urza s saga"})))
        row = self.db.conn.execute("SELECT matched_hash FROM lots WHERE id=?", (self.lot,)).fetchone()
        self.assertIsNone(row["matched_hash"])

    def test_second_run_changes_nothing(self):
        sets = SetIndex(names=frozenset({"urza s saga"}))
        self.assertEqual(self.db.clean_compositions(sets), 1)
        self.assertEqual(self.db.clean_compositions(sets), 0)


class SetIndexFromDatabaseTest(unittest.TestCase):
    def test_sets_are_stored_normalized(self):
        db = Database(":memory:")
        try:
            self.assertEqual(db.save_sets([("2X2", "Double Masters 2022"), ("MAR", "Marvel Super Heroes")]), 2)
            index = db.set_index()
            self.assertIn("double masters 2022", index.names)
            self.assertIn("2x2", index.codes)
            self.assertTrue(index.knows("Urza's Saga") is False)
        finally:
            db.close()


def _lot(lot_id: int, title: str):
    from topbot.models import Lot
    return Lot(id=lot_id, title=title, end_ts=9_000, start_ts=1_000, start_bid=100, current_bid=100,
               bid_count=1, bin_value=0, seller_id=1, seller_name="seller", seller_city=None,
               seller_refs=None, shipping_quick=None, image_url=None, url=f"https://topdeck.ru/lot/{lot_id}")


if __name__ == "__main__":
    unittest.main()
