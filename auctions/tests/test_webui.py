from __future__ import annotations

import unittest

from topbot.db import Database
from topbot.webui import health_payload

NOW = 1_795_400_000


class HealthPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")

    def tearDown(self) -> None:
        self.db.close()

    def payload(self) -> dict:
        return health_payload(self.db, now=NOW)

    def test_empty_database_is_healthy(self):
        payload = self.payload()
        self.assertEqual(payload["status"], "ok")
        self.assertIsNone(payload["last_auction_poll_age_s"])
        self.assertIsNone(payload["last_nonempty_poll_age_s"])

    def test_last_poll_with_lots_is_healthy(self):
        self.db.record_poll("auctions", NOW - 30, 1200, True, 438)
        payload = self.payload()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["last_auction_poll_age_s"], 30)
        self.assertEqual(payload["last_nonempty_poll_age_s"], 30)

    def test_failed_poll_is_degraded(self):
        self.db.record_poll("auctions", NOW - 30, 60090, False, 0, "TimeoutError()")
        self.assertEqual(self.payload()["status"], "degraded")

    def test_empty_poll_is_degraded_and_keeps_the_nonempty_age(self):
        self.db.record_poll("auctions", NOW - 600, 1200, True, 437)
        self.db.record_poll("auctions", NOW - 60, 160, True, 0)
        payload = self.payload()
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["last_auction_poll_age_s"], 60)
        self.assertEqual(payload["last_nonempty_poll_age_s"], 600)
        self.assertEqual(payload["lots_active"], 0)

    def test_price_poll_age_is_reported(self):
        self.db.record_poll("prices", NOW - 90, 20000, True, 30)
        self.assertEqual(self.payload()["last_price_poll_age_s"], 90)


if __name__ == "__main__":
    unittest.main()
