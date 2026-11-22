from __future__ import annotations

import unittest
from datetime import datetime, timezone

from aiogram.types import Chat, Document, Message, PhotoSize, VideoNote

from topbot.models import Card

from collectbot.bot import classify_media, entry_line, fit_lines, media_info, parse_callback, \
    render_collection
from collectbot.models import CollectionEntry


def message_with(**kwargs) -> Message:
    return Message(message_id=1, date=datetime(2026, 11, 22, tzinfo=timezone.utc),
                   chat=Chat(id=1, type="private"), **kwargs)


class MediaInfoTest(unittest.TestCase):
    def test_photo(self):
        photo = PhotoSize(file_id="f1", file_unique_id="u1", width=1280, height=720,
                          file_size=123456)
        info = media_info(message_with(photo=[photo]))
        self.assertEqual(info[:3], ("фото", ".jpg", 123456))
        self.assertIs(info[3], photo)

    def test_video_note(self):
        note = VideoNote(file_id="f2", file_unique_id="u2", length=384, duration=15,
                         file_size=2 * 1024 * 1024)
        info = media_info(message_with(video_note=note))
        self.assertEqual(info[:3], ("видео-кружок", ".mp4", 2 * 1024 * 1024))
        self.assertIs(info[3], note)

    def test_document_image(self):
        doc = Document(file_id="f3", file_unique_id="u3", file_name="card.jpg",
                       mime_type="image/jpeg", file_size=999)
        info = media_info(message_with(document=doc))
        self.assertEqual(info[:3], ("фото", ".jpg", 999))

    def test_unsupported_document(self):
        doc = Document(file_id="f4", file_unique_id="u4", file_name="doc.pdf",
                       mime_type="application/pdf")
        self.assertIsNone(media_info(message_with(document=doc)))


def entry(entry_id: int, set_code: str, number: str, count: int) -> CollectionEntry:
    return CollectionEntry(id=entry_id, user_id=7, set_code=set_code, number=number,
                           count=count, created_at=0, updated_at=0)


class ClassifyMediaTest(unittest.TestCase):
    def test_no_hints_is_rejected(self):
        self.assertEqual(classify_media(None, None, is_video=False), None)

    def test_image_files(self):
        self.assertEqual(classify_media("cards.jpg", None, False), ("фото", ".jpg"))
        self.assertEqual(classify_media("cards.PNG", None, False), ("фото", ".png"))
        self.assertEqual(classify_media(None, "image/jpeg", False), ("фото", ".jpg"))
        self.assertEqual(classify_media("card", "image/webp", False), ("фото", ".jpg"))

    def test_video_files(self):
        self.assertEqual(classify_media("clip.mp4", None, False), ("видео", ".mp4"))
        self.assertEqual(classify_media("VID.MOV", None, False), ("видео", ".mov"))
        self.assertEqual(classify_media(None, "video/mp4", True), ("видео", ".mp4"))
        self.assertEqual(classify_media("clip.bin", "video/x-matroska", False), ("видео", ".mp4"))

    def test_unsupported(self):
        self.assertIsNone(classify_media("doc.pdf", "application/pdf", False))
        self.assertIsNone(classify_media(None, "text/plain", False))


class EntryLineTest(unittest.TestCase):
    def test_with_ru_name(self):
        card = Card(name_en="Ninja Teen", name_ru="Ниндзя-подросток", set_code="TMT", collector_no="67")
        self.assertEqual(entry_line(entry(1, "TMT", "67", 2), card),
                         "Ниндзя-подросток (TMT:67) ×2")

    def test_without_card(self):
        self.assertEqual(entry_line(entry(1, "TMT", "67", 1), None), "TMT:67 ×1")


class RenderCollectionTest(unittest.TestCase):
    def test_groups_by_set(self):
        entries = [entry(1, "WAR", "149", 1), entry(2, "DDS", "51", 2), entry(3, "WAR", "150", 1)]
        names = {(entry_.set_code, entry_.number): None for entry_ in entries}
        text = render_collection(entries, lambda e: names.get((e.set_code, e.number)))
        self.assertIn("Коллекция: 4 карты, 3 записи", text)
        self.assertIn("DDS", text)
        self.assertIn("DDS:51 ×2", text)
        self.assertLess(text.index("DDS"), text.index("WAR"))

    def test_empty(self):
        self.assertEqual(render_collection([], lambda e: None), "Коллекция: 0 карт, 0 записей")

    def test_plural_forms(self):
        text = render_collection([entry(1, "TMT", "67", 1)], lambda e: None)
        self.assertIn("1 карта, 1 запись", text)
        text = render_collection([entry(1, "TMT", "67", 2), entry(2, "WAR", "149", 1)], lambda e: None)
        self.assertIn("3 карты, 2 записи", text)


class ParseCallbackTest(unittest.TestCase):
    def test_count(self):
        self.assertEqual(parse_callback("cnt:12:-1"), ("cnt", 12, -1))
        self.assertEqual(parse_callback("cnt:3:1"), ("cnt", 3, 1))

    def test_delete(self):
        self.assertEqual(parse_callback("del:12"), ("del", 12, 0))

    def test_garbage(self):
        self.assertIsNone(parse_callback("cnt:"))
        self.assertIsNone(parse_callback("cnt:x:1"))
        self.assertIsNone(parse_callback("del:y"))
        self.assertIsNone(parse_callback("other:1"))
        self.assertIsNone(parse_callback(""))


class FitLinesTest(unittest.TestCase):
    def test_all_fit(self):
        shown, hidden = fit_lines(["a", "b"], 100)
        self.assertEqual((shown, hidden), (["a", "b"], 0))

    def test_truncates_at_line_boundary(self):
        lines = [f"line-{index}" for index in range(100)]
        shown, hidden = fit_lines(lines, 50)
        self.assertEqual(hidden, 100 - len(shown))
        self.assertLessEqual(sum(len(line) + 1 for line in shown), 50)
        self.assertEqual(shown, lines[:len(shown)])

    def test_single_long_line_is_kept(self):
        shown, hidden = fit_lines(["x" * 100], 10)
        self.assertEqual((shown, hidden), (["x" * 100], 0))
