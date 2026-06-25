"""
data/schemas/organization.py  —  validation rules for the `organization` tab.

Board members and front office staff. Always-on reference data — NO `display`
column, no `status` column. Every row publishes; the page groups and renders by
`type` (front office vs board).

Columns: type | role | name | blurb

volume_min is 1: an empty org list is almost certainly a broken fetch, not a
real edit. The park always has at least an executive director.

(`why` = the plain-language reason shown on the drill-down; see events.py header /
SHEET_SYNC_ARCHITECTURE.md §3 "As-built schema contract".)
"""

TYPE_VALUES = ["front office", "board"]

SCHEMA = {
    "tab": "organization",

    "human": "Board members and front office staff — one row per person.",

    # Name is the natural key; role disambiguates if two people share a name.
    "identity": ["name"],

    # The two columns a person card can't render without.
    "required_headers": ["type", "name"],

    "drop_when_display": [],          # no display column — always-on reference data

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: a person with no type or name is broken ---------------
        {"field": "type", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "name", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- controlled vocab (warn) ------------------------------------------
        {"field": "type", "check": "in_vocab", "arg": TYPE_VALUES,
         "severity": "warn", "scope": "field",
         "why": "Must be front office or board."},
    ],
}
