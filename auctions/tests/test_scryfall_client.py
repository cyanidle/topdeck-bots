from __future__ import annotations

import unittest

from topbot.clients.scryfall import ScryfallClient


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None, headers: dict | None = None):
        self.status = status
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"http {self.status}")

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, params=None, json=None, headers=None, proxy=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json,
                           "headers": headers, "proxy": proxy})
        return self.responses.pop(0)


CARD = {
    "name": "Tarmogoyf",
    "printed_name": "Тармогойф",
    "oracle_id": "oracle-1",
    "id": "scry-1",
    "set": "m3c",
    "collector_number": "120",
    "image_uris": {"small": "https://cards.scryfall.io/small/x.jpg"},
    "artist": "Ryan Barger",
}


class ScryfallClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_headers_and_mapping(self):
        session = FakeSession([FakeResponse(200, CARD)])
        client = ScryfallClient(session, user_agent="topbot/0.1 (+test)", min_interval_s=0)
        card = await client.named_fuzzy("Tarmogoyf")
        self.assertEqual(card.name_en, "Tarmogoyf")
        self.assertEqual(card.name_ru, "Тармогойф")
        self.assertEqual(card.image_url, "https://cards.scryfall.io/small/x.jpg")
        self.assertEqual(card.artist, "Ryan Barger")
        call = session.calls[0]
        self.assertEqual(call["headers"]["User-Agent"], "topbot/0.1 (+test)")
        self.assertIn("application/json", call["headers"]["Accept"])
        self.assertEqual(call["proxy"], "http://127.0.0.1:1081")
        self.assertEqual(call["params"], {"fuzzy": "Tarmogoyf"})

    async def test_localized_name_requests_russian_printing(self):
        session = FakeSession([FakeResponse(200, {"data": [CARD]})])
        client = ScryfallClient(session, min_interval_s=0)
        self.assertEqual(await client.localized_name("Tarmogoyf"), "Тармогойф")
        self.assertEqual(session.calls[0]["params"], {
            "q": '!"Tarmogoyf" lang:ru', "unique": "cards", "include_multilingual": "true", "order": "name",
        })

    async def test_localized_name_returns_none_without_printed_name(self):
        english_only = {key: value for key, value in CARD.items() if key != "printed_name"}
        session = FakeSession([FakeResponse(200, {"data": [english_only]})])
        client = ScryfallClient(session, min_interval_s=0)
        self.assertIsNone(await client.localized_name("Tarmogoyf"))

    async def test_404_returns_none(self):
        session = FakeSession([FakeResponse(404)])
        client = ScryfallClient(session, min_interval_s=0)
        self.assertIsNone(await client.named_fuzzy("Nonexistent Card"))

    async def test_429_is_retried(self):
        session = FakeSession([FakeResponse(429, headers={"Retry-After": "0"}), FakeResponse(200, CARD)])
        client = ScryfallClient(session, min_interval_s=0)
        card = await client.named_fuzzy("Tarmogoyf")
        self.assertEqual(card.name_en, "Tarmogoyf")
        self.assertEqual(len(session.calls), 2)

    async def test_collection_limit(self):
        client = ScryfallClient(FakeSession([]), min_interval_s=0)
        with self.assertRaises(ValueError):
            await client.collection([{"id": str(i)} for i in range(76)])

    async def test_search_empty_payload(self):
        session = FakeSession([FakeResponse(404)])
        client = ScryfallClient(session, min_interval_s=0)
        self.assertEqual(await client.search("zzz"), [])

    async def test_autocomplete(self):
        session = FakeSession([FakeResponse(200, {"object": "catalog", "data": ["Tarmogoyf", "Tarmogoyf Nest"]})])
        client = ScryfallClient(session, min_interval_s=0)
        names = await client.autocomplete("tarmo")
        self.assertEqual(names, ["Tarmogoyf", "Tarmogoyf Nest"])
        self.assertEqual(session.calls[0]["params"], {"q": "tarmo", "include_extras": "false"})

    async def test_card_names_catalog(self):
        session = FakeSession([FakeResponse(200, {"data": ["Bayou", "Tarmogoyf"]})])
        client = ScryfallClient(session, min_interval_s=0)
        self.assertEqual(await client.card_names(), ["Bayou", "Tarmogoyf"])

    async def test_named_exact_uses_exact_parameter(self):
        session = FakeSession([FakeResponse(200, CARD)])
        client = ScryfallClient(session, min_interval_s=0)
        await client.named_exact("Tarmogoyf")
        self.assertEqual(session.calls[0]["params"], {"exact": "Tarmogoyf"})


if __name__ == "__main__":
    unittest.main()
