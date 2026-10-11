#!/bin/bash
# Double-click to open the Placement mapper (http://localhost:8701): see every placement on the satellite map,
# move pins, set sign states, and press "Confirmed here today" / "Looked, not found today" after a spot check.
# It saves to data/sources/placements.json (a backup is kept first). Close this window to stop it.
# Add --dry-run to the python line below if you only want to look around without saving.
cd "$(dirname "$0")/../.."
(sleep 2; open "http://localhost:8701") &
python3 "data/scripts/psbp_placements.py" "$@"
