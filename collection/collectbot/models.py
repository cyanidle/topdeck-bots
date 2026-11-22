from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CollectionEntry:
    id: int
    user_id: int
    set_code: str
    number: str
    count: int
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class ScanCard:
    identity: str
    set_code: str
    number: str
    name: str | None
    identified_by: str | None
    confidence: float
