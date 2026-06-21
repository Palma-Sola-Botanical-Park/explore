"""
data/schemas/newsletters.py  —  validation rules for the `newsletters` tab.

The newsletter archive: one row per past monthly newsletter (date, title,
description, link to the PDF). Feeds only the "Past newsletters" rail at the
bottom of news.html. No foreign keys, no images — a flat, simple tab.

Same engine as events.py; only the rule list differs. (`why` = the plain-language
reason shown on the drill-down; gate semantics: see events.py's header or
SHEET_SYNC_ARCHITECTURE.md §3 "As-built schema contract".)

link_url is intentionally NOT URL-checked: newsletter links are local repo
paths (/docs/news/...pdf), which url_or_blank (http[s]-only) would falsely flag.
"""

DISPLAY_VALUES = ["web", "both", "screen", "off"]

SCHEMA = {
    "tab": "newsletters",

    "human": "Newsletter archive — one row per past newsletter.",

    # Newest-first archive keyed on the month; date+title is a stable identity.
    "identity": ["date", "title"],

    # STRUCTURAL only: display + the date that drives sort and the month label.
    "required_headers": ["display", "date"],

    # off / blank display rows never reach any page.
    "drop_when_display": ["off", ""],

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: bad date breaks the newest-first sort + month label ---
        {"field": "date", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "date", "check": "iso_date", "severity": "error", "scope": "row",
         "why": "Must be a real date (YYYY-MM-DD)."},

        # --- controlled vocab (warn) ------------------------------------------
        {"field": "display", "check": "in_vocab", "arg": DISPLAY_VALUES,
         "severity": "warn", "scope": "field",
         "msg": "unknown display value — a typo here hides the row from everyone",
         "why": "Must be web, both, screen, or off — a typo hides the row from everyone."},

        # NB: link_url is NOT url-checked — newsletter links are local
        # /docs/news/...pdf paths, which url_or_blank (http[s]-only) would flag.
    ],
}
