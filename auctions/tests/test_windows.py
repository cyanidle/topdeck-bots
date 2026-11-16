from __future__ import annotations

import unittest
from datetime import datetime, timezone

from topbot.windows import (
    decide_quiet,
    due_window,
    humanize_remaining,
    is_quiet,
)

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


class FakeUser:
    def __init__(self, quiet_from=1380, quiet_to=540, muted_until=None):
        self.quiet_from = quiet_from
        self.quiet_to = quiet_to
        self.muted_until = muted_until


def msk(year, month, day, hour, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()) - 3 * 3600


class QuietTest(unittest.TestCase):
    def test_midnight_is_quiet(self):
        self.assertTrue(is_quiet(msk(2026, 11, 16, 0, 30), 1380, 540))

    def test_noon_is_loud(self):
        self.assertFalse(is_quiet(msk(2026, 11, 16, 12, 0), 1380, 540))

    def test_urgent_window_bypasses_quiet_hours(self):
        now = msk(2026, 11, 16, 1, 0)
        self.assertFalse(decide_quiet(now, FakeUser(), 3600).send_now)
        self.assertTrue(decide_quiet(now, FakeUser(), 900).send_now)

    def test_muted_user(self):
        now = msk(2026, 11, 16, 12, 0)
        self.assertFalse(decide_quiet(now, FakeUser(muted_until=now + 60), 900).send_now)

    def test_wrong_tz_is_loud(self):
        with self.assertRaises(ValueError):
            is_quiet(0, 1380, 540, tz="Mars/Olympus")


if __name__ == "__main__":
    unittest.main()
