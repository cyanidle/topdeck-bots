#!/usr/bin/env python3
"""Pulls card metadata and small card images from Scryfall into a local cache.

The cache is a one-time (refreshable) dataset used to build the image-hash index that
identifies cards by their picture instead of by the tiny collector line.

    python3 cards/tools/scryfall_fetch.py --sets fin,war,akh --out cards/data/scryfall

Downloads go through `curl`: Python's urllib spends ~20 s per request on this host while
curl answers the same request in 0.2 s. Requests are sequential with a small pause, well
below Scryfall's documented 10 requests/second. Already cached cards are skipped, so the
script resumes after an interruption.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.scryfall.com"
USER_AGENT = "topdeck-bots-cardscan/0.1"


def curl_args(proxy: str = "") -> list[str]:
    args = ["curl", "-s", "-m", "25", "--retry", "1", "--retry-delay", "1",
            "-H", f"User-Agent: {USER_AGENT}"]
    if proxy:
        args += ["--proxy", proxy]
    return args


def curl_json(url: str, retries: int = 5, proxy: str = "") -> dict:
    for attempt in range(retries):
        result = subprocess.run(curl_args(proxy) + ["-H", "Accept: application/json", url],
                                capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                payload = {}
            if payload:
                return payload
        time.sleep(1.0 + attempt)
    return {}


def complete_jpeg(path: Path) -> bool:
    """Scryfall responses can stall mid-transfer; a cut JPEG still opens in most decoders."""
    try:
        with path.open("rb") as handle:
            handle.seek(-2, os.SEEK_END)
            return handle.read(2) == b"\xff\xd9"
    except OSError:
        return False


def curl_file(url: str, path: Path, proxy: str = "", retries: int = 3) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        result = subprocess.run(curl_args(proxy) + ["-H", "Accept: image/jpeg", "-o", str(path),
                                                   url])
        if result.returncode == 0 and path.is_file() and path.stat().st_size > 512:
            if complete_jpeg(path):
                return True
        time.sleep(0.5 + attempt)
    return False


def image_url(card: dict, size: str) -> str:
    uris = card.get("image_uris")
    if not uris:
        faces = card.get("card_faces") or []
        if faces:
            uris = faces[0].get("image_uris")
    if not uris:
        return ""
    return uris.get(size) or uris.get("normal") or uris.get("small") or ""


def safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


def load_known(meta_path: Path) -> set[str]:
    known: set[str] = set()
    if not meta_path.is_file():
        return known
    with meta_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            known.add(record.get("id", ""))
    return known


def fetch_set(code: str, out: Path, delay: float, meta, known: set[str], size: str,
              proxy: str = "", force: bool = False,
              workers: int = 4) -> tuple[int, int, int]:
    """Downloads one set; returns (written, failed, seen) so a caller can tell a complete set."""
    url = f"{API}/cards/search?q=e%3A{code}&unique=prints&order=set"
    written = 0
    failed = 0
    seen = 0
    seen_pages: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        while url and url not in seen_pages:
            seen_pages.add(url)
            # The API answers one page at a time: a stalled page silently truncates the set, so
            # a page without a payload is reported as failed instead of ending the set quietly.
            payload = curl_json(url, proxy=proxy)
            if not payload.get("data"):
                print(f"  ! failed page: {url}", file=sys.stderr, flush=True)
                failed += 1
                break
            pending = []
            for card in payload.get("data", []):
                seen += 1
                card_id = card.get("id", "")
                key = f"{card_id}:{size}"
                if card_id and key in known and not force:
                    continue
                image_url_ = image_url(card, size)
                if not image_url_:
                    continue
                collector = str(card.get("collector_number", ""))
                target = out / "images" / f"{card.get('set', code)}_{safe_name(collector)}.jpg"
                pending.append((pool.submit(curl_file, image_url_, target, proxy), card,
                                card_id, key, collector, target))
                time.sleep(delay)
            for future, card, card_id, key, collector, target in pending:
                if not future.result():
                    print(f"  ! failed: {image_url(card, size)}", file=sys.stderr, flush=True)
                    failed += 1
                    continue
                meta.write(json.dumps({
                    "size": size,
                    "set": str(card.get("set", code)).upper(),
                    "collector": collector,
                    "name": card.get("name", ""),
                    "id": card_id,
                    "path": str(target),
                }, ensure_ascii=False) + "\n")
                meta.flush()
                if card_id:
                    known.add(key)
                written += 1
                if written % 50 == 0:
                    print(f"  {code}: {written} images", flush=True)
            url = payload.get("next_page") or ""
    return written, failed, seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sets", required=True, help="comma separated set codes, e.g. fin,war")
    parser.add_argument("--out", default="cards/data/scryfall", help="cache directory")
    parser.add_argument("--delay", type=float, default=0.08, help="seconds between requests")
    parser.add_argument("--size", default="small", choices=["small", "normal", "large"],
                        help="Scryfall image size to cache")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel image downloads (the tunnel is slow per stream)")
    parser.add_argument("--proxy", default="",
                        help="curl proxy URL, e.g. socks5h://127.0.0.1:1080")
    parser.add_argument("--force", action="store_true",
                        help="re-download images that are already cached")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "meta.jsonl"
    known = load_known(meta_path)
    print(f"cache: {len(known)} cards already known", flush=True)

    total = 0
    with meta_path.open("a", encoding="utf-8") as meta:
        for code in [c.strip().lower() for c in args.sets.split(",") if c.strip()]:
            print(f"set {code} ...", flush=True)
            count, failed, seen = fetch_set(code, out, args.delay, meta, known, args.size,
                                            args.proxy, args.force, args.workers)
            print(f"set {code}: {count} new images, {failed} failed, {seen} cards", flush=True)
            total += count
    print(f"done: {total} new images, metadata in {meta_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
