#!/usr/bin/env python3
"""Reference client for the public (guest) TOPDeck endpoints.

Usage:
  python tools/topdeck_probe.py search "Snapcaster Mage" "Lightning Bolt"
  python tools/topdeck_probe.py auctions --hours 24 --limit 10
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://topdeck.ru"
UA = "topbot-research/0.1 (+https://topdeck.ru)"
EMBEDDED_JSON = re.compile(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)')

RECORD_CAP = 1478


def fetch(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def embedded_records(html: str) -> list[dict]:
    records: list[dict] = []
    for match in EMBEDDED_JSON.finditer(html):
        try:
            payload = json.loads(json.loads('"' + match.group(1) + '"'))
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            records = payload
    return records


def search_singles(cards: list[str], throttle: float = 1.0) -> tuple[list[dict], list[str]]:
    query = "\n".join(cards)
    html = fetch(f"{BASE}/apps/toptrade/singles/search?q={urllib.parse.quote(query)}")
    offers = embedded_records(html)
    found = {str(o.get("name", "")).strip().lower() for o in offers}
    missing = [c for c in cards if c.lower() not in found]
    if len(offers) >= RECORD_CAP:
        missing = cards
    time.sleep(throttle)
    return offers, missing


def fetch_auctions(throttle: float = 1.0) -> list[dict]:
    html = fetch(f"{BASE}/apps/toptrade/auctions")
    lots = embedded_records(html)
    time.sleep(throttle)
    return lots


def lot_url(lot: dict) -> str:
    return f"{BASE}/apps/toptrade/auctions/{lot['id']}"


def lot_thumb(lot: dict) -> str:
    return f"{BASE}/apps/toptrade/uploads/{lot['image_path']}/thumb_{lot['image']}"


def cheapest_by_card(offers: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for offer in offers:
        name = str(offer.get("name", "")).strip()
        cost = int(offer.get("cost") or 0)
        if not name or cost <= 0:
            continue
        if name not in best or cost < int(best[name]["cost"]):
            best[name] = offer
    return best


def cmd_search(args: argparse.Namespace) -> int:
    offers, missing = search_singles(args.cards)
    print(f"offers: {len(offers)}  missing/truncated: {missing}")
    for name, offer in sorted(cheapest_by_card(offers).items(), key=lambda kv: kv[1]["cost"]):
        seller = offer.get("seller")
        who = seller.get("name") if isinstance(seller, dict) else seller
        print(f"{int(offer['cost']):>7} ₽  x{offer.get('qty')}  {name}  [{offer.get('source')}: {who}]  {offer.get('url')}")
    return 0


def cmd_auctions(args: argparse.Namespace) -> int:
    lots = fetch_auctions()
    now = time.time()
    horizon = now + args.hours * 3600
    ending = [lot for lot in lots if now <= int(lot["date_estimated"]) <= horizon]
    ending.sort(key=lambda lot: int(lot["date_estimated"]))
    print(f"active lots: {len(lots)}  ending within {args.hours}h: {len(ending)}")
    for lot in ending[: args.limit]:
        left = (int(lot["date_estimated"]) - now) / 60
        price = max(int(lot["current_bid"]), int(lot["start_bid"]))
        print(f"{left:7.0f} min  {price:>7} ₽  {lot['lot'][:70]}  {lot_url(lot)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="single-card price offers across TOPDeck sellers and partner shops")
    search.add_argument("cards", nargs="+")
    search.set_defaults(func=cmd_search)

    auctions = sub.add_parser("auctions", help="active auctions ending soonest first")
    auctions.add_argument("--hours", type=float, default=24.0)
    auctions.add_argument("--limit", type=int, default=10)
    auctions.set_defaults(func=cmd_auctions)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
