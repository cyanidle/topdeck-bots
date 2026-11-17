from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .matching import normalize

CLEAN_VERSION = 1

METADATA_PARENS = re.compile(r"\([^)]*\d[^)]*\)")
PARENS = re.compile(r"\(([^)]*)\)")
PAREN_PARTS = re.compile(r"[,;]")
DIGITS = re.compile(r"\d")
URL_LIKE = re.compile(r"https?:|www\.|\b[a-z0-9-]+\.(?:com|ru|net|org|io|me)\b")
FLAG_WORDS = frozenset({
    "foil", "фойл", "etched", "borderless", "extended", "art", "surge", "promo", "showcase", "retro",
    "nm", "sp", "ex", "gd", "lp", "mp", "hp", "pl", "vg", "mint", "near", "played",
    "en", "eng", "ru", "rus", "рус", "russian", "jp", "jpn", "fra", "fr", "it", "ita", "de", "ger",
    "es", "spa", "pt", "kor", "kr", "zhs", "zht",
})


@dataclass(frozen=True)
class SetIndex:
    names: frozenset[str] = frozenset()
    codes: frozenset[str] = frozenset()

    def knows(self, text: str) -> bool:
        norm = normalize(text)
        return bool(norm) and (norm in self.names or norm in self.codes)


def is_junk(name: str) -> bool:
    text = (name or "").strip()
    norm = normalize(text)
    if len(norm) < 3 or not any(character.isalpha() for character in norm):
        return True
    if URL_LIKE.search(text.lower()):
        return True
    if ":" in text:
        return True
    return "/" in text and "//" not in text


def is_metadata_group(content: str, sets: SetIndex) -> bool:
    """Скобка с номером, кодом/именем издания или флагами — метаданные лота, а не карта в составе."""
    if DIGITS.search(content):
        return True
    parts = [part for part in PAREN_PARTS.split(content) if part.strip()]
    if not parts:
        return True
    for part in parts:
        norm = normalize(part)
        if norm in sets.names or norm in sets.codes:
            continue
        if norm.split() and all(token in FLAG_WORDS for token in norm.split()):
            continue
        return False
    return True


def metadata_names(title: str, sets: SetIndex) -> frozenset[str]:
    names: set[str] = set()
    for group in PARENS.findall(title or ""):
        if not is_metadata_group(group, sets):
            continue
        for part in PAREN_PARTS.split(group):
            norm = normalize(part)
            if norm:
                names.add(norm)
    return frozenset(names)


def clean_composition(names: Iterable[str], title: str, sets: SetIndex,
                      card_names: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Убирает мусор и названия изданий — в описании лота ссылкой бывает имя сета, а не карта."""
    metadata = metadata_names(title, sets)
    kept: list[str] = []
    seen: set[str] = set()
    for name in names:
        norm = normalize(name)
        if not norm or norm in seen or is_junk(name):
            continue
        if norm in metadata and sets.knows(name):
            continue
        if card_names and sets.knows(name) and norm not in card_names:
            continue
        seen.add(norm)
        kept.append(name)
    return tuple(kept)
