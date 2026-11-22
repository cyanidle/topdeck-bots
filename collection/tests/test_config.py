from __future__ import annotations

import unittest

from collectbot.config import Settings


class SettingsFromEnvTest(unittest.TestCase):
    def test_defaults(self):
        settings = Settings.from_env(env_file="/nonexistent/.env", environ={})
        self.assertEqual(settings.scan_stride, 2)
        self.assertEqual(settings.media_max_mb, 20)

    def test_scan_stride_override(self):
        settings = Settings.from_env(env_file="/nonexistent/.env", environ={"SCAN_STRIDE": "1"})
        self.assertEqual(settings.scan_stride, 1)

    def test_bad_stride_fails_loudly(self):
        with self.assertRaises(ValueError):
            Settings.from_env(env_file="/nonexistent/.env", environ={"SCAN_STRIDE": "x"})
        with self.assertRaises(ValueError):
            Settings.from_env(env_file="/nonexistent/.env", environ={"SCAN_STRIDE": "0"})

    def test_token_is_redacted(self):
        settings = Settings.from_env(env_file="/nonexistent/.env",
                                     environ={"BOT_TOKEN": "secret"})
        self.assertEqual(settings.redacted()["bot_token"], "***")
