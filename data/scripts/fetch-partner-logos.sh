#!/usr/bin/env bash
#
# fetch-partner-logos.sh
# Downloads the Palma Sola partner logos from the current WordPress media
# library and drops them into images/partners/ with clean filenames.
#
# Run it from anywhere — it targets the repo path directly. If your repo
# lives somewhere other than the default below, edit REPO once.
#
# Usage:
#   chmod +x fetch-partner-logos.sh
#   ./fetch-partner-logos.sh
#
set -euo pipefail

REPO="${HOME}/Documents/GitHub/ReworkDemo"
DEST="${REPO}/images/partners"
BASE="https://palmasolabp.org/wp-content/uploads"

mkdir -p "$DEST"
echo "Saving logos to: $DEST"
echo

# source_path_on_wordpress  ->  clean_local_filename
# (filenames chosen for tidiness; rename freely, just tell me the final names
#  so the grid markup matches.)
download() {
  local src="$1" out="$2"
  echo "  - $out"
  curl -fsSL "$BASE/$src" -o "$DEST/$out" \
    || echo "    !! FAILED: $src  (it may have been removed from the media library)"
}

download "2025/05/image009.png"               "partner-01.png"
download "2025/05/image003.png"               "partner-02.png"
download "2025/05/image005.png"               "partner-03.png"
download "2024/02/image001.png"               "partner-04.png"
download "2025/08/image001.png"               "florida-federation-garden-clubs.png"
download "2025/05/image004.png"               "partner-05.png"
download "2025/05/Unknown.png"                "partner-06.png"
download "2023/09/licenceplates.jpg"          "florida-license-plates.jpg"
download "2025/05/image007.png"               "partner-07.png"
download "2024/02/image001-1-1024x338.jpg"    "partner-08-banner.jpg"
download "2025/05/image006.png"               "partner-09.png"
download "2025/07/logoamericangardens.png"    "american-gardens.png"
download "2025/05/4afd92e7-5dc5-6998-f8dc-4e832ecd32d3.png" "partner-10.png"
download "2023/09/mcc.jpg"                     "manatee-community.jpg"
download "2023/09/amichamber.png"             "ami-chamber.png"
download "2026/03/50POPS.png"                 "50-pops.png"

echo
echo "Done. $(ls -1 "$DEST" | wc -l | tr -d ' ') files in images/partners/"
echo "Review them, rename any you want, then commit + push via GitHub Desktop."
