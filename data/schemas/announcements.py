"""
data/schemas/announcements.py  —  validation rules for the `announcements` tab.

Homepage + in-park-screen messages. Structural columns are display + title (the
headline a card can't render without). body/link are content: a rename degrades
to a thinner card, never blocks the feed.

Columns: emoji | title | body | link_text | link_url | display
         | show_from | show_until | director_note

director_note (added 2026-09-06) routes a row AWAY from the top bar and into a
block on the homepage, under her name — an editor's welcome rather than a
notice. The bar is for operational facts ("closing 3pm Saturday"); this is the
Executive Director in her own voice, saying what she is pleased about this
month. One flag decides which of the two a row is. Blank = a normal notice.

show_from / show_until (show_from added 2026-09-06) are the window. Together
they let the director PRE-STAGE: write three monthly notes in one sitting, give
each a start and an end, and the right one appears and retires on its own. A
blank show_from means "already showing".

show_until is the expiry date. The web bar is for NEWS —
"the nursery is closed Monday", "new signs are going up this month" — and news
goes stale silently. The tab had no date field at all, which is how it filled up
with evergreen copy instead ("You could get married here"), which is really
screen content. Blank = never expires, which stays right for the screen loop.

NOTE: link_url is intentionally NOT format-checked. Bev's links are often
in-site relative paths ("news.html?story=Bishop", "/docs/news/...pdf"), which a
URL check would falsely flag amber. Same call we made for volunteer's local photo
path and the news image fields — the link still works; the board stays honest.

volume_min is 0 ON PURPOSE: "nothing to announce right now" is a legitimate
editorial state. At the default 1, clearing the board would block the feed and
keep serving the OLD announcements (last-known-good) while the dashboard went red.
Flip it back to 1 if you'd rather an empty board be treated as a broken fetch.

(`why` = the plain-language reason shown on the drill-down; see events.py header.)
"""

DISPLAY_VALUES = ["web", "both", "screen", "off"]

SCHEMA = {
    "tab": "announcements",

    "human": "Homepage + in-park-screen announcements — one row per message.",

    "identity": ["title"],

    "required_headers": ["display", "title"],

    "drop_when_display": ["off", ""],

    "autofix_trim": True,

    "volume_min": 0,   # empty board is a valid editorial state — see docstring

    "rules": [
        # --- row-fatal: an announcement with no headline is broken -----------
        {"field": "title",   "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- controlled vocab (warn) -----------------------------------------
        {"field": "display", "check": "in_vocab", "arg": DISPLAY_VALUES,
         "severity": "warn", "scope": "field",
         "msg": "unknown display value — a typo here hides the row from everyone",
         "why": "Must be web, both, screen, or off — a typo hides the row from everyone."},

        # --- expiry (optional) ------------------------------------------------
        # Warn, not error: a garbled date should never quarantine the message.
        # It just stops expiring, which is the safe direction to fail.
        {"field": "show_from", "check": "iso_date_or_blank",
         "severity": "warn", "scope": "field",
         "why": "If set, must be a real date (YYYY-MM-DD). Blank means show it now."},

        {"field": "show_until", "check": "iso_date_or_blank",
         "severity": "warn", "scope": "field",
         "why": "If set, must be a real date (YYYY-MM-DD). Blank never expires."},

        {"field": "show_until", "check": "ge_field", "arg": "show_from",
         "severity": "warn", "scope": "field",
         "msg": "show_until is before show_from — the row would never appear",
         "why": "Can't be earlier than show_from, or the row never shows."},

        {"field": "director_note", "check": "in_vocab", "arg": ["yes", "no", ""],
         "severity": "warn", "scope": "field",
         "why": "yes moves this row to the Director's note on the home page."},
    ],
}
