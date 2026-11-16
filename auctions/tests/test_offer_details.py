from __future__ import annotations

import unittest

from topbot.messages import (INLINE_OFFERS, offer_details, offers_line, plural, render_auction_message,
                             render_offer_line, seller_label)
from topbot.models import Lot, Offer


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

    def test_empty_parentheses_are_removed(self):
        self.assertEqual(offer_details(offer("1 Snapcaster Mage 1500 ()")), "")
        self.assertEqual(offer_details(offer("1x Snapcaster Mage (1500)")), "")
        self.assertEqual(offer_details(offer("1 Snapcaster Mage 1 500 ₽ ( ) #")), "")
        self.assertEqual(offer_details(offer("1 Snapcaster Mage ( 1500 , )")), "")

    def test_only_trailing_price_leaves_no_stray_punctuation(self):
        self.assertEqual(offer_details(offer("1 Snapcaster Mage 1 500 ₽ -")), "")
        self.assertEqual(offer_details(offer("Snapcaster Mage –")), "")

    def test_hash_left_after_dropped_number_is_cleaned(self):
        line = "1\u00a0Snapcaster Mage (#0158, LTR, EN, M/NM)\u00a0- 2950"
        self.assertEqual(offer_details(offer(line)), "(LTR, EN, M/NM)")

    def test_latin_p_price_mark_is_dropped(self):
        self.assertEqual(offer_details(offer("3\u00a0Snapcaster Mage (Ice Age, sp+) - 950p.")),
                         "(Ice Age, SP+)")

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


class PluralTest(unittest.TestCase):
    def test_russian_forms(self):
        cases = {1: "лот", 2: "лота", 4: "лота", 5: "лотов", 11: "лотов", 12: "лотов", 21: "лот",
                 22: "лота", 25: "лотов", 101: "лот", 111: "лотов"}
        for count, expected in cases.items():
            self.assertEqual(plural(count, "лот", "лота", "лотов"), expected, count)


class OffersLineTest(unittest.TestCase):
    def test_prices_and_sellers_are_joined_with_dots(self):
        offers = [offer("", seller_name="Bernard", cost=700), offer("", seller_name="Cornelius", cost=800)]
        self.assertEqual(offers_line(offers), "💰 Цены у продавцов: 700 ₽ Bernard · 800 ₽ Cornelius")

    def test_limit_cuts_the_tail(self):
        offers = [offer("", seller_name="Bernard", cost=700), offer("", seller_name="Cornelius", cost=800)]
        self.assertEqual(offers_line(offers, limit=1), "💰 Цены у продавцов: 700 ₽ Bernard")

    def test_default_limit_is_three_offers(self):
        offers = [offer("", seller_name=f"S{index}", cost=700 + index) for index in range(5)]
        self.assertEqual(offers_line(offers), offers_line(offers, limit=INLINE_OFFERS))
        self.assertEqual(len(offers_line(offers).split(" · ")), 3)

    def test_thousands_are_grouped_with_nbsp(self):
        self.assertEqual(offers_line([offer("", seller_name="Bernard", cost=1200)]),
                         "💰 Цены у продавцов: 1\u00a0200 ₽ Bernard")

    def test_long_seller_name_is_shortened(self):
        label = seller_label(offer("", seller_name="VeryLongSellerNicknameHere"))
        self.assertEqual(label, "VeryLongSellerNic…")
        self.assertEqual(len(label), 18)

    def test_short_seller_name_is_kept(self):
        self.assertEqual(seller_label(offer("", seller_name="Umezawa")), "Umezawa")

    def test_source_is_used_when_the_seller_is_unknown(self):
        self.assertEqual(seller_label(offer("", seller_name=None, source="MtgRussia.com")), "MtgRussia.com")


class AuctionMessageTest(unittest.TestCase):
    def lot(self, **overrides) -> Lot:
        fields = dict(id=1, title="Bayou — Reserved List (3ED, EN, №283)", end_ts=1000, start_ts=0,
                      start_bid=500, current_bid=1488, bid_count=5, bin_value=0, seller_id=1,
                      seller_name="Red_clix", seller_city="Краснодар", seller_refs=93,
                      shipping_quick=None, image_url=None, url="https://topdeck.ru/apps/toptrade/auctions/1")
        fields.update(overrides)
        return Lot(**fields)

    def test_prices_go_after_the_bid_and_before_the_seller(self):
        text = render_auction_message(self.lot(), 900, offers=[offer("", seller_name="Bernard", cost=700)])
        lines = text.splitlines()
        self.assertEqual(lines[0], "⏰ 15 мин до конца")
        self.assertEqual(lines[1], "Bayou — Reserved List (3ED, EN, №283)")
        self.assertEqual(lines[2], "Текущая ставка: 1\u00a0488 ₽ · 5 ставок")
        self.assertEqual(lines[3], "💰 Цены у продавцов: 700 ₽ Bernard")
        self.assertEqual(lines[4], "Продавец: Red_clix (Краснодар, 93 отз.)")

    def test_no_offers_means_no_price_line(self):
        text = render_auction_message(self.lot(), 900)
        self.assertNotIn("Цены у продавцов", text)

    def test_card_name_line_stays_above_the_prices(self):
        text = render_auction_message(self.lot(), 900, card_name="Bayou",
                                      offers=[offer("", seller_name="Bernard", cost=700)])
        self.assertLess(text.index("Из вашего списка в лоте: Bayou"), text.index("💰 Цены у продавцов"))

    def test_bin_line_sits_above_the_prices(self):
        text = render_auction_message(self.lot(bin_value=2000), 900,
                                      offers=[offer("", seller_name="Bernard", cost=700)])
        self.assertLess(text.index("Выкуп: 2\u00a0000 ₽"), text.index("💰 Цены у продавцов"))


if __name__ == "__main__":
    unittest.main()
