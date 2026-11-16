from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

URGENT_WINDOW = 900


def due_window(remaining_s: int, windows: tuple[int, ...], sent: set[int]) -> int | None:
    """Самое узкое назревшее окно: `remaining <= window`, окно не отправлено и уже́ любого отправленного.

    Окно с остатком меньше половины считается истёкшим и не досылается.
    """
    if remaining_s <= 0:
        return None
    narrowest_sent = min(sent) if sent else None
    firing = [
        window for window in windows
        if window not in sent
        and (narrowest_sent is None or window < narrowest_sent)
        and remaining_s <= window
        and remaining_s >= window // 2
    ]
    if not firing:
        return None
    return min(firing)


def msk_clock(ts: int) -> str:
    local = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(offset_for("Europe/Moscow"))
    return local.strftime("%H:%M")


def humanize_remaining(remaining_s: int) -> str:
    minutes = max(0, int(remaining_s // 60))
    if minutes < 1:
        return "меньше минуты"
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes:02d} мин" if minutes else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч" if hours else f"{days} д"


def is_quiet(now_utc: int, quiet_from: int, quiet_to: int, tz: str = "Europe/Moscow") -> bool:
    local = datetime.fromtimestamp(now_utc, tz=timezone.utc).astimezone(offset_for(tz))
    minute = local.hour * 60 + local.minute
    if quiet_from == quiet_to:
        return False
    if quiet_from < quiet_to:
        return quiet_from <= minute < quiet_to
    return minute >= quiet_from or minute < quiet_to


def offset_for(tz: str) -> timezone:
    if tz in ("Europe/Moscow", "MSK", "UTC+3"):
        return timezone(timedelta(hours=3))
    if tz in ("UTC", "Etc/UTC"):
        return timezone.utc
    raise ValueError(f"unsupported tz: {tz}")


@dataclass(frozen=True)
class QuietDecision:
    send_now: bool
    reason: str


def decide_quiet(now_utc: int, user, window_s: int | None, urgent_bypass: bool = True) -> QuietDecision:
    if getattr(user, "muted_until", None) and user.muted_until and user.muted_until > now_utc:
        return QuietDecision(send_now=False, reason="muted")
    quiet = is_quiet(now_utc, user.quiet_from, user.quiet_to)
    if quiet and not (urgent_bypass and window_s is not None and window_s <= URGENT_WINDOW):
        return QuietDecision(send_now=False, reason="quiet_hours")
    return QuietDecision(send_now=True, reason="ok")
