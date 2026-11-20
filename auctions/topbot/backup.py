from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

HOURLY_SUFFIX = ".hourly"
PART_SUFFIX = ".part"


def hourly_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + HOURLY_SUFFIX)


def copy_database(db_path: Path, target: Path) -> None:
    """`target` всегда оказывается либо прежней, либо целой новой копией: недописанный
    или не прошедший `PRAGMA integrity_check` файл не подменяет её и удаляется."""
    partial = target.with_name(target.name + PART_SUFFIX)
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
    partial.chmod(0o600)
    partial.replace(target)


def run_hourly_backup(db_path: Path) -> Path:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    target = hourly_path(db_path)
    copy_database(db_path, target)
    return target


def next_run_at(now: float, interval_s: int) -> int:
    return (int(now) // interval_s + 1) * interval_s


class HourlyBackup:
    """Сразу при старте и далее по границе интервала кладёт свежий снимок БД рядом с основной —
    один файл с постоянным именем, чтобы внешний сервер забирал его по scp."""

    def __init__(self, db_path: Path, interval_s: int, clock=time.time):
        self.db_path = db_path
        self.interval_s = interval_s
        self.clock = clock

    async def run_once(self) -> Path:
        return await asyncio.to_thread(run_hourly_backup, self.db_path)

    def delay(self) -> float:
        now = self.clock()
        return max(1.0, next_run_at(now, self.interval_s) - now)

    async def run(self) -> None:
        log.info("hourly backup started", extra={"db": str(self.db_path), "every_s": self.interval_s,
                                                "target": str(hourly_path(self.db_path))})
        while True:
            started = time.perf_counter()
            try:
                target = await self.run_once()
            except Exception:
                log.exception("hourly backup failed")
            else:
                log.info("hourly backup written", extra={"path": str(target),
                                                         "bytes": target.stat().st_size,
                                                         "ms": int((time.perf_counter() - started) * 1000)})
            await asyncio.sleep(self.delay())
