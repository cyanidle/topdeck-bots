from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..models import Card

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 0.1
MAX_RETRIES = 2


class ScryfallClient:
    def __init__(self, session, base: str = "https://api.scryfall.com", user_agent: str = "topbot/0.1",
                 proxy: str | None = "http://127.0.0.1:1081", min_interval_s: float = MIN_INTERVAL_S):
        self.session = session
        self.base = base.rstrip("/")
        self.user_agent = user_agent
        self.proxy = proxy
        self.min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    @property
    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "application/json;q=0.9,*/*;q=0.8"}

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                       json_body: dict | None = None) -> dict | None:
        url = f"{self.base}{path}"
        started = time.perf_counter()
        for attempt in range(MAX_RETRIES + 1):
            await self._throttle()
            async with self.session.request(method, url, params=params, json=json_body, headers=self.headers,
                                            proxy=self.proxy or None) as response:
                if response.status == 404:
                    return None
                if response.status == 429:
                    retry_after = float(response.headers.get("Retry-After", "1") or 1)
                    log.warning("scryfall rate limited", extra={"path": path, "retry_after": retry_after,
                                                                "attempt": attempt + 1})
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(min(retry_after, 30))
                        continue
                    response.raise_for_status()
                if response.status >= 500 and attempt < MAX_RETRIES:
                    await asyncio.sleep(1 + attempt)
                    continue
                response.raise_for_status()
                payload = await response.json()
                log.info("scryfall request", extra={"path": path, "status": response.status,
                                                    "ms": int((time.perf_counter() - started) * 1000),
                                                    "attempt": attempt + 1})
                return payload
        return None

    async def named_fuzzy(self, query: str) -> Card | None:
        payload = await self._request("GET", "/cards/named", {"fuzzy": query})
        return self._to_card(payload) if payload else None

    async def named_exact(self, query: str) -> Card | None:
        payload = await self._request("GET", "/cards/named", {"exact": query})
        return self._to_card(payload) if payload else None

    async def autocomplete(self, query: str) -> list[str]:
        payload = await self._request("GET", "/cards/autocomplete", {"q": query, "include_extras": "false"})
        return list(payload.get("data", [])) if payload else []

    async def card_names(self) -> list[str]:
        payload = await self._request("GET", "/catalog/card-names")
        return list(payload.get("data", [])) if payload else []

    async def card_sets(self) -> list[tuple[str, str]]:
        payload = await self._request("GET", "/sets")
        if not payload:
            return []
        return [(item["code"], item["name"]) for item in payload.get("data", [])
                if item.get("code") and item.get("name")]

    async def search(self, query: str, limit: int = 5) -> list[Card]:
        payload = await self._request("GET", "/cards/search", {"q": query, "unique": "cards"})
        if not payload:
            return []
        return [self._to_card(item) for item in payload.get("data", [])[:limit]]

    async def search_multilingual(self, query: str, limit: int = 5) -> list[Card]:
        payload = await self._request(
            "GET", "/cards/search",
            {"q": query, "unique": "cards", "include_multilingual": "true", "order": "name"},
        )
        if not payload:
            return []
        return [self._to_card(item) for item in payload.get("data", [])[:limit]]

    async def localized_name(self, name_en: str) -> str | None:
        cards = await self.search_multilingual(f'!"{name_en}" lang:ru', limit=5)
        return next((card.name_ru for card in cards if card.name_ru), None)

    async def collection(self, identifiers: list[dict]) -> list[Card]:
        if not identifiers:
            return []
        if len(identifiers) > 75:
            raise ValueError("Scryfall /cards/collection accepts at most 75 identifiers per request")
        payload = await self._request("POST", "/cards/collection", json_body={"identifiers": identifiers})
        if not payload:
            return []
        return [self._to_card(item) for item in payload.get("data", [])]

    @staticmethod
    def _to_card(payload: dict) -> Card:
        image = payload.get("image_uris") or {}
        return Card(
            name_en=payload.get("name", ""),
            name_ru=payload.get("printed_name"),
            oracle_id=payload.get("oracle_id"),
            scryfall_id=payload.get("id"),
            set_code=payload.get("set"),
            collector_no=payload.get("collector_number"),
            image_url=image.get("small") or image.get("normal"),
            artist=payload.get("artist"),
        )
