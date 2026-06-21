"""
data/schemas/events.py  —  the validation rules for the `events` tab.

A schema is plain data. validate_promote.py's engine interprets it, so adding a
new tab later = writing a new schema file like this one; the engine is shared.
Full as-built contract: SHEET_SYNC_ARCHITECTURE.md §3 "As-built schema contract".

Rule anatomy:
    {"field", "check", "severity", "scope", optional "arg", optional "msg", optional "why"}

`why` is a MINIMAL, PAGE-AGNOSTIC, LITERAL restatement of what the rule checks,
in plain English — NOT a downstream-consequence essay. The engine ignores it
(like `tab`); it's copied into health/<tab>.json so the drill-down page can show
each rule + its live status + its reason side by side, legible to a volunteer.

The engine acts on exactly ONE special combination:
    severity "error" + scope "row"  ->  QUARANTINE that row (publish the rest)
EVERY other combination is treated as a WARNING — the row still publishes and
the issue is logged on the board. In particular:
    scope "field" (any severity)  ->  warning
    scope "file"  (any severity)  ->  warning   (NOT a tab block)

File-level blocking — holding last-known-good when a whole column is missing —
comes ONLY from `required_headers`, never from a rule. So `scope` is not an
independent dial: only ("error","row") changes an outcome. We still write
scope:"field" vs "row" for human readability, but "file" on a rule does nothing.
(This corrects an earlier version of this docstring that claimed scope:"file"
blocks the tab — the engine has never done that.)

`volume_min` (engine default 1): the floor of publishable rows below which the
whole feed is blocked and last-known-good is held. Zero publishable rows is
almost always a broken fetch, not a real edit. Raise it per-feed only if a tab
should never legitimately shrink past some count.
"""

# Controlled vocab (EVENTS_DATA_MODEL.md §2). Type these exactly in the sheet.
CATEGORIES = [
    "Fitness & Wellness", "Talks & Learning", "Workshops", "Family & Kids",
    "Arts & Music", "Community", "Volunteer", "Private",
]
# display: web/both/screen all PUBLISH; off (and blank) are dropped at promote;
# anything else is a typo that would silently hide a row -> warn. (screen is
# not-for-web, not dead — the in-park screen pages need it.)
DISPLAY_VALUES = ["web", "both", "screen", "off"]
# secondary flags. in_vocab is case-insensitive in the engine (it lowercases
# both sides), so the lowercase three-value form covers Yes/No/blank dropdowns.
YES_NO_BLANK = ["yes", "no", ""]

SCHEMA = {
    "tab": "events",

    # One-line plain-language summary of the feed (shown atop the detail page).
    "human": "Dated park events — one row per event (multi-day events use date_end).",

    # Identity used for diffing staging vs prior (the dashboard's edit counts).
    # A multi-day run is ONE row (date + date_end), so date+title is stable.
    "identity": ["date", "title"],

    # Missing any of these column HEADERS = the tab is untrustworthy -> block,
    # serve last-known-good. (This is the 2026-06-14 News-went-dark catch.)
    "required_headers": ["display", "date", "title"],

    # Promote drops rows whose display is off or blank (both mean "not live").
    # web / both / screen all publish; the page does the web-vs-screen split.
    "drop_when_display": ["off", ""],

    # Autofixes applied to a working copy before checks (staging stays raw).
    "autofix_trim": True,  # strip surrounding whitespace on every cell

    # Block the feed (hold last-known-good) if fewer than this many rows publish.
    "volume_min": 1,

    "rules": [
        # --- structural / row-fatal -------------------------------------------
        {"field": "date",  "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "date",  "check": "iso_date", "severity": "error", "scope": "row",
         "why": "Must be a real date (YYYY-MM-DD)."},
        {"field": "title", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- multi-day events (date_end) --------------------------------------
        # Blank = ordinary one-day event. If set, must be a real date >= date.
        {"field": "date_end", "check": "iso_date_or_blank", "severity": "error", "scope": "row",
         "why": "If set, must be a real date (YYYY-MM-DD)."},
        {"field": "date_end", "check": "ge_field", "arg": "date",
         "severity": "error", "scope": "row",
         "msg": "date_end is before date — a backwards span would smear across the calendar",
         "why": "If set, can't be earlier than the start date."},

        # --- controlled vocab (warn: show it rather than hide it) -------------
        {"field": "category", "check": "in_vocab", "arg": CATEGORIES,
         "severity": "warn", "scope": "field",
         "why": "Must be one of the 8 approved categories."},
        {"field": "display",  "check": "in_vocab", "arg": DISPLAY_VALUES,
         "severity": "warn", "scope": "field",
         "msg": "unknown display value — a typo here hides the row from everyone",
         "why": "Must be web, both, screen, or off — a typo hides the row from everyone."},

        # --- secondary flags (must be yes/no/blank) ---------------------------
        {"field": "kid_friendly",  "check": "in_vocab", "arg": YES_NO_BLANK, "severity": "warn", "scope": "field",
         "why": "Must be yes, no, or blank."},
        {"field": "save_the_date", "check": "in_vocab", "arg": YES_NO_BLANK, "severity": "warn", "scope": "field",
         "why": "Must be yes, no, or blank."},
        {"field": "fundraiser",    "check": "in_vocab", "arg": YES_NO_BLANK, "severity": "warn", "scope": "field",
         "why": "Must be yes, no, or blank."},
        {"field": "closes_park",   "check": "in_vocab", "arg": YES_NO_BLANK, "severity": "warn", "scope": "field",
         "why": "Must be yes, no, or blank."},

        # --- referential integrity (cross-tab) --------------------------------
        # Every non-blank series must match a name in the series tab. Orphan =
        # warn (the "Part of … series →" link just won't resolve). Trimmed first.
        {"field": "series", "check": "fk", "arg": ["series", "name"],
         "severity": "warn", "scope": "field",
         "msg": "series name doesn't match any row in the series tab",
         "why": "Must match a name in the series tab, or be blank."},

        # --- format (warn) ----------------------------------------------------
        {"field": "registration_url", "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},
        {"field": "link_url",         "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},
    ],
}
