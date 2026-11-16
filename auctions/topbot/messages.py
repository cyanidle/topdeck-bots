from __future__ import annotations

import re

from .models import Lot, Offer
from .windows import humanize_remaining

NBSP = "\u00a0"

QTY_PREFIX = re.compile(r"^\s*\d+\s*[xхXХ]?\s+")
PRICE_TOKEN = re.compile(r"\d[\d\s\u00a0]*\s*(?:₽|руб(?:лей|ля|\.)?|rub\.?|р\.)", re.IGNORECASE)
PRICE_GROUP = re.compile(r"\b\d{1,3}(?:[\s\u00a0]\d{3})+\b|\b\d{3,}\b")
CONDITIONS = {"nm": "NM", "sp": "SP", "ex": "EX", "gd": "GD", "lp": "LP", "mp": "MP",
              "hp": "HP", "pl": "PL", "vg": "VG"}
LANGUAGES = {"en": "EN", "eng": "EN", "ru": "RU", "rus": "RU", "jp": "JP", "jpn": "JP", "fr": "FR",
             "fra": "FR", "it": "IT", "ita": "IT", "de": "DE", "ger": "DE", "es": "ES", "spa": "ES",
             "pt": "PT", "kor": "KR", "kr": "KR", "zhs": "ZH", "zht": "ZH"}
CODES = {**CONDITIONS, **LANGUAGES}
CODE_PATTERN = re.compile(r"(?<![\w-])(" + "|".join(sorted(CODES, key=len, reverse=True)) + r")(?![\w-])",
                          re.IGNORECASE)
GRADE_MODIFIER = re.compile(r"(?<![\w-])(" + "|".join(CONDITIONS) + r")(?=[-+])", re.IGNORECASE)


def money(value: int | None) -> str:
    return f"{int(value or 0):,}".replace(",", NBSP)


def plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def offer_details(offer: Offer, limit: int = 48) -> str:
    text = (offer.line or "").replace(NBSP, " ")
    text = QTY_PREFIX.sub("", text)
    for name in (offer.eng_name, offer.rus_name, offer.name):
        if name:
            text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    text = PRICE_TOKEN.sub(" ", text)
    segments = []
    for part in re.split(r"(\([^)]*\))", text):
        if part.startswith("(") and part.endswith(")"):
            segments.append(re.sub(r"\b\d{4,}\b", " ", part))
        else:
            segments.append(PRICE_GROUP.sub(" ", part))
    text = "".join(segments)
    text = CODE_PATTERN.sub(lambda match: CODES[match.group(1).lower()], text)
    text = GRADE_MODIFIER.sub(lambda match: CONDITIONS[match.group(1).lower()], text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r",\s*(?=[),])", "", text)
    text = re.sub(r"\(\s*", "(", text)
    text = re.sub(r"\s*\)", ")", text)
    details = re.sub(r"\s{2,}", " ", text).strip(" -,;:")
    details = re.sub(r"^[#№]\s*", "", details)
    details = re.sub(r"[\s\-–—*]+$", "", details).strip(" -,;:")
    return details if len(details) <= limit else details[:limit - 1].rstrip() + "…"


def render_auction_message(lot: Lot, remaining_s: int, card_name: str | None = None) -> str:
    bids = lot.bid_count
    lines = [f"⏰ {humanize_remaining(remaining_s)} до конца"]
    lines.append(lot.title)
    if card_name:
        lines.append(f"Из вашего списка в лоте: {card_name}")
    bid_word = "ставок" if bids % 10 in (0, 5, 6, 7, 8, 9) or 11 <= bids % 100 <= 14 else ("ставка" if bids % 10 == 1 else "ставки")
    lines.append(f"Текущая ставка: {money(lot.price)} ₽ · {bids} {bid_word}")
    if lot.bin_value > 0:
        lines.append(f"Выкуп: {money(lot.bin_value)} ₽")
    seller_line = f"Продавец: {lot.seller_name or '—'}"
    if lot.seller_city:
        seller_line += f" ({lot.seller_city}"
        seller_line += f", {lot.seller_refs} отз.)" if lot.seller_refs else ")"
    lines.append(seller_line)
    lines.append(lot.url)
    return "\n".join(lines)


def render_offer_line(offer: Offer) -> str:
    who = offer.seller_name or offer.source or "—"
    source = "" if who == offer.source else f" ({offer.source})"
    details = offer_details(offer)
    line = f"{money(offer.cost)} ₽ ×{offer.qty} — {who}{source}"
    return f"{line} — {details}" if details else line
