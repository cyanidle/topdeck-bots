#!/usr/bin/env bash
# Frame gate: the detector runs only on a handful of frames per card, and recognition runs on
# the extracted normalized rectangles. A couple of seconds instead of a full feed pass, so it
# is cheap enough to run on every change.
#
# The table below was read by eye from the frames (frame = the moment the card is held up):
#
#   12   DDS 051/065 C   "DDS - EN"          DDS:51
#   120  U 0180          "FIN - EN"          FIN:180
#   168  149/264 C       "WAR - RU"          WAR:149
#   252  R 0082          "HOB - EN"          HOB:82
#   600  R 0067          "TMT - EN"          TMT:67   (Ninja Teen)
#   730  158/307 R       "C18 - EN"          C18:158  (Rampaging Baloths)
#   823  M 0251          "MSH - EN"          MSH:251  (The Ten Rings, filmed in a sleeve)
#   886  R 0097          "MSH - EN"          MSH:97   (Elektra)
#
# A card the engine cannot read may stay "unknown"; naming a *different* card is a failure.
# The two cards of frames 730/823 share one show segment (the Ten Rings lies on top of the
# Baloths), which is the case that used to report the neighbour's collector line.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
bin="${CARDSCAN_BIN:-$root/build/cardscan}"
video="${CARDSCAN_SAMPLE_VIDEO:-$HOME/Documents/cards.mp4}"
index="${CARDSCAN_PHASH_INDEX:-$root/data/scryfall/phash.tsv}"
span="${CARDSCAN_FRAME_SPAN:-8}"

if [[ ! -x "$bin" ]]; then
  echo "cardscan binary not found: $bin (build it first)" >&2
  exit 1
fi
if [[ ! -f "$video" ]]; then
  echo "skip frame check: $video not found"
  exit 0
fi
if [[ ! -f "$index" ]]; then
  echo "skip frame check: pHash index not built ($index)"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

frames="12,120,168,252,600,730,823,886"
"$bin" --extract-cards "$video" "$tmp/cards" --frames "$frames" --frame-span "$span" \
  --set-codes "$root/data/set-codes.txt" --phash-index "$index" >"$tmp/extract.txt"

"$bin" --cards "$tmp/cards" --set-codes "$root/data/set-codes.txt" --phash-index "$index" \
  >"$tmp/recognized.txt"

python3 - "$tmp/recognized.txt" "$frames" "$span" <<'PY'
import re
import sys

# identity each frame really shows; anything else that is not "unknown" is a wrong card
truth = {
    12: {"DDS:51"},
    120: {"FIN:180"},
    168: {"WAR:149"},
    252: {"HOB:82"},
    600: {"TMT:67"},
    730: {"C18:158"},
    823: {"MSH:251"},
    886: {"MSH:97"},
}
# the engine has to name these, otherwise recall has silently collapsed
must = {12: "DDS:51", 252: "HOB:82", 886: "MSH:97"}

anchors = [int(f) for f in sys.argv[2].split(",")]
span = int(sys.argv[3])

rows = []
for line in open(sys.argv[1]):
    m = re.match(r"#\s*(\d+)\s+f=(\d+)\.\.(\d+)\s+(\S+)\s+(\S+)\s+by=(\S+)", line)
    if m:
        rows.append((int(m.group(2)), int(m.group(3)), m.group(5)))


def normalized(identity):
    if identity == "unknown":
        return identity
    set_code, _, number = identity.partition(":")
    return "%s:%s" % (set_code, number.lstrip("0") or number)


def anchor_of(first, last):
    hits = [a for a in anchors if first >= a - span - 2 and last <= a + span + 2]
    return hits[0] if len(hits) == 1 else None


problems = []
found = {}
for first, last, identity in rows:
    anchor = anchor_of(first, last)
    identity = normalized(identity)
    if anchor is None:
        print("note: presentation f=%d..%d (%s) matches no anchor, ignored"
              % (first, last, identity))
        continue
    found[anchor] = identity
    allowed = {normalized(i) for i in truth[anchor]} | {"unknown"}
    print("frame %3d -> %s" % (anchor, identity))
    if identity not in allowed:
        problems.append("frame %d shows %s, reported %s"
                        % (anchor, sorted(truth[anchor]), identity))

for anchor, identity in sorted(must.items()):
    if found.get(anchor) != normalized(identity):
        problems.append("frame %d must be %s, got %s" % (anchor, identity, found.get(anchor, "nothing")))

if problems:
    raise SystemExit("frame check failed:\n  " + "\n  ".join(problems))

print("frame check passed (%d frames, %d identified)"
      % (len(anchors), sum(1 for _, _, i in rows if i != "unknown")))
PY
