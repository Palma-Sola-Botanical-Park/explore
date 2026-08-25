"""
data/schemas/photographers.py  —  validation rules for the `photographers` tab.

Photographer profiles — one row per contributor. This tab migrates the existing
static `data/photographers.json` into the Sheets pipeline. After migration,
`photographers.html` needs to read from `data/published/photographers.json`
instead of the old static file (or route through fetchTab + MIGRATED).

Display-gated since 2026-08-25. It was always-on reference data until then; a
`display` column was added so a profile can be drafted without going live, and
so a future screen format can highlight contributors without the web page
having to show the same set.

Columns: display | id | name | specialty | blurb | inat | site | site_label | focus

⚠ The column had to exist in the Sheet BEFORE these rules were added. With no
`display` column, `row.get("display", "")` returns "" for every row, "" is in
drop_when_display, and the whole tab empties — which trips the volume guard and
freezes the feed on last-known-good. Silent, and it fails safe, but it fails.

`focus` is in the Sheet and has no rule here. Deliberate for now: nothing reads
it. If it starts driving a crop anchor the way photo_credits.focus does, give it
a rule then.

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

    # The two things a profile card can't render without, plus the visibility
    # switch. display-first, matching events/classes and the other gated tabs.
    "required_headers": ["display", "id", "name"],

    # "" (blank) counts as not-live, same as every other gated tab.
    "drop_when_display": ["off", ""],

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: a profile with no id or name is broken ----------------
        {"field": "id",   "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "name", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- vocabulary (warn) — a typo hides the row from everyone ------------
        {"field": "display", "check": "in_vocab",
         "arg": ["web", "both", "screen", "off"], "severity": "warn", "scope": "field",
         "why": "Must be web, both, screen or off. Blank counts as off."},

        # --- format (warn) — external profile links ---------------------------
        {"field": "inat", "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},
        {"field": "site", "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},
    ],
}
