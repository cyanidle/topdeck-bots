#!/usr/bin/env python3
"""Hot backup of the topbot SQLite database with a copy rotation.

Usage:
  python tools/backup_db.py --db data/topbot.sqlite3 --out /data/backups/topdeck-bots --keep 5
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

NAME_FORMAT = "%Y%m%d-%H%M%S"


def backup_name(db_path: Path, stamp: float) -> str:
    return f"{db_path.stem}-{time.strftime(NAME_FORMAT, time.localtime(stamp))}.sqlite3"


def copy_database(db_path: Path, target: Path) -> None:
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    source = sqlite3.connect(db_path, timeout=30)
    try:
        copy = sqlite3.connect(partial)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    check = sqlite3.connect(partial)
    try:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if verdict != "ok":
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"integrity_check failed on the copy: {verdict}")
    partial.replace(target)


def prune(out_dir: Path, keep: int, prefix: str) -> list[Path]:
    if keep <= 0:
        return []
    backups = sorted(out_dir.glob(f"{prefix}-*.sqlite3"), key=lambda path: path.name, reverse=True)
    removed = []
    for path in backups[keep:]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def run_backup(db_path: Path, out_dir: Path, keep: int, stamp: float | None = None) -> tuple[Path, list[Path]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / backup_name(db_path, time.time() if stamp is None else stamp)
    copy_database(db_path, target)
    os.chmod(target, 0o600)
    return target, prune(out_dir, keep, db_path.stem)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hot backup of the topbot SQLite database")
    parser.add_argument("--db", default="data/topbot.sqlite3", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--keep", default=5, type=int, help="number of backups to keep, newest first")
    args = parser.parse_args(argv)
    try:
        target, removed = run_backup(args.db, args.out, args.keep)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup written: {target} ({target.stat().st_size} bytes), removed: {len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
