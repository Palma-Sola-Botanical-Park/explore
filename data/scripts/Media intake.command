#!/bin/bash
# Double-click to open the Media intake page (http://localhost:8703).
# Registers park media into data/sources/media_library.json and uploads it to R2.
# Keys go in ~/PSBP-media/r2.env (never in the repo). Close this window to stop.
cd "$(dirname "$0")/../.."
python3 "data/scripts/media_intake.py" "$@"
