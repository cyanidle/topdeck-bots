from __future__ import annotations

from dataclasses import dataclass

from .matching import normalize


@dataclass(frozen=True, slots=True)
class Lot:
    id: int
    title: str
    end_ts: int
    start_ts: int
    start_bid: int
    current_bid: int
    bid_count: int
    bin_value: int
    seller_id: int | None
    seller_name: str | None
    seller_city: str | None
    seller_refs: int | None
    shipping_quick: str | None
    image_url: str | None
    url: str

    @property
    def price(self) -> int:
        return max(self.current_bid, self.start_bid)


@dataclass(frozen=True, slots=True)
class Offer:
    eng_name: str
    rus_name: str | None
    name: str
    source: str
    seller_name: str | None
    seller_id: int | None
    city: str | None
    cost: int
    qty: int
    url: str | None
    line: str
    stamp: str | None


@dataclass(frozen=True, slots=True)
class FinishedLot:
    title: str
    final_price: int
    seller: str
    winner: str
    finished_on: str
    lot_id: int | None


@dataclass(frozen=True, slots=True)
class LotDetail:
    lot_id: int
    composition: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Card:
    name_en: str
    name_ru: str | None = None
    oracle_id: str | None = None
    scryfall_id: str | None = None
    set_code: str | None = None
    collector_no: str | None = None
    image_url: str | None = None
    artist: str | None = None
    id: int | None = None

    @property
    def name_norm(self) -> str:
        return normalize(self.name_en)


@dataclass(slots=True)
class PollStats:
    lots: int = 0
    matched_lots: int = 0
    watched_lots: int = 0
    vanished: int = 0
    compositions: int = 0
    extended: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str | None
    windows: tuple[int, ...]
    muted_until: int | None


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    id: int
    card_id: int
    name_en: str
    name_ru: str | None
    image_url: str | None


@dataclass(frozen=True, slots=True)
class WatchlistCard:
    card_id: int
    name_en: str
    name_ru: str | None
    lots_count: int
    lots_min_price: int | None
    next_end_ts: int | None
    next_lot_id: int | None


@dataclass(frozen=True, slots=True)
class Counts:
    users: int
    cards: int
    lots_active: int
    notifications_sent: int
    card_names: int


@dataclass(frozen=True, slots=True)
class PollRun:
    id: int
    kind: str
    started_at: int
    duration_ms: int | None
    ok: bool
    items: int | None
    error: str | None
