from __future__ import annotations

import logging
import time
from pathlib import Path

from cardscan import analyze

from .db import normalize_number
from .models import ScanCard

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
SCAN_RESULT_LIMIT = 60


def load_set_codes(path: str | Path) -> list[str]:
    source = Path(path)
    if not source.is_file():
        return []
    codes = [line.strip().upper() for line in source.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    return sorted(set(codes))


def report_cards(report: dict) -> list[ScanCard]:
    cards: list[ScanCard] = []
    for item in report.get("cards", []):
        cards.append(ScanCard(
            identity=str(item.get("identity") or ""),
            set_code=str(item.get("set") or "").upper(),
            number=normalize_number(str(item.get("collector_number") or "")),
            name=item.get("name"),
            identified_by=item.get("identified_by"),
            confidence=float(item.get("confidence") or 0.0),
        ))
    return cards


def scan_media(path: str | Path, set_codes: list[str], stride: int = 1) -> dict:
    source = Path(path)
    started = time.monotonic()
    log.info("scan started", extra={"file": source.name, "set_codes": len(set_codes),
                                    "stride": stride})
    report = analyze(str(path), set_codes=set_codes, stride=stride)
    result = ",".join(
        f"{str(item.get('identity') or 'unknown')}[{item.get('identified_by') or '-'}]"
        for item in (report.get("cards") or [])[:SCAN_RESULT_LIMIT])
    log.info("scan finished", extra={
        "file": source.name,
        "cards": report.get("count"),
        "identified": report.get("identified"),
        "frames": report.get("frames"),
        "processed_frames": report.get("processed_frames"),
        "ocr_reads": report.get("ocr_reads"),
        "ocr_reads_with_identity": report.get("ocr_reads_with_identity"),
        "engine_s": report.get("elapsed_seconds"),
        "wall_s": round(time.monotonic() - started, 1),
        "result": result,
    })
    return report
