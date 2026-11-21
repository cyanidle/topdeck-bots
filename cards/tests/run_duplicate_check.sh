#!/usr/bin/env bash
# Duplicate-card gate: two identical cards must be counted twice, while one card split into
# two segments by reading noise must stay one card.
#
# Fixtures are synthesized from the user's sample video (skipped when it is absent):
#   dup.mp4   A B A   - the same card again after another card -> 3 cards, DDS:51 twice
#   dup2.mp4  A _ A   - the same card again after a pause      -> 2 cards, DDS:51 twice
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
bin="${CARDSCAN_BIN:-$root/build/cardscan}"
video="${CARDSCAN_SAMPLE_VIDEO:-$HOME/Documents/cards.mp4}"
index="${CARDSCAN_PHASH_INDEX:-$root/data/scryfall/phash.tsv}"

if [[ ! -x "$bin" ]]; then
  echo "cardscan binary not found: $bin" >&2
  exit 1
fi
if [[ ! -f "$video" ]]; then
  echo "skip duplicate check: $video not found"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

ffmpeg -v error -y -i "$video" -filter_complex \
  "[0:v]trim=start_frame=0:end_frame=30,setpts=PTS-STARTPTS[a];\
   [0:v]trim=start_frame=150:end_frame=180,setpts=PTS-STARTPTS[b];\
   [0:v]trim=start_frame=0:end_frame=30,setpts=PTS-STARTPTS[c];\
   [a][b][c]concat=n=3:v=1:a=0[v]" -map "[v]" -an -c:v libx264 -crf 20 "$tmp/dup.mp4"

ffmpeg -v error -y -i "$video" -f lavfi -i "color=c=black:s=720x1280:r=30:d=1.6" \
  -filter_complex \
  "[0:v]trim=start_frame=0:end_frame=30,setpts=PTS-STARTPTS[a];\
   [0:v]trim=start_frame=0:end_frame=30,setpts=PTS-STARTPTS[c];\
   [a][1:v][c]concat=n=3:v=1:a=0[v]" -map "[v]" -an -c:v libx264 -crf 20 "$tmp/dup2.mp4"

index_args=()
[[ -f "$index" ]] && index_args=(--phash-index "$index")

"$bin" "$tmp/dup.mp4" --set-codes "$root/data/set-codes.txt" "${index_args[@]}" \
  --json "$tmp/dup.json" >/dev/null
"$bin" "$tmp/dup2.mp4" --set-codes "$root/data/set-codes.txt" "${index_args[@]}" \
  --json "$tmp/dup2.json" >/dev/null

python3 - "$tmp/dup.json" "$tmp/dup2.json" <<'PY'
import json
import sys


def normalized(identity):
    set_code, _, number = (identity or "").partition(":")
    return f"{set_code}:{number.lstrip('0') or number}"


def identities(path):
    report = json.load(open(path))
    return [normalized(c["identity"]) for c in report["cards"]], report["count"]


a_b_a, count_a_b_a = identities(sys.argv[1])
a_pause_a, count_a_pause_a = identities(sys.argv[2])
print("A B A ->", count_a_b_a, a_b_a)
print("A _ A ->", count_a_pause_a, a_pause_a)

if count_a_b_a != 3:
    raise SystemExit("expected 3 cards for A B A, got %d" % count_a_b_a)
if count_a_pause_a != 2:
    raise SystemExit("expected 2 cards for A _ A, got %d" % count_a_pause_a)
if a_b_a[0] != a_b_a[2]:
    raise SystemExit("A B A identities differ: %s" % a_b_a)
if a_pause_a[0] != a_pause_a[1]:
    raise SystemExit("A _ A identities differ: %s" % a_pause_a)
print("duplicate check passed")
PY
