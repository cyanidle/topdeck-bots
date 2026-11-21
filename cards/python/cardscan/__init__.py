"""ctypes binding for the native `cardscan` detector library.

The C++ core is built as ``libcardscan.so`` (see ``CMakeLists.txt``). This module
loads it with ctypes and exposes a small, typed Python API::

    from cardscan import Scanner

    with Scanner() as scanner:
        report = scanner.analyze("card.jpg")
        print(report["cards"][0]["identity"])

The library is looked up in ``$CARDSCAN_LIBRARY`` first, then next to this file
(``cards/python/cardscan/libcardscan.so``), then in the CMake build tree
(``cards/build/libcardscan.so``).
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = ["CardscanError", "Scanner", "analyze", "library_path"]

_OPTION_INT_KEYS = {
    "min_track_hits",
    "ocr_stride",
    "max_frames",
    "stride",
}


class CardscanError(RuntimeError):
    """Raised when the native detector reports an error."""


def _candidate_paths() -> list[Path]:
    here = Path(__file__).resolve().parent
    cards_root = here.parent.parent
    candidates: list[Path] = []
    env = os.environ.get("CARDSCAN_LIBRARY")
    if env:
        candidates.append(Path(env))
    candidates.extend(
        [
            here / "libcardscan.so",
            cards_root / "build" / "libcardscan.so",
            cards_root / "build" / "libcardscan.dylib",
            cards_root / "build" / "cardscan.dll",
        ]
    )
    return candidates


def library_path() -> Path:
    """Returns the path of the loaded (or loadable) shared library."""
    for candidate in _candidate_paths():
        if candidate.is_file():
            return candidate
    raise CardscanError(
        "libcardscan shared library not found; build it with "
        "`cmake -S cards -B cards/build && cmake --build cards/build` "
        "or point CARDSCAN_LIBRARY at it"
    )


_lib: ctypes.CDLL | None = None


def _library() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    lib = ctypes.CDLL(str(library_path()))
    lib.cardscan_create.restype = ctypes.c_void_p
    lib.cardscan_create.argtypes = []
    lib.cardscan_destroy.restype = None
    lib.cardscan_destroy.argtypes = [ctypes.c_void_p]
    lib.cardscan_version.restype = ctypes.c_char_p
    lib.cardscan_version.argtypes = []
    lib.cardscan_set_option.restype = ctypes.c_int
    lib.cardscan_set_option.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.cardscan_set_set_codes.restype = ctypes.c_int
    lib.cardscan_set_set_codes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int,
    ]
    lib.cardscan_analyze.restype = ctypes.c_void_p
    lib.cardscan_analyze.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.cardscan_last_error.restype = ctypes.c_char_p
    lib.cardscan_last_error.argtypes = [ctypes.c_void_p]
    lib.cardscan_free_string.restype = None
    lib.cardscan_free_string.argtypes = [ctypes.c_void_p]
    _lib = lib
    return lib


class Scanner:
    """Wraps one native analyzer handle (options are per scanner)."""

    def __init__(self, **options: Any) -> None:
        self._lib = _library()
        self._handle = self._lib.cardscan_create()
        if not self._handle:
            raise CardscanError("cardscan_create returned NULL")
        for key, value in options.items():
            self.set_option(key, value)

    def __enter__(self) -> "Scanner":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._handle:
            self._lib.cardscan_destroy(self._handle)
            self._handle = None

    @property
    def version(self) -> str:
        return self._lib.cardscan_version().decode()

    def set_option(self, key: str, value: Any) -> None:
        if isinstance(value, bool):
            text = "1" if value else "0"
        else:
            text = str(value)
        rc = self._lib.cardscan_set_option(
            self._handle, key.encode(), text.encode()
        )
        if rc != 0:
            raise CardscanError(f"unknown option or bad value: {key}={value!r}")

    def set_set_codes(self, codes: Iterable[str]) -> None:
        """Installs a set-code vocabulary used to snap OCR misreads."""
        items = [code.strip().upper().encode() for code in codes if code.strip()]
        array = (ctypes.c_char_p * len(items))(*items) if items else None
        rc = self._lib.cardscan_set_set_codes(
            self._handle, array, len(items) if array is not None else 0
        )
        if rc != 0:
            raise CardscanError("cardscan_set_set_codes failed")

    def analyze(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        """Runs detection/recognition and returns the JSON report as a dict."""
        if not self._handle:
            raise CardscanError("scanner is closed")
        raw = self._lib.cardscan_analyze(self._handle, str(path).encode())
        if not raw:
            message = self._lib.cardscan_last_error(self._handle)
            raise CardscanError(message.decode() if message else "analysis failed")
        try:
            return json.loads(ctypes.cast(raw, ctypes.c_char_p).value or b"{}")
        finally:
            self._lib.cardscan_free_string(raw)

    analyze_image = analyze
    analyze_video = analyze


def analyze(path: str | os.PathLike[str], **options: Any) -> dict[str, Any]:
    """One-shot helper: analyze a single file with a throwaway scanner."""
    codes: Sequence[str] | None = options.pop("set_codes", None)
    with Scanner(**options) as scanner:
        if codes:
            scanner.set_set_codes(codes)
        return scanner.analyze(path)
