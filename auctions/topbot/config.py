from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_WINDOWS = (86400, 10800, 3600, 900)


def load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _int_list(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.replace(" ", "").split(",") if part)


@dataclass
class Settings:
    bot_token: str = ""
    admin_ids: tuple[int, ...] = ()
    admin_usernames: tuple[str, ...] = ()
    telegram_proxy: str = "http://127.0.0.1:1081"
    scryfall_proxy: str = "http://127.0.0.1:1081"
    scryfall_enabled: bool = True
    scryfall_base: str = "https://api.scryfall.com"
    topdeck_base: str = "https://topdeck.ru"
    web_bind: str = "127.0.0.1"
    web_port: int = 8080
    db_path: str = "./data/topbot.sqlite3"
    user_agent: str = "topbot/0.1 (+contact)"
    poll_interval_s: int = 840
    prices_interval_s: int = 900
    windows: tuple[int, ...] = DEFAULT_WINDOWS
    quiet_from: int = 23 * 60
    quiet_to: int = 9 * 60
    throttle_s: float = 1.5
    log_level: str = "INFO"
    log_format: str = "text"
    log_file: str = ""

    @classmethod
    def from_env(cls, env_file: str = ".env", environ: dict[str, str] | None = None) -> "Settings":
        raw = load_env_file(env_file)
        raw.update(os.environ if environ is None else environ)
        settings = cls()
        for name in ("bot_token", "telegram_proxy", "scryfall_proxy", "scryfall_base", "topdeck_base",
                     "web_bind", "db_path", "user_agent", "log_level", "log_format", "log_file"):
            key = name.upper()
            if raw.get(key):
                setattr(settings, name, raw[key])
        if raw.get("ADMIN_TG_IDS"):
            settings.admin_ids = _int_list(raw["ADMIN_TG_IDS"])
        if raw.get("ADMIN_USERNAMES"):
            settings.admin_usernames = tuple(
                name.strip().lstrip("@").lower() for name in raw["ADMIN_USERNAMES"].split(",") if name.strip()
            )
        if raw.get("SCRYFALL_ENABLED"):
            settings.scryfall_enabled = raw["SCRYFALL_ENABLED"] not in ("0", "false", "no")
        for name in ("web_port", "poll_interval_s", "prices_interval_s", "quiet_from", "quiet_to"):
            key = name.upper()
            if raw.get(key):
                setattr(settings, name, int(raw[key]))
        if raw.get("NOTIFY_WINDOWS"):
            settings.windows = _int_list(raw["NOTIFY_WINDOWS"])
        if raw.get("THROTTLE_S"):
            settings.throttle_s = float(raw["THROTTLE_S"])
        return settings

    def redacted(self) -> dict[str, object]:
        shown = dict(self.__dict__)
        if shown.get("bot_token"):
            shown["bot_token"] = "***"
        return shown