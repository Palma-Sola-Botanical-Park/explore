"""
data/schemas/tours.py  —  validation rules for the `tours` tab.

Tour definitions — the parent in the tours → tour_stops one-to-many. Each row is
one curated walk through the park (Bite-Sized, Full, Kids, Rare Fruit). The
tour_stops tab holds the actual stops and direction steps for each tour_id.

NO `display` column — visibility is controlled by `status` (Draft / Published).
drop_when_display is EMPTY (drop nothing): ALL rows publish to JSON and the
page filters on status. This keeps draft tours in the JSON for preview tooling
without showing them to visitors.

Columns: tour_id | tour_name | blurb | estimated_minutes | difficulty | hero_image | status

(`why` = the plain-language reason shown on the drill-down; see events.py header /
SHEET_SYNC_ARCHITECTURE.md §3 "As-built schema contract".)
"""

DIFFICULTY_VALUES = ["Easy", "Moderate", "Hard"]
STATUS_VALUES     = ["Draft", "Published"]

SCHEMA = {
    "tab": "tours",

    "human": "Tour definitions — one row per curated park walk.",

    # Each tour is uniquely keyed by its integer id.
    "identity": ["tour_id"],

    # The two things a tour card can't render without.
    "required_headers": ["tour_id", "tour_name"],

    "drop_when_display": [],          # no display column — page filters on status

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: a tour must have an id and a name --------------------
        {"field": "tour_id",   "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "tour_name", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- controlled vocab (warn) -----------------------------------------
        {"field": "difficulty", "check": "in_vocab", "arg": DIFFICULTY_VALUES,
         "severity": "warn", "scope": "field",
         "why": "Must be Easy, Moderate, or Hard."},
        {"field": "status",     "check": "in_vocab", "arg": STATUS_VALUES,
         "severity": "warn", "scope": "field",
         "why": "Must be Draft or Published."},
    ],
}
