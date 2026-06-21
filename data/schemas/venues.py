"""
data/schemas/venues.py  —  validation rules for the `venues` tab.

Rentable options + the seasonal price grid. NO `display` column — venues are
always-on reference data (the page decides what to show), so drop_when_display is
EMPTY: nothing is dropped on display. (If it were the usual ["off",""], every
display-less row would read as blank and get dropped -> zero published -> block.)
The canonical key is `id` (weddings/large/medium/small/...); id + name are the
two columns a card can't render without, so both are row-fatal.

Columns: order | id | name | category | scope | duration | capacity | includes |
         sat_season | wknd_season | wkdy_season | sat_off | wknd_off | wkdy_off |
         deposit | manager | insurance | note | photo

Two deliberate NON-checks, to keep the board green and honest:
  * The six price columns (sat_season .. wkdy_off) are NOT validated as numbers —
    the engine has no numeric check today, so a fat-fingered "$3,850" would sail
    through. If you want that guarded it's a one-function add to the engine (a
    `number` check) plus a warn rule per column. Say the word and I'll wire it.
  * `photo` is NOT URL-checked — some photos may be local /ReworkDemo paths,
    which a URL check would falsely flag (same reason we skipped it on volunteer).

(`why` = the plain-language reason shown on the drill-down; see events.py header.)
"""

CATEGORY_VALUES = ["wedding", "rental"]
SCOPE_VALUES    = ["Whole Park", "Building", "Partial"]

SCHEMA = {
    "tab": "venues",

    "human": "Rentable venues and the seasonal price grid — one row per option.",

    # id is the address every price/scope row hangs off; it keys the diff.
    "identity": ["id"],

    # the key + the label a card can't render without.
    "required_headers": ["id", "name"],

    "drop_when_display": [],          # no display column — drop nothing.

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: id is the address, name is the label -----------------
        {"field": "id",   "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "name", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- controlled vocab (warn) -----------------------------------------
        {"field": "category", "check": "in_vocab", "arg": CATEGORY_VALUES,
         "severity": "warn", "scope": "field",
         "why": "Must be wedding or rental."},
        {"field": "scope",    "check": "in_vocab", "arg": SCOPE_VALUES,
         "severity": "warn", "scope": "field",
         "why": "Must be Whole Park, Building, or Partial."},
    ],
}
