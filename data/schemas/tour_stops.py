"""
data/schemas/tour_stops.py  —  validation rules for the `tour_stops` tab.

The child in the tours → tour_stops one-to-many. Each tour has multiple rows
here: Origin (stop 0), interleaved Stop and Directions rows using the 0.5
numbering pattern (0, 0.5, 1, 1.5, 2, 2.5 ...) so sort order is explicit and
stop vs direction is obvious from the number alone.

Row types:
  * Origin      — stop 0, the starting point (the office). Has title + quick_hits.
  * Stop        — integer stop_number (1, 2, 3 ...). Has psbp_id linking to a
                  species page, title, quick_hits.
  * Directions  — half-integer (0.5, 1.5 ...). Has directions_text only.
                  No psbp_id, no title, no quick_hits.

NO `display` or `status` column — visibility is controlled entirely by the
parent tour's status field. All rows publish; the page joins on tour_id and
filters by the parent tour's status.

title and psbp_id are NOT required at the schema level: they're only meaningful
on Origin/Stop rows, not Directions. The engine doesn't support conditional
rules (required-when-type-is), so a missing title on a Stop row is caught
visually, not by the gate. Same for psbp_id — it links to a species page, but
only on Stop rows, and there's no engine FK check against the JSON masters.

Columns: tour_id | stop_number | type | psbp_id | title | quick_hits | directions_text

(`why` = the plain-language reason shown on the drill-down; see events.py header.)
"""

TYPE_VALUES = ["Origin", "Stop", "Directions"]

SCHEMA = {
    "tab": "tour_stops",

    "human": "Tour stops and directions — many rows per tour, ordered by stop_number.",

    # Composite key: which tour + which position in the sequence.
    "identity": ["tour_id", "stop_number"],

    # The three columns the renderer can't sequence a tour without.
    "required_headers": ["tour_id", "stop_number", "type"],

    "drop_when_display": [],          # no display column — parent tour controls visibility

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: every row must belong to a tour and have a position ---
        {"field": "tour_id",     "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "stop_number", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "type",        "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "type",        "check": "in_vocab", "arg": TYPE_VALUES,
         "severity": "error", "scope": "row",
         "msg": "type must be Origin, Stop, or Directions — renderer can't handle unknown types",
         "why": "Must be Origin, Stop, or Directions."},

        # NOTE: psbp_id is NOT FK-checked against plant_signage.json / wildlife_signage.json.
        # Those are JSON masters, not Sheet tabs, so the engine's fk check can't reach them.
        # A bad psbp_id just means the "learn more" link won't resolve — cosmetic, not fatal.

        # NOTE: title is NOT required — it's only meaningful on Origin/Stop rows.
        # Directions rows intentionally leave it blank.
    ],
}
