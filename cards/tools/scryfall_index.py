#!/usr/bin/env python3
"""Builds and maintains the Scryfall image-hash index in SQLite.

The engine identifies a card by its artwork hash, so it needs a table of
`set`/`collector`/`name`/`pHash`/`artHash` rows and compares a crop against all of them.
This tool owns that data: it downloads new prints (through `scryfall_fetch.py`) and then
lets the engine build the flat index itself (`cardscan --build-index`, so the numbers are
bit-identical to the lookup path). The build is a merge: the previous index records are
reused, only new images are hashed, and the TSV with its `.orb` sidecar is published as an
atomic pair. Once the pair is published the engine never reads the jpgs again, so they are
pruned right after a successful build (pass `--keep-images` to keep them).

    # one pass: import the cache, reconcile set sizes, build the index if needed
    python3 cards/tools/scryfall_index.py --sets fin,war,akh

    # a long-lived indexer that keeps the index fresh while the bot reads it
    python3 cards/tools/scryfall_index.py --watch --reconcile-interval 3600

Concurrency contract with the bot
---------------------------------
* The engine publishes the TSV and `.orb` sidecar as a pair (the sidecar is renamed
  first, the TSV second): a reader that lands between the two renames sees a checksum
  mismatch and falls back to the jpg cache for that moment, never a wrong record.
* The build only runs when new prints appeared, so the index files stay untouched
  between downloads and a reader's open handles keep their meaning.
* Readers that query the SQLite file directly work under WAL: writes are short batched
  transactions (`BEGIN IMMEDIATE`, `busy_timeout`), so a reader is never blocked for long
  and never sees a half-applied batch.
* Only this tool writes to the database; several indexers started by mistake serialise on
  the SQLite write lock instead of corrupting each other's rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scryfall_fetch  # noqa: E402  (reuses the curl transport and the image layout)

SCHEMA_VERSION = 1
DEFAULT_DB = "cards/data/scryfall/index.db"
DEFAULT_META = "cards/data/scryfall/meta.jsonl"
DEFAULT_OUT = "cards/data/scryfall/phash.tsv"
DEFAULT_BIN = "cards/build/cardscan"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prints (
  key       TEXT PRIMARY KEY,
  card_id   TEXT NOT NULL,
  set_code  TEXT NOT NULL,
  collector TEXT NOT NULL,
  name      TEXT NOT NULL,
  size      TEXT NOT NULL,
  path      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prints_set ON prints(set_code);
CREATE TABLE IF NOT EXISTS sets (
  code       TEXT PRIMARY KEY,
  expected   INTEGER,
  checked_at REAL,
  fetched_at REAL,
  size       TEXT
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def log(message: str) -> None:
    print(message, flush=True)


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    else:
        conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sets)")}
        if "size" not in columns:
            conn.execute("ALTER TABLE sets ADD COLUMN size TEXT")
        conn.execute(
            "INSERT OR IGNORE INTO state(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
    return conn


def state_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def state_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def import_meta(conn: sqlite3.Connection, meta: Path, batch: int = 2000) -> int:
    """Upserts every record of the fetcher's metadata log; appends are re-read each pass."""
    if not meta.is_file():
        return 0
    known = {row[0] for row in conn.execute("SELECT key FROM prints")}
    rows: list[tuple] = []
    added = 0
    with meta.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # the fetcher appends, so the last line may be incomplete
            card_id = record.get("id", "")
            path = record.get("path", "")
            if not card_id or not path:
                continue
            size = record.get("size", "small")
            key = f"{card_id}:{size}"
            rows.append((key, card_id, str(record.get("set", "")).upper(),
                         str(record.get("collector", "")), record.get("name", ""),
                         size, path))
            if key not in known:
                known.add(key)
                added += 1
            if len(rows) >= batch:
                upsert_prints(conn, rows)
                rows.clear()
    if rows:
        upsert_prints(conn, rows)
    if added:
        state_set(conn, "export_needed", "1")
    return added


UPSERT = """
INSERT INTO prints(key, card_id, set_code, collector, name, size, path)
VALUES(?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(key) DO UPDATE SET
  card_id   = excluded.card_id,
  set_code  = excluded.set_code,
  collector = excluded.collector,
  name      = excluded.name,
  size      = excluded.size,
  path      = excluded.path
"""


def upsert_prints(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.executemany(UPSERT, rows)
    conn.commit()


def fetch_sets(conn: sqlite3.Connection, codes: list[str], cache: Path, size: str,
               delay: float, limit: int = 0, proxy: str = "", force: bool = False,
               workers: int = 4) -> int:
    """Downloads prints that the cache is missing; resumable, same as the fetcher alone."""
    if not codes:
        return 0
    cache.mkdir(parents=True, exist_ok=True)
    meta_path = cache / "meta.jsonl"
    known = scryfall_fetch.load_known(meta_path)
    written = 0
    with meta_path.open("a", encoding="utf-8") as meta:
        for code in codes[:limit or len(codes)]:
            log(f"set {code.lower()} ...")
            written_count, failed, seen = scryfall_fetch.fetch_set(
                code.lower(), cache, delay, meta, known, size, proxy, force, workers)
            written += written_count
            log(f"set {code.lower()}: {written_count} images, {failed} failed, {seen} cards")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO sets(code) VALUES(?)", (code.upper(),))
            conn.execute("UPDATE sets SET fetched_at = ? WHERE code = ?",
                         (time.time(), code.upper()))
            if failed == 0 and seen > 0:
                conn.execute("UPDATE sets SET size = ? WHERE code = ?", (size, code.upper()))
            conn.commit()
    return written


def set_count(code: str, proxy: str = "") -> int | None:
    payload = scryfall_fetch.curl_json(
        f"{scryfall_fetch.API}/cards/search?q=e%3A{code.lower()}&unique=prints", proxy=proxy)
    total = payload.get("total_cards")
    return int(total) if isinstance(total, int) else None


def reconcile(conn: sqlite3.Connection, codes: list[str] | None = None,
              proxy: str = "") -> list[str]:
    """Compares the indexed print count per set with Scryfall and reports incomplete sets."""
    if codes:
        for code in codes:
            conn.execute("INSERT OR IGNORE INTO sets(code) VALUES(?)", (code.upper(),))
        conn.commit()
    rows = conn.execute("SELECT code, expected FROM sets ORDER BY code").fetchall()
    incomplete: list[str] = []
    for code, previous in rows:
        present = conn.execute("SELECT COUNT(DISTINCT card_id) FROM prints WHERE set_code = ?",
                               (code,)).fetchone()[0]
        expected = set_count(code, proxy)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE sets SET expected = ?, checked_at = ? WHERE code = ?",
                     (expected, time.time(), code))
        conn.commit()
        if expected is None:
            log(f"{code}: card count unavailable, keeping {present} prints")
            continue
        log(f"{code}: {present}/{expected} prints"
            + ("" if previous in (None, expected) else f" (was {previous})"))
        if present < expected:
            incomplete.append(code)
    return incomplete


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def publish(args, conn: sqlite3.Connection) -> bool:
    """Runs the engine's merge build when the meta log grew; prunes the jpg cache after."""
    out = Path(args.out)
    sidecar = out.with_name(out.name + ".orb")
    if state_get(conn, "export_needed", "1") == "0" and out.is_file() and sidecar.is_file():
        return False
    # The jpg paths inside the meta log are repo-relative, so the engine must run from
    # the repo root regardless of where this tool was started.
    result = subprocess.run(
        [str(Path(args.cardscan)), "--build-index", str(Path(args.meta)), str(out)],
        cwd=str(repo_root()), capture_output=True, text=True)
    if result.returncode != 0:
        log(f"cardscan --build-index failed: {result.stderr.strip()}")
        return False
    for line in result.stderr.strip().splitlines():
        if line.startswith("index"):
            log(line)
    state_set(conn, "export_needed", "0")
    state_set(conn, "exported_at", str(int(time.time())))
    conn.commit()
    if not args.keep_images:
        prune_images(out)
    return True


def prune_images(out: Path) -> None:
    """Drops the jpgs the published TSV references: their records are in the .orb already.

    Only indexed images go: a jpg downloaded after this build read its meta must survive
    for the next build, or its print would silently leave the index (the fetcher skips
    keys already present in the meta log and never re-downloads them)."""
    indexed: set[str] = set()
    if out.is_file():
        with out.open(encoding="utf-8") as handle:
            for line in handle:
                fields = line.rstrip("\n").split("\t")
                if len(fields) >= 6 and fields[5]:
                    indexed.add(fields[5])
    images = repo_root() / out.parent / "images"
    if not images.is_dir():
        return
    removed = 0
    for jpg in images.glob("*.jpg"):
        try:
            relative = jpg.relative_to(repo_root()).as_posix()
        except ValueError:
            relative = jpg.as_posix()
        if relative not in indexed:
            continue
        try:
            jpg.unlink()
            removed += 1
        except OSError as error:
            log(f"cannot remove {jpg}: {error}")
    if removed:
        log(f"pruned {removed} cached images")


def stats(conn: sqlite3.Connection, out: Path) -> None:
    total = conn.execute("SELECT COUNT(DISTINCT path) FROM prints").fetchone()[0]
    sets = conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
    cards = 0
    if out.is_file():
        with out.open(encoding="utf-8") as handle:
            cards = sum(1 for _ in handle)
    sidecar = out.with_name(out.name + ".orb")
    orb = sidecar.stat().st_size if sidecar.is_file() else 0
    log(f"prints={total} sets={sets} index={out} ({cards} cards, "
        f".orb {orb} bytes, updated {state_get(conn, 'exported_at', 'never')})")


def run_once(args, conn: sqlite3.Connection, full_pass: bool = True) -> None:
    """One pass over the pipeline. `full_pass` guards the network reconcile."""
    out = Path(args.out)
    if args.force:
        # A re-download with the same --size changes no meta keys, so the build would
        # otherwise never notice the newer jpgs.
        state_set(conn, "export_needed", "1")
        conn.commit()

    def absorb() -> None:
        added = import_meta(conn, Path(args.meta))
        if added:
            log(f"imported {added} new prints")
        conn.execute("INSERT OR IGNORE INTO sets(code) SELECT DISTINCT set_code FROM prints")
        conn.commit()

    # Build and publish first: a long download must not hold back the index the bot reads.
    absorb()
    publish(args, conn)

    # One `--fetch-limit` set per pass: the index must stay publishable while it downloads.
    if not args.no_fetch:
        missing = []
        if args.sets:
            for code in args.sets:
                present = conn.execute(
                    "SELECT COUNT(DISTINCT card_id) FROM prints WHERE set_code = ?",
                    (code.upper(),)).fetchone()[0]
                fetched = conn.execute("SELECT size FROM sets WHERE code = ?",
                                       (code.upper(),)).fetchone()
                if not present or not fetched or fetched[0] != args.size:
                    missing.append(code)
        if missing:
            log(f"fetching sets: {','.join(missing[:args.fetch_limit or len(missing)])}")
            fetch_sets(conn, missing, out.parent, args.size, args.delay, args.fetch_limit,
                       args.proxy, args.force, args.workers)
            absorb()
            publish(args, conn)

        if full_pass and (args.reconcile or args.sets):
            incomplete = reconcile(conn, None, args.proxy)
            if incomplete:
                log("refreshing incomplete sets: "
                    + ",".join(incomplete[:args.fetch_limit or len(incomplete)]))
                fetch_sets(conn, incomplete, out.parent, args.size, args.delay,
                           args.fetch_limit, args.proxy, args.force, args.workers)
                absorb()
                publish(args, conn)

    publish(args, conn)
    stats(conn, out)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite index database")
    parser.add_argument("--meta", default=DEFAULT_META, help="fetcher metadata log (JSONL)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="engine index TSV")
    parser.add_argument("--cardscan", default=DEFAULT_BIN, help="cardscan binary that builds the index")
    parser.add_argument("--sets", default="", help="comma separated set codes to complete")
    parser.add_argument("--size", default="small", choices=["small", "normal", "large"])
    parser.add_argument("--delay", type=float, default=0.08, help="seconds between downloads")
    parser.add_argument("--proxy", default="",
                        help="curl proxy URL for downloads, e.g. socks5h://127.0.0.1:1080")
    parser.add_argument("--force", action="store_true",
                        help="re-download cached images (e.g. after switching --size)")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel image downloads (the tunnel is slow per stream)")
    parser.add_argument("--fetch-limit", type=int, default=0,
                        help="download at most N sets per pass (0 = all)")
    parser.add_argument("--keep-images", action="store_true",
                        help="keep the downloaded jpgs after the index build (debugging)")
    parser.add_argument("--reconcile", action="store_true",
                        help="compare every known set with the Scryfall print count")
    parser.add_argument("--reconcile-interval", type=float, default=3600.0,
                        help="seconds between card-count checks in --watch mode")
    parser.add_argument("--export-interval", type=float, default=60.0,
                        help="seconds between passes in --watch mode")
    parser.add_argument("--watch", action="store_true", help="keep refreshing the index")
    parser.add_argument("--no-fetch", action="store_true", help="never touch the network")
    parser.add_argument("--stats", action="store_true", help="print the database statistics only")
    args = parser.parse_args(argv)
    args.sets = [code.strip().upper() for code in args.sets.split(",") if code.strip()]
    # The engine build runs from the repo root (jpg paths inside the meta log are
    # repo-relative); anchor the default paths the same way so the tool works from any cwd.
    root = repo_root()
    for name in ("db", "meta", "out", "cardscan"):
        if not Path(getattr(args, name)).is_absolute():
            setattr(args, name, str(root / getattr(args, name)))
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    binary = Path(args.cardscan)
    if not binary.is_file() and not args.stats:
        raise SystemExit(f"cardscan binary not found: {binary} (build it first)")
    conn = connect(Path(args.db))
    if args.stats:
        stats(conn, Path(args.out))
        return 0
    if not args.watch:
        run_once(args, conn)
        return 0
    log("watching: fetch -> build (Ctrl-C to stop)")
    next_reconcile = 0.0
    while True:
        try:
            full_pass = time.time() >= next_reconcile
            if full_pass:
                next_reconcile = time.time() + args.reconcile_interval
            run_once(args, conn, full_pass)
            time.sleep(args.export_interval)
        except KeyboardInterrupt:
            log("stopped")
            return 0
        except SystemExit:
            raise
        except Exception as error:  # keep the indexer alive: the bot depends on it running
            log(f"pass failed: {error}")
            time.sleep(min(args.export_interval, 30.0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
