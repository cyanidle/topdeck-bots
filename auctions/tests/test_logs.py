from __future__ import annotations

import json
import logging
import unittest

from topbot.logs import JsonFormatter, TextFormatter, setup_logging


def make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="topbot.test", level=logging.INFO, pathname=__file__, lineno=10,
        msg="auction poll done", args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class FormatterTest(unittest.TestCase):
    def test_json_has_extras_and_is_valid(self):
        payload = json.loads(JsonFormatter().format(make_record(lots=365, ms=812, delivered=True)))
        self.assertEqual(payload["logger"], "topbot.test")
        self.assertEqual(payload["msg"], "auction poll done")
        self.assertEqual(payload["lots"], 365)
        self.assertEqual(payload["ms"], 812)
        self.assertTrue(payload["delivered"])
        self.assertIn("ts", payload)
        self.assertEqual(payload["level"], "INFO")

    def test_json_serialises_unknown_types(self):
        payload = json.loads(JsonFormatter().format(make_record(obj=object())))
        self.assertIn("obj", payload)

    def test_text_appends_extras(self):
        line = TextFormatter("%(levelname)s %(name)s %(message)s").format(make_record(lots=5, path="/apps/x"))
        self.assertIn("auction poll done", line)
        self.assertIn("lots=5", line)
        self.assertIn("path=/apps/x", line)

    def test_text_without_extras_is_plain(self):
        line = TextFormatter("%(levelname)s %(message)s").format(make_record())
        self.assertEqual(line, "INFO auction poll done")


class SetupTest(unittest.TestCase):
    def tearDown(self) -> None:
        setup_logging("CRITICAL")

    def test_no_duplicate_handlers(self):
        setup_logging("DEBUG")
        setup_logging("DEBUG")
        root = logging.getLogger()
        self.assertEqual(len([h for h in root.handlers if isinstance(h, logging.StreamHandler)]), 1)
        self.assertEqual(root.level, logging.DEBUG)

    def test_json_file_handler_writes_json(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "logs" / "bot.jsonl"
            setup_logging("INFO", "text", str(target))
            logging.getLogger("topbot.test").info("hello", extra={"lots": 3})
            logging.shutdown()
            line = target.read_text(encoding="utf-8").strip().splitlines()[-1]
            payload = json.loads(line)
            self.assertEqual(payload["msg"], "hello")
            self.assertEqual(payload["lots"], 3)

    def test_aiogram_noise_is_downgraded(self):
        setup_logging("INFO")
        self.assertEqual(logging.getLogger("aiogram.event").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
