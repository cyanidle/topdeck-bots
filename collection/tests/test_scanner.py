from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from collectbot.scanner import load_set_codes, report_cards, scan_media

SAMPLE_REPORT = {
    "type": "image",
    "count": 2,
    "identified": 1,
    "cards": [
        {"set": "DDS", "collector_number": "051", "identity": "DDS:51", "identified": True,
         "identified_by": "codes", "confidence": 0.92, "name": None},
        {"set": "", "collector_number": "", "identity": "", "identified": False,
         "identified_by": None, "confidence": 0.1, "name": None},
    ],
}


class ReportCardsTest(unittest.TestCase):
    def test_prints_vs_unknown(self):
        cards = report_cards(SAMPLE_REPORT)
        prints = [card for card in cards if card.set_code and card.number]
        self.assertEqual([(card.set_code, card.number) for card in prints], [("DDS", "51")])
        self.assertEqual(len(cards) - len(prints), 1)

    def test_empty_report(self):
        self.assertEqual(report_cards({}), [])

    def test_leading_zeros_stripped(self):
        cards = report_cards({"cards": [{"set": "HOB", "collector_number": "0082",
                                         "identity": "HOB:82", "identified": True,
                                         "identified_by": "codes", "confidence": 0.7}]})
        self.assertEqual(cards[0].number, "82")


class SetCodesTest(unittest.TestCase):
    def test_loads_dedupes_and_uppercases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codes.txt"
            path.write_text("dds\nc18\n\nTMT\ndds\n", encoding="utf-8")
            self.assertEqual(load_set_codes(path), ["C18", "DDS", "TMT"])

    def test_missing_file_is_empty(self):
        self.assertEqual(load_set_codes(Path("/nonexistent") / "codes.txt"), [])


class LiveScanTest(unittest.TestCase):
    def test_sample_image_scan(self):
        image = Path(os.environ.get("CARDSCAN_SAMPLE_IMAGE",
                                    str(Path.home() / "Documents" / "cards.jpg")))
        if not image.is_file():
            self.skipTest("no sample image (set CARDSCAN_SAMPLE_IMAGE)")
        report = scan_media(image, ["DDS", "C18", "TMT"])
        cards = report_cards(report)
        prints = [card for card in cards if card.set_code and card.number]
        self.assertTrue(any(card.set_code == "DDS" for card in prints), cards)
