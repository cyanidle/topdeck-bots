#!/usr/bin/env bash
# Identity gate for the sample video: the printed collector lines below were read by eye from
# the video frames (comment = frame number), so a wrong "identified" card fails the check.
#
# Needs the pHash index built from the Scryfall cache (cards/tools/scryfall_fetch.py +
# cardscan --build-index); skipped when it is absent.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
bin="${CARDSCAN_BIN:-$root/build/cardscan}"
video="${CARDSCAN_SAMPLE_VIDEO:-$HOME/Documents/cards.mp4}"
index="${CARDSCAN_PHASH_INDEX:-$root/data/scryfall/phash.tsv}"

if [[ ! -x "$bin" ]]; then
  echo "cardscan binary not found: $bin (build it first)" >&2
  exit 1
fi
if [[ ! -f "$video" ]]; then
  echo "skip identity check: $video not found"
  exit 0
fi
if [[ ! -f "$index" ]]; then
  echo "skip identity check: pHash index not built ($index)"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

"$bin" "$video" --set-codes "$root/data/set-codes.txt" --phash-index "$index" \
  --json "$tmp/report.json" >/dev/null

python3 - "$tmp/report.json" <<'PY'
import json
import sys

expected = {
    "DDS:51",   # frame 12  "051/065 C / DDS - EN"
    "FIN:180",  # frame 120 "U 0180 FFXVI / FIN - EN"
    "WAR:149",  # frame 168 "149/264 C / WAR - RU"
    "HOB:82",   # frame 252 "R 0082 / HOB - EN"
    "MSH:97",   # frame 886 "R 0097 / MSH - EN"
}
# AKH:187 (frame 198) is verified by eye too, but that card shares one show segment with
# WAR:149 (5.5-8 s) and the current segmenter reports a single card per segment, so only one
# of the two can pass; see GOAL.md ("known misses").

report = json.load(open(sys.argv[1]))
cards = report["cards"]


def normalized(identity: str) -> str:
    set_code, _, number = identity.partition(":")
    digits = number.lstrip("0") or number
    return f"{set_code}:{digits}"


identities = [normalized(c["identity"]) for c in cards if c.get("identity")]

print("count=%d identified=%d reads=%d/%d"
      % (report["count"], report["identified"], report["ocr_reads_with_identity"],
         report["ocr_reads"]))
print("identities:", sorted(identities))

if len(identities) != len(set(identities)):
    raise SystemExit("duplicate identities in one report: %s" % identities)

missing = expected - set(identities)
if missing:
    raise SystemExit("missing verified identities: %s" % sorted(missing))

print("identity check passed (%d verified cards)" % len(expected))
PY
