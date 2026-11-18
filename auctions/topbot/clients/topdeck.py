from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from typing import Any, Sequence

from ..composition import METADATA_PARENS, is_junk
from ..models import FinishedLot, Lot, LotDetail, Offer

log = logging.getLogger(__name__)

BASE = "https://topdeck.ru"
RETRIES = 2
RECORD_CAP = 1478
EMBEDDED_JSON = re.compile(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)')
ERROR_PAGE = re.compile(r"<title>\s*Ошибка\s*[—–-]\s*TOPTrade\s*</title>", re.I)
TAG = re.compile(r"<[^>]+>")
DESCRIPTION_BLOCK = re.compile(r'<div[^>]*class="[^"]*tt-item-desc[^"]*"[^>]*>(.*?)</div>', re.S)
PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
CARD_LINK = re.compile(r'<a[^>]*topdeck_tooltipCard[^>]*>(.*?)</a>', re.S)
SPACES = re.compile(r"\s+")
LINK_MARK = "\x01"


class TopdeckErrorPage(RuntimeError):
    """Сайт ответил 200 собственной страницей ошибки вместо данных."""


def embedded_records(html: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for match in EMBEDDED_JSON.finditer(html):
        try:
            payload = json.loads(json.loads('"' + match.group(1) + '"'))
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            records = payload
    return records


def strip_tags(text: str) -> str:
    return TAG.sub("", text or "").strip()


def lot_url(lot_id: int | str) -> str:
    return f"{BASE}/apps/toptrade/auctions/{lot_id}"


def lot_image(image_path: str | None, image: str | None) -> str | None:
    if not image_path or not image:
        return None
    return f"{BASE}/apps/toptrade/uploads/{image_path}/{image}"


def parse_auctions(html: str) -> list[Lot]:
    lots: list[Lot] = []
    for raw in embedded_records(html):
        seller = raw.get("seller") or {}
        lots.append(Lot(
            id=int(raw["id"]),
            title=raw.get("lot", ""),
            end_ts=int(raw.get("date_estimated") or 0),
            start_ts=int(raw.get("date_published") or 0),
            start_bid=int(raw.get("start_bid") or 0),
            current_bid=int(raw.get("current_bid") or 0),
            bid_count=int(raw.get("bid_amount") or 0),
            bin_value=int(raw.get("bin_value") or 0),
            seller_id=int(seller["id"]) if seller.get("id") else None,
            seller_name=seller.get("name"),
            seller_city=seller.get("city"),
            seller_refs=int(seller["refs"]) if seller.get("refs") else None,
            shipping_quick=raw.get("shipping_info_quick"),
            image_url=lot_image(raw.get("image_path"), raw.get("image")),
            url=lot_url(raw["id"]),
        ))
    return lots


def parse_offers(html: str) -> list[Offer]:
    offers: list[Offer] = []
    for raw in embedded_records(html):
        seller = raw.get("seller")
        seller_is_object = isinstance(seller, dict)
        offers.append(Offer(
            eng_name=(raw.get("eng_name") or "").strip(),
            rus_name=(raw.get("rus_name") or "").strip() or None,
            name=(raw.get("name") or "").strip(),
            source=raw.get("source") or "",
            seller_name=seller.get("name") if seller_is_object else (seller or None),
            seller_id=int(seller["id"]) if seller_is_object and seller.get("id") else None,
            city=seller.get("city") if seller_is_object else raw.get("city"),
            cost=int(raw.get("cost") or 0),
            qty=int(raw.get("qty") or 0),
            url=raw.get("url"),
            line=strip_tags(raw.get("line") or ""),
            stamp=raw.get("stamp"),
        ))
    return offers


def _clean_name(text: str) -> str:
    return SPACES.sub(" ", strip_tags(text)).strip(" .,;—–-•")


def _paragraph_names(part: str) -> list[str]:
    marked = CARD_LINK.sub(lambda match: f"{LINK_MARK}{strip_tags(match.group(1))}{LINK_MARK}", part)
    text = _clean_name(METADATA_PARENS.sub(" ", strip_tags(marked)))
    links = [_clean_name(piece) for piece in text.split(LINK_MARK)[1::2]]
    names = [name for name in links if name]
    if names:
        return names
    if not text or text.endswith(":"):
        return []
    for chunk in re.split(r"\s*//\s*", text):
        name = chunk.split("(")[0].strip(" .,;—–-•")
        if len(name) >= 3:
            names.append(name)
    return names


def parse_lot_composition(html: str) -> tuple[str, ...]:
    block = DESCRIPTION_BLOCK.search(html)
    if block is None:
        return ()
    seen: set[str] = set()
    names: list[str] = []
    for part in PARAGRAPH.findall(block.group(1)):
        for name in _paragraph_names(part):
            if is_junk(name):
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return tuple(names)


def parse_finished_table(html: str) -> list[FinishedLot]:
    rows: list[FinishedLot] = []
    table = re.search(r"<table.*?</table>", html, re.S)
    if not table:
        return rows
    for row_html in re.findall(r"<tr.*?</tr>", table.group(0), re.S)[1:]:
        cells = [strip_tags(cell) for cell in re.findall(r"<td.*?</td>", row_html, re.S)]
        if len(cells) < 5:
            continue
        link = re.search(r'href="(/apps/toptrade/auctions/(\d+))"', row_html)
        rows.append(FinishedLot(
            title=cells[0],
            final_price=int(re.sub(r"\D", "", cells[1]) or 0),
            seller=cells[2],
            winner=cells[3],
            finished_on=cells[4],
            lot_id=int(link.group(2)) if link else None,
        ))
    return rows


class TopdeckClient:
    def __init__(self, session, base: str = BASE, user_agent: str = "topbot/0.1", throttle_s: float = 1.5):
        self.session = session
        self.base = base.rstrip("/")
        self.user_agent = user_agent
        self.throttle_s = throttle_s
        self._last_call = 0.0

    async def _throttle(self) -> None:
        wait = self.throttle_s - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def fetch_text(self, path: str, params: dict[str, str] | None = None,
                         retries: int = RETRIES) -> str:
        url = f"{self.base}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            await self._throttle()
            started = time.perf_counter()
            try:
                async with self.session.get(url, headers={"User-Agent": self.user_agent,
                                                          "Accept-Encoding": "gzip"}) as response:
                    response.raise_for_status()
                    text = await response.text()
                    status = response.status
                    if ERROR_PAGE.search(text):
                        raise TopdeckErrorPage(f"error page {len(text)} chars")
            except Exception as exc:
                last_error = exc
                log.warning("topdeck fetch failed", extra={
                    "path": path, "attempt": attempt + 1,
                    "ms": int((time.perf_counter() - started) * 1000), "error": repr(exc)})
                if attempt < retries:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise
            log.info("topdeck fetch", extra={"path": path, "status": status, "chars": len(text),
                                             "ms": int((time.perf_counter() - started) * 1000),
                                             "attempt": attempt + 1})
            return text
        raise last_error or RuntimeError("unreachable")

    async def fetch_auctions(self) -> list[Lot]:
        lots = parse_auctions(await self.fetch_text("/apps/toptrade/auctions"))
        log.info("topdeck auctions parsed", extra={"lots": len(lots)})
        return lots

    async def fetch_lot(self, lot_id: int) -> LotDetail:
        html = await self.fetch_text(f"/apps/toptrade/auctions/{lot_id}")
        if DESCRIPTION_BLOCK.search(html) is None:
            raise TopdeckErrorPage(f"lot page {lot_id} without description block")
        composition = parse_lot_composition(html)
        log.info("topdeck lot composition", extra={"lot_id": lot_id, "cards": len(composition)})
        return LotDetail(lot_id=lot_id, composition=composition)

    async def search_singles(self, names: Sequence[str]) -> tuple[list[Offer], bool]:
        html = await self.fetch_text("/apps/toptrade/singles/search", {"q": "\n".join(names)})
        offers = parse_offers(html)
        truncated = len(offers) >= RECORD_CAP
        log.info("topdeck singles search", extra={"cards": len(names), "offers": len(offers),
                                                  "truncated": truncated, "sources": len({o.source for o in offers})})
        return offers, truncated

    async def finished_search(self, query: str, page: int = 1) -> list[FinishedLot]:
        path = "/apps/toptrade/auctions/search" if page <= 1 else f"/apps/toptrade/auctions/search/{page}"
        return parse_finished_table(await self.fetch_text(path, {"q": query}))
