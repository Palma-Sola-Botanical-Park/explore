#!/usr/bin/env bash
#
# shrink.sh — shrink JPGs to a size BUDGET for the PSBP photo pipeline (macOS/sips).
# Steps JPEG quality down until each file is under the target, so it clears the
# pre-commit size guard every time — even on dense images that a fixed quality misses.
#
# Run from the photos/ folder:
#   ./shrink.sh                          # every .jpg under here
#   ./shrink.sh PSBP-00005/*.jpg         # one species
#   ./shrink.sh PSBP-00005/662740624.jpg # one file
#
# Tunables (override inline, e.g.  TARGET_KB=450 ./shrink.sh):
TARGET_KB="${TARGET_KB:-300}"   # aim to land at/under this  (your ~300 KB web-res goal)
HARD_KB="${HARD_KB:-500}"       # the guard's hard ceiling; script flags anything still over
MAX_DIM="${MAX_DIM:-1600}"      # longest side in px; only downscales, never enlarges
Q_START="${Q_START:-72}"        # first quality tried (highest)
Q_MIN="${Q_MIN:-40}"            # never drop below this quality
Q_STEP="${Q_STEP:-8}"           # quality decrement per attempt
DRY_RUN="${DRY_RUN:-0}"         # DRY_RUN=1 to preview without writing

set -euo pipefail
command -v sips >/dev/null || { echo "sips not found (macOS only)."; exit 1; }

target_bytes=$(( TARGET_KB * 1024 ))
hard_bytes=$(( HARD_KB * 1024 ))
human() { awk -v b="$1" 'BEGIN{printf "%.0f KB", b/1024}'; }

shrink_one() {
  local f="$1"
  [ -f "$f" ] || { echo "  skip (missing):  $f"; return; }

  local before w h dir base
  before="$(stat -f%z "$f")"

  if [ "$before" -le "$target_bytes" ]; then
    echo "  skip ($(human "$before"), at/under target):  $f"
    return
  fi

  read -r w h < <(sips -g pixelWidth -g pixelHeight "$f" 2>/dev/null \
    | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print (w?w:0)" "(h?h:0)}')

  local dim_args=()
  if [ "$w" -gt "$MAX_DIM" ] || [ "$h" -gt "$MAX_DIM" ]; then
    dim_args=(-Z "$MAX_DIM")   # downscale ceiling; only when longer than MAX_DIM
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "  would shrink ($(human "$before"), ${w}x${h}) -> <=$(human "$target_bytes"):  $f"
    return
  fi

  dir="$(dirname "$f")"; base="$(basename "$f")"

  # Step quality down from Q_START until the output fits the budget (or hits Q_MIN).
  # Each attempt re-encodes from the ORIGINAL file, never from a prior attempt.
  local q result_tmp="" result_bytes=0 tmp size
  q="$Q_START"
  while true; do
    tmp="$dir/.${base}.shrink.$$.${q}.jpg"
    if ! sips "${dim_args[@]}" -s format jpeg -s formatOptions "$q" "$f" --out "$tmp" >/dev/null 2>&1; then
      rm -f "$tmp" "$result_tmp"; echo "  FAILED to encode (left untouched):  $f"; return
    fi
    size="$(stat -f%z "$tmp")"
    rm -f "$result_tmp"                 # drop the previous (higher-q, larger) attempt
    result_tmp="$tmp"; result_bytes="$size"
    [ "$size" -le "$target_bytes" ] && break   # fits the budget at this quality
    [ "$q" -le "$Q_MIN" ] && break             # hit the quality floor
    q=$(( q - Q_STEP )); [ "$q" -lt "$Q_MIN" ] && q="$Q_MIN"
  done

  if [ "$result_bytes" -ge "$before" ]; then
    rm -f "$result_tmp"; echo "  keep original (no gain):  $f"; return
  fi

  mv "$result_tmp" "$f"   # atomic replace on the same filesystem

  local flag=""
  if [ "$result_bytes" -gt "$hard_bytes" ]; then
    flag="   !! STILL OVER ${HARD_KB} KB at q${Q_MIN} - downscale it (e.g. MAX_DIM=1000)"
  elif [ "$result_bytes" -gt "$target_bytes" ]; then
    flag="   (over ${TARGET_KB} KB aim, but under the ${HARD_KB} KB guard)"
  fi
  printf "  %s -> %s  (-%s%%, q%s)  %s%s\n" "$(human "$before")" "$(human "$result_bytes")" \
    "$(awk -v a="$before" -v b="$result_bytes" 'BEGIN{printf "%.0f",(a-b)*100/a}')" "$q" "$f" "$flag"
}

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

echo "Shrinking ${#targets[@]} file(s) to <=${TARGET_KB} KB (guard ${HARD_KB} KB)${DRY_RUN:+  -- DRY RUN}"
for f in "${targets[@]}"; do shrink_one "$f"; done
echo "Done."
