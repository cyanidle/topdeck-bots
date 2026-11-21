"""Smoke test for the ctypes binding. Run with:

    python3 cards/python/test_cardscan.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cardscan import Scanner, analyze, library_path  # noqa: E402


def sample_image() -> Path | None:
    env = os.environ.get("CARDSCAN_SAMPLE_IMAGE")
    if env and Path(env).is_file():
        return Path(env)
    fallback = Path.home() / "Documents" / "cards.jpg"
    return fallback if fallback.is_file() else None


class CardscanBindingTest(unittest.TestCase):
    def test_library_is_loadable(self) -> None:
        self.assertTrue(library_path().is_file())

    def test_photo_is_recognized(self) -> None:
        image = sample_image()
        if image is None:
            self.skipTest("no sample image (set CARDSCAN_SAMPLE_IMAGE)")
        report = analyze(image)
        self.assertEqual(report["type"], "image")
        self.assertGreaterEqual(report["count"], 1)
        cards = report["cards"]
        self.assertTrue(any(card["set"] == "DDS" for card in cards), cards)
        self.assertTrue(
            any((card["collector"] or "").replace("/", "").startswith("051") for card in cards),
            cards,
        )

    def test_scanner_options_and_set_codes(self) -> None:
        with Scanner(min_track_hits=2) as scanner:
            self.assertTrue(scanner.version)
            scanner.set_set_codes(["dds", "c18", "tmt"])
            image = sample_image()
            if image is None:
                self.skipTest("no sample image (set CARDSCAN_SAMPLE_IMAGE)")
            report = scanner.analyze(image)
            self.assertIn("cards", report)

    def test_unknown_option_is_rejected(self) -> None:
        with Scanner() as scanner:
            with self.assertRaises(Exception):
                scanner.set_option("no_such_option", 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
