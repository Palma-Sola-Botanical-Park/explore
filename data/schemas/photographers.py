"""
data/schemas/photographers.py  —  validation rules for the `photographers` tab.

Photographer profiles — one row per contributor. This tab migrates the existing
static `data/photographers.json` into the Sheets pipeline. After migration,
`photographers.html` needs to read from `data/published/photographers.json`
instead of the old static file (or route through fetchTab + MIGRATED).

Always-on reference data — NO `display` column. Every row publishes; the page
renders all of them.

Columns: id | name | specialty | blurb | inat | site | site_label | focus

`inat` and `site` are full external URLs (iNaturalist profiles, Fine Art America,
etc.) — unlike the news/volunteer image fields which are local paths. These ARE
url_or_blank checked because they're genuine https links.

(`why` = the plain-language reason shown on the drill-down; see events.py header /
SHEET_SYNC_ARCHITECTURE.md §3 "As-built schema contract".)
"""

SCHEMA = {
    "tab": "photographers",

    "human": "Photographer profiles — one row per contributor.",

    # Slug id is the key (helen-lewis, rob-carr, etc.).
    "identity": ["id"],

    # The two things a profile card can't render without.
    "required_headers": ["id", "name"],

    "drop_when_display": [],          # no display column — always-on reference data

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: a profile with no id or name is broken ----------------
        {"field": "id",   "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "name", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- format (warn) — external profile links ---------------------------
        {"field": "inat", "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},
        {"field": "site", "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},
    ],
}
