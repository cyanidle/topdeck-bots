from __future__ import annotations

import json
import unittest

from topbot.clients.topdeck import (
    RECORD_CAP,
    lot_image,
    lot_url,
    parse_auctions,
    parse_finished_table,
    parse_lot_composition,
    parse_offers,
    strip_tags,
)
from topbot.models import FinishedLot


def page(records: list[dict], constructor: str = "ActiveAuctionsVM") -> str:
    payload = json.dumps(records, ensure_ascii=True)
    encoded = json.dumps(payload)
    return f"<html><script>new {constructor}(JSON.parse({encoded}), {{base: '/apps/toptrade'}}).init();</script></html>"


AUCTION = {
    "id": "550142",
    "date_published": "1794773596",
    "date_estimated": "1795374796",
    "lot": "Bayou - Reserved List (Revised Edition, 3ED, REV, EN, №283)",
    "image": "4280f9.jpeg",
    "image_path": "2026/november",
    "start_bid": "500",
    "current_bid": "1488",
    "bid_amount": "5",
    "bin_value": "2000",
    "bin_value_original": "2200",
    "shipping_info_quick": "Нахабино, почта",
    "shipping_info": "<p>Почта</p>",
    "date_published_conv": "15 ноября 2026",
    "date_estimated_conv": "22 ноября 2026",
    "time_left": "6д 22ч",
    "seller": {"id": "13397", "name": "pineapplepen", "city": "Нахабино", "refs": "7"},
}

OFFER_TOPDECK = {
    "source": "topdeck",
    "stamp": "2026-11-15 04:32:52",
    "qty": 2,
    "url": "https://topdeck.ru/forums/topic/442180/",
    "line": "\t2\u00a0\u00a0 <b style='color:#777777'>Tarmogoyf</b> Nest\u00a0\u00a0\u00a0 M3C ENG, NM",
    "seller": {"id": "2049", "name": "Umezawa", "city": "Москва", "refs": "1009"},
    "cost": 100,
    "eng_name": "Tarmogoyf",
    "name": "Tarmogoyf Nest",
    "rus_name": "Тармогойф",
}

OFFER_SHOP = {
    "source": "MagicSpark.shop",
    "seller": "MagicSpark.shop",
    "city": "Москва",
    "url": "https://magicspark.shop/card/fra/116",
    "cost": 371,
    "qty": 1,
    "line": "1 <b>Tarmogoyf</b> (SP, fra-116)",
    "eng_name": "Tarmogoyf",
    "name": "Tarmogoyf",
    "rus_name": "Тармогойф",
    "stamp": "2026-11-15 04:36:01",
}

FINISHED_HTML = """
<table><tr><th>Лот</th><th>Ставка</th><th>Продавец</th><th>Победитель</th><th>Завершен</th></tr>
<tr><td><a href="/apps/toptrade/auctions/548482">Snapcaster Mage ENG (MM3, #050)</a></td>
    <td>1041</td><td><a href="/apps/toptrade/member/8195">132 Modelist</a></td>
    <td><a href="/apps/toptrade/member/29348">14 Николай Бысь</a></td><td>5 ноября 2026</td></tr>
</table>"""


class ParseAuctionsTest(unittest.TestCase):
    def test_absolute_times_and_seller(self):
        lots = parse_auctions(page([AUCTION]))
        self.assertEqual(len(lots), 1)
        lot = lots[0]
        self.assertEqual(lot.id, 550142)
        self.assertEqual(lot.end_ts, 1795374796)
        self.assertEqual(lot.start_ts, 1794773596)
        self.assertEqual(lot.current_bid, 1488)
        self.assertEqual(lot.bid_count, 5)
        self.assertEqual(lot.bin_value, 2000)
        self.assertEqual(lot.seller_id, 13397)
        self.assertEqual(lot.seller_refs, 7)
        self.assertEqual(lot.seller_city, "Нахабино")
        self.assertEqual(lot.image_url, lot_image("2026/november", "4280f9.jpeg"))
        self.assertEqual(lot.url, lot_url(550142))

    def test_broken_payloads(self):
        self.assertEqual(parse_auctions("<html></html>"), [])
        self.assertEqual(parse_auctions(page([])), [])
        self.assertEqual(parse_auctions('<script>JSON.parse("not json")</script>'), [])


class ParseOffersTest(unittest.TestCase):
    def test_object_and_string_seller(self):
        offers = parse_offers(page([OFFER_TOPDECK, OFFER_SHOP], constructor="SinglesSearchVM"))
        self.assertEqual(len(offers), 2)
        first, second = offers
        self.assertEqual((first.seller_id, first.seller_name, first.city), (2049, "Umezawa", "Москва"))
        self.assertIsNone(second.seller_id)
        self.assertEqual(second.seller_name, "MagicSpark.shop")
        self.assertEqual(second.city, "Москва")
        self.assertNotIn("<b", first.line)
        self.assertEqual(first.rus_name, "Тармогойф")
        self.assertEqual((first.eng_name, first.name), ("Tarmogoyf", "Tarmogoyf Nest"))
        self.assertEqual(second.cost, 371)

    def test_cap_constant(self):
        self.assertEqual(RECORD_CAP, 1478)


LOT_PAGE = """
<div class="col-xs-12 tt-item-desc">
  <h4>Описание</h4>
  <p>Состав лота, все на английском:</p>
  <p><a class="topdeck_tooltipCard" href="/apps/cards/Blightstep%20Pathway">Blightstep Pathway</a>
     // <a class="topdeck_tooltipCard" href="/apps/cards/Searstep%20Pathway">Searstep Pathway</a> (KHM, #291, Showcase)</p>
  <p>Bonder's Enclave (IKO,#363, Showcase)</p>
  <p><strong><a class="topdeck_tooltipCard" href="/apps/cards/%D0%A1%D0%B5%D0%B7%D0%BE%D0%BD%20%D1%83%D0%B4%D0%B2%D0%BE%D0%B5%D0%BD%D0%B8%D1%8F">Сезон Удвоения</a> • RU, SP (RAV), #158</strong></p>
</div>
"""


class ParseCompositionTest(unittest.TestCase):
    def test_links_pairs_and_plain_text(self):
        self.assertEqual(parse_lot_composition(LOT_PAGE),
                         ("Blightstep Pathway", "Searstep Pathway", "Bonder's Enclave", "Сезон Удвоения"))

    def test_service_lines_are_skipped(self):
        html = '<div class="tt-item-desc"><p>Состав лота:</p><p>все на английском:</p></div>'
        self.assertEqual(parse_lot_composition(html), ())

    def test_page_without_description_has_no_composition(self):
        self.assertEqual(parse_lot_composition("<html><body>нет описания</body></html>"), ())

    def test_set_name_inside_brackets_is_not_a_card(self):
        html = ('<div class="tt-item-desc"><p><a class="topdeck_tooltipCard" href="/apps/cards/Goblin%20Cadets">'
                'Goblin Cadets</a> (<a class="topdeck_tooltipCard" href="/apps/cards/Urza%27s%20Saga">'
                'Urza&#39;s Saga</a>, #189)</p></div>')
        self.assertEqual(parse_lot_composition(html), ("Goblin Cadets",))

    def test_urls_and_prose_are_not_composition(self):
        html = ('<div class="tt-item-desc"><p>https://scryfall.com/card/2x2/10/emiel-the-blessed</p>'
                '<p>Состояние NM/EX</p><p>Bayou</p></div>')
        self.assertEqual(parse_lot_composition(html), ("Bayou",))


class ParseFinishedTest(unittest.TestCase):
    def test_table_row(self):
        rows = parse_finished_table(FINISHED_HTML)
        self.assertEqual(rows, [FinishedLot(
            title="Snapcaster Mage ENG (MM3, #050)",
            final_price=1041,
            seller="132 Modelist",
            winner="14 Николай Бысь",
            finished_on="5 ноября 2026",
            lot_id=548482,
        )])

    def test_strip_tags(self):
        self.assertEqual(strip_tags("<b>Snap</b>&nbsp;caster"), "Snap&nbsp;caster")


class FetchRetryTest(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self, status: int, text: str = "") -> None:
            self.status = status
            self._text = text

        async def text(self) -> str:
            return self._text

        def raise_for_status(self) -> None:
            if self.status >= 400:
                raise RuntimeError(f"http {self.status}")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    class Session:
        def __init__(self, outcomes: list) -> None:
            self.outcomes = list(outcomes)
            self.calls = 0

        def get(self, url, headers=None):
            self.calls += 1
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    async def test_retries_on_failure_then_succeeds(self):
        from topbot.clients.topdeck import TopdeckClient

        session = self.Session([OSError("boom"), self.Response(200, "ok")])
        client = TopdeckClient(session, throttle_s=0)
        text = await client.fetch_text("/x", retries=1)
        self.assertEqual(text, "ok")
        self.assertEqual(session.calls, 2)

    async def test_raises_after_all_retries(self):
        from topbot.clients.topdeck import TopdeckClient

        session = self.Session([OSError("boom"), OSError("boom")])
        client = TopdeckClient(session, throttle_s=0)
        with self.assertRaises(OSError):
            await client.fetch_text("/x", retries=1)
        self.assertEqual(session.calls, 2)


if __name__ == "__main__":
    unittest.main()
