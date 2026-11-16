from __future__ import annotations

import unittest

from topbot.matching import (is_metadata_tail, is_multi_lot, match_names, match_title, normalize,
                             title_candidates)
from topbot.models import Card

CARDS = [
    Card(id=1, name_en="Bayou"),
    Card(id=2, name_en="Plains", name_ru="Равнина"),
    Card(id=3, name_en="Tarmogoyf", name_ru="Тармогойф"),
    Card(id=4, name_en="Nissa, Leyline Tamer"),
    Card(id=5, name_en="Snapcaster Mage", name_ru="Маг Молниеносных Заклинаний"),
]


class NormalizeTest(unittest.TestCase):
    def test_punctuation_and_yo(self):
        self.assertEqual(normalize("Удар Молнии №283"), "удар молнии 283")
        self.assertEqual(normalize("Ёлка,  Мир"), "елка мир")


class CandidatesTest(unittest.TestCase):
    def test_split_on_dash_and_drop_parens(self):
        candidates = title_candidates("Bayou - Reserved List (Revised Edition, 3ED, REV, EN, №283)")
        self.assertIn("bayou", candidates)
        self.assertIn("reserved list", candidates)
        self.assertTrue(all("revised" not in candidate for candidate in candidates))


class MatchTest(unittest.TestCase):
    def test_exact_english_name(self):
        matches = match_title("Bayou - Reserved List (Revised Edition, 3ED, REV, EN, №283)", CARDS)
        self.assertEqual(matches[0][0], 1)
        self.assertEqual(matches[0][2], "exact_en")

    def test_quantity_prefix(self):
        matches = match_title("4 Plains (Времена Года) BORDERLESS SURGE FOIL THE HOBBIT (317-320) ENG, NM", CARDS)
        self.assertEqual(matches[0][0], 2)

    def test_russian_name(self):
        matches = match_title("Фойл Тармогойф (FUT, 153, RU, NM)", CARDS)
        self.assertEqual(matches[0][0], 3)
        self.assertEqual(matches[0][2], "exact_ru")

    def test_foil_prefix_and_comma(self):
        matches = match_title("Foil Nissa, Leyline Tamer (Reality Fracture Commander, FRC №002)", CARDS)
        self.assertEqual(matches[0][0], 4)

    def test_multi_lot_ignored(self):
        title = "Лот Ультиматумов ( Список внутри)"
        self.assertTrue(is_multi_lot(title))
        self.assertEqual(match_title(title, CARDS), [])

    def test_unknown_card(self):
        self.assertEqual(match_title("Black Lotus (Unlimited, EN)", CARDS), [])

    def test_short_names_do_not_produce_fuzzy_noise(self):
        self.assertEqual(match_title("Snap", CARDS), [])


class PrefixMatchTest(unittest.TestCase):
    def test_title_starts_with_card_name_and_metadata(self):
        matches = match_title("Exploration 2XM #167", [Card(id=7, name_en="Exploration")])
        self.assertEqual([(match[0], match[2]) for match in matches], [(7, "prefix_en")])
        self.assertLess(matches[0][1], 1.0)

    def test_metadata_tail_detection(self):
        self.assertTrue(is_metadata_tail(""))
        self.assertTrue(is_metadata_tail("2xm 167"))
        self.assertTrue(is_metadata_tail("foil signed"))
        self.assertFalse(is_metadata_tail("sanctuary"))
        self.assertFalse(is_metadata_tail("titan"))

    def test_set_name_tail_with_number(self):
        matches = match_title("Mystic Remora Ice Age #87 EN NM-", [Card(id=5, name_en="Mystic Remora")])
        self.assertEqual([(match[0], match[2]) for match in matches], [(5, "prefix_en")])

    def test_word_continuation_is_not_a_prefix_match(self):
        cards = [Card(id=11, name_en="Island"), Card(id=12, name_en="Mountain")]
        self.assertEqual(match_title("Island Sanctuary", cards), [])
        self.assertEqual(match_title("Mountain Titan", cards), [])

    def test_short_names_never_use_prefix(self):
        self.assertEqual(match_title("Snapcaster Mage", [Card(id=5, name_en="Snap")]), [])

    def test_longer_card_name_wins(self):
        cards = [Card(id=11, name_en="Island"), Card(id=12, name_en="Island Sanctuary")]
        self.assertEqual([(m[0], m[2]) for m in match_title("Island Sanctuary", cards)], [(12, "exact_en")])
        self.assertEqual([(m[0], m[2]) for m in match_title("Island Sanctuary NM", cards)], [(12, "exact_en")])

    def test_only_longest_prefix_card_matches(self):
        cards = [Card(id=7, name_en="Exploration"), Card(id=13, name_en="Exploration 2XM")]
        matches = match_title("Exploration 2XM #167", cards)
        self.assertEqual([(match[0], match[2]) for match in matches], [(13, "prefix_en")])

    def test_russian_prefix(self):
        matches = match_title("Тармогойф FUT #153 RU NM", CARDS)
        self.assertEqual([(match[0], match[2]) for match in matches], [(3, "prefix_ru")])

    def test_russian_language_flag_tail(self):
        cards = CARDS + [Card(id=9, name_en="Ranger-Captain of Eos", name_ru="Капитан Лесничих Эоса")]
        matches = match_title("Капитан Лесничих Эоса РУС подпись", cards)
        self.assertEqual([(match[0], match[2]) for match in matches], [(9, "prefix_ru")])
        self.assertEqual([(m[0], m[2]) for m in match_title("Капитан Лесничих Эоса РУС", cards)],
                         [(9, "exact_ru")])

    def test_composition_prefix(self):
        matches = match_names(["Delighted Halfling LTR #158"], [Card(id=8, name_en="Delighted Halfling")])
        self.assertEqual([(match[0], match[2]) for match in matches], [(8, "composition:prefix_en")])


class CompositionMatchTest(unittest.TestCase):
    def test_composition_names_match_cards(self):
        cards = [*CARDS, Card(id=6, name_en="Blightstep Pathway")]
        matches = match_names(["Blightstep Pathway", "Searstep Pathway"], cards)
        self.assertEqual([(match[0], match[2]) for match in matches], [(6, "composition:exact_en")])

    def test_russian_composition_names(self):
        matches = match_names(["Фойл Тармогойф"], CARDS)
        self.assertEqual([(match[0], match[2]) for match in matches], [(3, "composition:exact_ru")])

    def test_unknown_names_are_ignored(self):
        self.assertEqual(match_names(["Black Lotus"], CARDS), [])

    def test_multi_lot_title_is_still_skipped(self):
        self.assertEqual(match_title("Лот Ультиматумов ( Список внутри)", CARDS), [])

    def test_bullet_separator_splits_title(self):
        candidates = title_candidates("Сезон Удвоения • RU, SP, Ravnica: City of Guilds (RAV), #158")
        self.assertIn("сезон удвоения", candidates)

    def test_pipe_separator_splits_title(self):
        candidates = title_candidates("Lightning Bolt | NM, M10")
        self.assertIn("lightning bolt", candidates)


if __name__ == "__main__":
    unittest.main()
