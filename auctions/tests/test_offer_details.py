from __future__ import annotations

import unittest

from topbot.messages import offer_details, render_offer_line
from topbot.models import Offer


def offer(line: str, eng_name: str = "Snapcaster Mage", rus_name: str | None = None, *,
          source: str = "topdeck", seller_name: str | None = "Umezawa", cost: int = 1486,
          qty: int = 1) -> Offer:
    return Offer(eng_name=eng_name, rus_name=rus_name, name=eng_name, source=source,
                 seller_name=seller_name, seller_id=1, city="Москва", cost=cost, qty=qty,
                 url="https://topdeck.ru/x", line=line, stamp=None)


class OfferDetailsTest(unittest.TestCase):
    def test_shop_line_keeps_set_number_and_condition(self):
        self.assertEqual(offer_details(offer("1 Snapcaster Mage (SP, isd-78)")), "(SP, isd-78)")

    def test_topdeck_line_with_set_condition_and_reserve(self):
        self.assertEqual(offer_details(offer("1x [EN] Snapcaster Mage 1100 ISD (РЕЗЕРВ)")), "[EN] ISD (РЕЗЕРВ)")

    def test_tabbed_line_is_collapsed_and_codes_uppercased(self):
        line = "3\t\tSnapcaster Mage\t\t1 460\t\tuma\t\ten\t\tnm"
        self.assertEqual(offer_details(offer(line)), "uma EN NM")

    def test_price_inside_text_is_dropped(self):
        self.assertEqual(offer_details(offer("1x EN isd Snapcaster Mage 1483р. SP")), "EN isd SP")

    def test_card_name_is_removed_for_russian_listing(self):
        details = offer_details(offer("1 Фойл Тармогойф (FUT, 153, RU, NM)", eng_name="Tarmogoyf",
                                      rus_name="Тармогойф"))
        self.assertEqual(details, "Фойл (FUT, 153, RU, NM)")

    def test_price_written_with_rubles_word_is_dropped(self):
        self.assertEqual(offer_details(offer("Snapcaster Mage 1500 рублей (UMA) #71")), "(UMA) #71")

    def test_trailing_price_marks_are_cleaned(self):
        self.assertEqual(offer_details(offer("1 Snapcaster Mage ISD - 1500 *")), "ISD")

    def test_leading_hash_without_number_is_dropped(self):
        self.assertEqual(offer_details(offer("# MH2 RU NM")), "MH2 RU NM")

    def test_grade_modifier_inside_parentheses_is_uppercased(self):
        self.assertEqual(offer_details(offer("1 Snapcaster Mage (EN, mh2, nm-)")), "(EN, mh2, NM-)")

    def test_empty_line_gives_empty_details(self):
        self.assertEqual(offer_details(offer("1 Snapcaster Mage")), "")

    def test_long_details_are_truncated(self):
        details = offer_details(offer("1 Snapcaster Mage " + "x" * 90))
        self.assertLessEqual(len(details), 48)
        self.assertTrue(details.endswith("…"))


class RenderOfferLineTest(unittest.TestCase):
    def test_line_contains_price_seller_and_details(self):
        text = render_offer_line(offer("1 Snapcaster Mage (NM, uma-71)", seller_name="Umezawa", cost=1486, qty=2))
        self.assertIn("1\u00a0486 ₽ ×2", text)
        self.assertIn("Umezawa", text)
        self.assertIn("(topdeck)", text)
        self.assertTrue(text.endswith("(NM, uma-71)"))

    def test_marketplace_source_is_not_repeated(self):
        text = render_offer_line(offer("1 Snapcaster Mage (SP, isd-78)", source="MtgRussia.com",
                                       seller_name=None, cost=1486))
        self.assertEqual(text.count("MtgRussia.com"), 1)
        self.assertIn("MtgRussia.com — (SP, isd-78)", text)

    def test_no_details_leaves_short_line(self):
        text = render_offer_line(offer("1 Snapcaster Mage", cost=1000))
        self.assertEqual(text, "1\u00a0000 ₽ ×1 — Umezawa (topdeck)")


if __name__ == "__main__":
    unittest.main()
