from __future__ import annotations

import unittest

from topbot.windows import due_window, humanize_remaining

WINDOWS = (86400, 10800, 3600, 900)


class DueWindowTest(unittest.TestCase):
    def test_too_far_away(self):
        self.assertIsNone(due_window(90000, WINDOWS, set()))

    def test_day_window_fires_at_its_boundary(self):
        self.assertEqual(due_window(86400, WINDOWS, set()), 86400)
        self.assertIsNone(due_window(86400 + 1, WINDOWS, set()))

    def test_already_sent_is_skipped(self):
        self.assertEqual(due_window(3000, WINDOWS, {86400}), 3600)

    def test_narrowest_window_wins(self):
        self.assertEqual(due_window(700, WINDOWS, set()), 900)

    def test_stale_window_is_dropped(self):
        self.assertIsNone(due_window(6 * 3600, WINDOWS, set()))
        self.assertEqual(due_window(13 * 3600, WINDOWS, set()), 86400)

    def test_broader_window_is_not_sent_after_narrower(self):
        self.assertIsNone(due_window(10800, WINDOWS, {10800}))
        self.assertIsNone(due_window(800, WINDOWS, {900}))

    def test_narrower_window_still_fires_after_broader(self):
        self.assertEqual(due_window(3000, WINDOWS, {86400}), 3600)
        self.assertEqual(due_window(600, WINDOWS, {86400, 10800, 3600}), 900)

    def test_all_windows_sent(self):
        self.assertIsNone(due_window(600, WINDOWS, {86400, 10800, 3600, 900}))

    def test_finished_or_past(self):
        self.assertIsNone(due_window(0, WINDOWS, set()))
        self.assertIsNone(due_window(-42, WINDOWS, set()))

    def test_urgent_window_fires_only_inside_its_range(self):
        self.assertIsNone(due_window(901, WINDOWS, {86400, 10800, 3600}))
        self.assertEqual(due_window(899, WINDOWS, {86400, 10800, 3600}), 900)


class HumanizeTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(humanize_remaining(20), "меньше минуты")
        self.assertEqual(humanize_remaining(900), "15 мин")
        self.assertEqual(humanize_remaining(3600), "1 ч")
        self.assertEqual(humanize_remaining(5400), "1 ч 30 мин")
        self.assertEqual(humanize_remaining(86400), "1 д")
        self.assertEqual(humanize_remaining(90000), "1 д 1 ч")


if __name__ == "__main__":
    unittest.main()
