from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .models import Card

MULTI_LOT_MARKERS = ("список внутри", "лот ", "сборник", "набор карт", "коллекция")
QTY_PREFIX = re.compile(r"^\s*\d+\s*[xхXХ]?\s+")
PARENS = re.compile(r"\([^)]*\)")
SEPARATORS = re.compile(r"\s+[-—–]\s+|\s*[•·|]\s*")
NON_WORD = re.compile(r"[^0-9a-zа-я ]+")
SPACES = re.compile(r"\s+")
PREFIX_MIN_LEN = 6
PREFIX_CONFIDENCE = 0.95
FUZZY_MIN_LEN = 6
MATCH_VERSION = 2
FLAG_WORDS = {
    "foil", "фойл", "etched", "borderless", "extended", "art", "surge", "promo", "showcase", "retro",
    "ру", "ru", "en", "eng", "jp", "fra", "it", "sp", "nm", "hp", "mp", "pl", "gd", "lp", "mint",
    "near", "played", "ex", "vg",
}


def normalize(text: str) -> str:
    text = (text or "").replace("№", " ").replace("#", " ")
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    text = NON_WORD.sub(" ", text)
    return SPACES.sub(" ", text).strip()


def strip_flags(normalized: str) -> str:
    tokens = normalized.split()
    while tokens and tokens[0] in FLAG_WORDS:
        tokens.pop(0)
    while tokens and tokens[-1] in FLAG_WORDS:
        tokens.pop()
    return " ".join(tokens)


def is_multi_lot(title: str) -> bool:
    lowered = (title or "").lower()
    return any(marker in lowered for marker in MULTI_LOT_MARKERS)


def title_candidates(title: str) -> list[str]:
    text = PARENS.sub(" ", title or "")
    text = QTY_PREFIX.sub("", text.strip())
    parts = SEPARATORS.split(text) if SEPARATORS.search(text) else [text]
    candidates: list[str] = []
    for part in parts:
        for piece in (part, *re.split(r"\s{2,}", part)):
            cleaned = normalize(piece)
            for variant in (strip_flags(cleaned), cleaned):
                if len(variant) >= 3 and variant not in candidates:
                    candidates.append(variant)
    whole = normalize(text)
    for variant in (strip_flags(whole), whole):
        if variant and variant not in candidates:
            candidates.insert(0, variant)
    return candidates


def is_name_prefix(candidate: str, card_name: str) -> bool:
    if not candidate.startswith(card_name):
        return False
    return len(candidate) == len(card_name) or candidate[len(card_name)] == " "


def is_metadata_tail(tail: str) -> bool:
    tail = tail.strip()
    if not tail:
        return True
    if any(character.isdigit() for character in tail):
        return True
    return tail.split()[0] in FLAG_WORDS


def match_candidates(candidates: Iterable[str], cards: Iterable["Card"], threshold: float = 0.92,
                     method_prefix: str = "") -> list[tuple[int, float, str]]:
    candidates = [candidate for candidate in candidates if candidate]
    if not candidates:
        return []
    names = [
        (card.id, language, normalize(name))
        for card in cards
        for language, name in (("en", card.name_en), ("ru", card.name_ru))
        if name
    ]
    matches: dict[int, tuple[int, float, str]] = {}

    def remember(card_id: int, confidence: float, method: str) -> None:
        current = matches.get(card_id)
        if current is None or confidence > current[1]:
            matches[card_id] = (card_id, confidence, method)

    exact: dict[str, int] = {}
    for card_id, language, card_name in names:
        if card_name in candidates:
            exact[card_name] = card_id
            remember(card_id, 1.0, method_prefix + "exact_" + language)

    prefix_owners: dict[str, tuple[int, str, int]] = {}
    for card_id, language, card_name in names:
        if len(card_name) < PREFIX_MIN_LEN:
            continue
        for candidate in candidates:
            owner = exact.get(candidate)
            if owner is not None and owner != card_id:
                continue
            if not is_name_prefix(candidate, card_name):
                continue
            if not is_metadata_tail(candidate[len(card_name):]):
                continue
            best = prefix_owners.get(candidate)
            if best is None or len(card_name) > best[2]:
                prefix_owners[candidate] = (card_id, language, len(card_name))
    for card_id, language, _ in prefix_owners.values():
        remember(card_id, PREFIX_CONFIDENCE, method_prefix + "prefix_" + language)

    for card_id, _, card_name in names:
        if len(card_name) < FUZZY_MIN_LEN:
            continue
        for candidate in candidates:
            if SequenceMatcher(None, candidate, card_name).ratio() >= threshold:
                remember(card_id, threshold, method_prefix + "fuzzy")
                break
    return sorted(matches.values(), key=lambda item: (-item[1], item[0]))


def match_title(title: str, cards: Iterable["Card"], threshold: float = 0.92) -> list[tuple[int, float, str]]:
    if not title or is_multi_lot(title):
        return []
    return match_candidates(title_candidates(title), cards, threshold)


def match_names(names: Iterable[str], cards: Iterable["Card"], threshold: float = 0.92) -> list[tuple[int, float, str]]:
    candidates: list[str] = []
    for name in names:
        cleaned = normalize(name)
        for variant in (strip_flags(cleaned), cleaned):
            if len(variant) >= 3 and variant not in candidates:
                candidates.append(variant)
    return match_candidates(candidates, cards, threshold, method_prefix="composition:")
