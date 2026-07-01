#!/usr/bin/env bash
#
# shrink.sh — resize + recompress JPGs for the PSBP photo pipeline (macOS/sips).
#
# Run it from the photos/ folder:
#   ./shrink.sh                          # shrink every .jpg under here
#   ./shrink.sh PSBP-00052/*.jpg         # shrink one species' photos
#   ./shrink.sh PSBP-00052/626741187.jpg PSBP-00125/681678865.jpg   # named files
#
# Tunables (override inline, e.g.  MAX_DIM=2000 QUALITY=70 ./shrink.sh):
MAX_DIM="${MAX_DIM:-1600}"          # longest side in px; NEVER enlarges
QUALITY="${QUALITY:-65}"            # JPEG quality, 1–100
MIN_BYTES="${MIN_BYTES:-307200}"    # skip files already under this (300 KB)
DRY_RUN="${DRY_RUN:-0}"             # DRY_RUN=1 to preview without writing

set -euo pipefail

command -v sips >/dev/null || { echo "sips not found (this is macOS-only)."; exit 1; }

human() { awk -v b="$1" 'BEGIN{printf "%.0f KB", b/1024}'; }

shrink_one() {
  local f="$1"
  [ -f "$f" ] || { echo "  skip (missing):  $f"; return; }

  local before after w h dir base tmp
  before="$(stat -f%z "$f")"

  if [ "$before" -lt "$MIN_BYTES" ]; then
    echo "  skip ($(human "$before"), already small):  $f"
    return
  fi

  # Current pixel dimensions, so we only downscale (sips -Z will upscale otherwise).
  read -r w h < <(sips -g pixelWidth -g pixelHeight "$f" 2>/dev/null \
    | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print (w?w:0)" "(h?h:0)}')

  local args=(-s format jpeg -s formatOptions "$QUALITY")
  if [ "$w" -gt "$MAX_DIM" ] || [ "$h" -gt "$MAX_DIM" ]; then
    args+=(-Z "$MAX_DIM")
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "  would shrink ($(human "$before"), ${w}x${h}):  $f"
    return
  fi

  dir="$(dirname "$f")"; base="$(basename "$f")"
  tmp="$dir/.${base}.shrink.$$.jpg"

  if ! sips "${args[@]}" "$f" --out "$tmp" >/dev/null 2>&1; then
    echo "  FAILED (left untouched):  $f"
    rm -f "$tmp"
    return
  fi

  after="$(stat -f%z "$tmp")"
  if [ "$after" -lt "$before" ]; then
    mv "$tmp" "$f"   # atomic replace on the same filesystem
    printf "  %s → %s  (-%s%%)  %s\n" "$(human "$before")" "$(human "$after")" \
      "$(awk -v a="$before" -v b="$after" 'BEGIN{printf "%.0f",(a-b)*100/a}')" "$f"
  else
    rm -f "$tmp"     # re-encode came out larger — keep the original, never inflate
    echo "  keep original (no gain):  $f"
  fi
}

# Targets: the files you name, or every JPG in the tree (skipping hidden temps/.git).
targets=()
if [ "$#" -gt 0 ]; then
  targets=("$@")
else
  while IFS= read -r -d '' p; do targets+=("$p"); done < <(
    find . -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) \
      ! -name '.*' -not -path '*/.git/*' -print0
  )
fi

[ "${#targets[@]}" -gt 0 ] || { echo "No JPGs found."; exit 0; }

echo "Shrinking ${#targets[@]} file(s)  —  max ${MAX_DIM}px, quality ${QUALITY}${DRY_RUN:+, DRY RUN}"
for f in "${targets[@]}"; do shrink_one "$f"; done
echo "Done."
