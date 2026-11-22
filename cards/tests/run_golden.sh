#!/usr/bin/env bash
# Golden checks against the user's sample media.
#
#   CARDSCAN_SAMPLE_IMAGE=~/Documents/cards.jpg \
#   CARDSCAN_SAMPLE_VIDEO=~/Documents/cards.mp4 cards/tests/run_golden.sh
#
# Missing files skip their check. The default run stays cheap: the detector is only run on a
# handful of frames per card (run_frame_check.sh) instead of the whole feed. Set
# CARDSCAN_FULL=1 to also run the whole feed through the production path (minutes).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
bin="${CARDSCAN_BIN:-$root/build/cardscan}"
image="${CARDSCAN_SAMPLE_IMAGE:-$HOME/Documents/cards.jpg}"
video="${CARDSCAN_SAMPLE_VIDEO:-$HOME/Documents/cards.mp4}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if [[ ! -x "$bin" ]]; then
  echo "cardscan binary not found: $bin (build it first)" >&2
  exit 1
fi

fail() { echo "FAIL: $*" >&2; exit 1; }

if [[ -f "$image" ]]; then
  "$bin" "$image" --json "$tmp/image.json" --overlay "$tmp/image.png" >/dev/null
  python3 - "$tmp/image.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
assert report["type"] == "image", report
assert report["count"] >= 1, report
cards = report["cards"]
assert any(c["set"] == "DDS" for c in cards), cards
assert any((c["collector"] or "").replace("/", "").startswith("051") for c in cards), cards
print("image ok:", cards[0]["identity"], cards[0]["confidence"])
PY
else
  echo "skip image: $image not found"
fi

if [[ -x "$here/run_frame_check.sh" ]]; then
  CARDSCAN_BIN="$bin" CARDSCAN_SAMPLE_VIDEO="$video" "$here/run_frame_check.sh"
fi

if [[ -x "$here/run_duplicate_check.sh" ]]; then
  CARDSCAN_BIN="$bin" CARDSCAN_SAMPLE_VIDEO="$video" "$here/run_duplicate_check.sh"
fi

if [[ "${CARDSCAN_FULL:-0}" == "1" ]]; then
  if [[ -f "$video" ]]; then
    "$bin" "$video" --json "$tmp/video.json" >/dev/null
    python3 - "$tmp/video.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
assert report["type"] == "video", report
assert report["count"] >= 5, report["count"]
identities = [c["identity"] for c in report["cards"] if c["identity"]]
assert len(identities) == len(set(identities)), identities
assert report["identified"] >= 3, report["identified"]
print("video ok: cards=%d identified=%d reads=%d/%d"
      % (report["count"], report["identified"], report["ocr_reads_with_identity"], report["ocr_reads"]))
PY
  else
    echo "skip video: $video not found"
  fi
  if [[ -x "$here/run_identity_check.sh" ]]; then
    CARDSCAN_BIN="$bin" CARDSCAN_SAMPLE_VIDEO="$video" "$here/run_identity_check.sh"
  fi
else
  echo "skip full-feed checks (set CARDSCAN_FULL=1 to run them)"
fi

echo "golden checks passed"
