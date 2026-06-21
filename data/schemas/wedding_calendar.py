"""
data/schemas/wedding_calendar.py  —  validation rules for the `wedding_calendar` tab.

Lists ONLY the not-open dates (private rentals / closures). NO `display` column —
every row is a real calendar fact, so drop_when_display is EMPTY (drop nothing).
`date` is the spine: it's how the row lands on the calendar and how venue.html /
events.html compute closures, so a blank/garbled date is row-fatal (split into
required + iso_date so the drill-down distinguishes "blank" from "not a real date",
exactly like events/news).

Columns: date | status | note | closes_park | close_time

volume_min is 0 ON PURPOSE: an empty closure list is a VALID state — it just means
the park is open every day with nothing booked. At the default 1, an empty tab
would block and keep serving stale closures while the dashboard went red.

NOTE: the public-facing reason column is `note` (NOT `public_note`). If the queued
events.html closure-label task is reading `public_note`, point it at `note`.
`close_time` is free text ("5pm", or blank = closed all day) — unchecked on purpose.

(`why` = the plain-language reason shown on the drill-down; see events.py header.)
"""

STATUS_VALUES = ["possible", "booked"]
YES_NO_BLANK  = ["yes", "no", ""]   # in_vocab is case-insensitive in the engine

SCHEMA = {
    "tab": "wedding_calendar",

    "human": "Park-closure / not-open dates — one row per closed date.",

    # one row per closed date.
    "identity": ["date"],

    # the date column is the only thing the calendar truly can't work without.
    "required_headers": ["date"],

    "drop_when_display": [],          # no display column — drop nothing.

    "autofix_trim": True,

    "volume_min": 0,   # empty closure list is valid (park open every day)

    "rules": [
        # --- row-fatal: every closure must land on a real date ---------------
        {"field": "date", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "date", "check": "iso_date", "severity": "error", "scope": "row",
         "why": "Must be a real date (YYYY-MM-DD)."},

        # --- controlled vocab (warn) -----------------------------------------
        {"field": "status",      "check": "in_vocab", "arg": STATUS_VALUES,
         "severity": "warn", "scope": "field",
         "why": "Must be possible or booked."},
        {"field": "closes_park", "check": "in_vocab", "arg": YES_NO_BLANK,
         "severity": "warn", "scope": "field",
         "why": "Must be yes, no, or blank."},
    ],
}
