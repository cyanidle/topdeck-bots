from __future__ import annotations

import os
from dataclasses import dataclass

from topbot.config import load_env_file

from . import REPO_ROOT

DEFAULT_SET_CODES = REPO_ROOT / "cards" / "data" / "set-codes.txt"


@dataclass
class Settings:
    bot_token: str = ""
    telegram_proxy: str = "http://127.0.0.1:1081"
    scryfall_proxy: str = "http://127.0.0.1:1081"
    scryfall_enabled: bool = True
    scryfall_base: str = "https://api.scryfall.com"
    db_path: str = "./data/collection.sqlite3"
    set_codes_path: str = str(DEFAULT_SET_CODES)
    user_agent: str = "collectbot/0.1 (+contact)"
    media_max_mb: int = 20
    scan_stride: int = 2
    log_level: str = "INFO"
    log_format: str = "text"
    log_file: str = ""

    @classmethod
    def from_env(cls, env_file: str = ".env", environ: dict[str, str] | None = None) -> "Settings":
        raw = load_env_file(env_file)
        raw.update(os.environ if environ is None else environ)
        settings = cls()
        for name in ("bot_token", "scryfall_base", "db_path", "set_codes_path",
                     "user_agent", "log_level", "log_format", "log_file"):
            key = name.upper()
            if raw.get(key):
                setattr(settings, name, raw[key])
        for name in ("telegram_proxy", "scryfall_proxy"):
            key = name.upper()
            if key in raw:
                setattr(settings, name, raw[key].strip())
        if raw.get("SCRYFALL_ENABLED"):
            settings.scryfall_enabled = raw["SCRYFALL_ENABLED"] not in ("0", "false", "no")
        if raw.get("MEDIA_MAX_MB"):
            settings.media_max_mb = int(raw["MEDIA_MAX_MB"])
        if raw.get("SCAN_STRIDE"):
            settings.scan_stride = int(raw["SCAN_STRIDE"])
            if settings.scan_stride < 1:
                raise ValueError(f"SCAN_STRIDE must be >= 1, got {settings.scan_stride}")
        return settings

    def redacted(self) -> dict[str, object]:
        shown = dict(self.__dict__)
        if shown.get("bot_token"):
            shown["bot_token"] = "***"
        return shown
