from __future__ import annotations

import unittest

from topbot.config import Settings
from topbot.clients.scryfall import ScryfallClient


def settings(**environ: str) -> Settings:
    return Settings.from_env(env_file="absent.env", environ=environ)


class ProxySettingTest(unittest.TestCase):
    def test_proxies_default_to_the_local_tunnel(self):
        settings_ = Settings()
        self.assertEqual(settings_.telegram_proxy, "http://127.0.0.1:1081")
        self.assertEqual(settings_.scryfall_proxy, "http://127.0.0.1:1081")

    def test_empty_value_means_direct_connection(self):
        settings_ = settings(TELEGRAM_PROXY="", SCRYFALL_PROXY="")
        self.assertEqual(settings_.telegram_proxy, "")
        self.assertEqual(settings_.scryfall_proxy, "")

    def test_explicit_proxy_is_kept(self):
        settings_ = settings(TELEGRAM_PROXY="socks5://127.0.0.1:1080",
                             SCRYFALL_PROXY="http://10.0.0.1:3128")
        self.assertEqual(settings_.telegram_proxy, "socks5://127.0.0.1:1080")
        self.assertEqual(settings_.scryfall_proxy, "http://10.0.0.1:3128")

    def test_empty_value_is_stripped(self):
        self.assertEqual(settings(SCRYFALL_PROXY="  ").scryfall_proxy, "")


class ScryfallDirectModeTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_proxy_is_sent_as_no_proxy(self):
        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def raise_for_status(self):
                return None

            async def json(self):
                return {"name": "Tarmogoyf"}

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, params=None, json=None, headers=None, proxy=None):
                self.calls.append(proxy)
                return Response()

        session = Session()
        await ScryfallClient(session, proxy="").named_fuzzy("Tarmogoyf")
        self.assertEqual(session.calls, [None])


if __name__ == "__main__":
    unittest.main()
