"""
data/schemas/announcements.py  —  validation rules for the `announcements` tab.

Homepage + in-park-screen messages. Structural columns are display + title (the
headline a card can't render without). body/link are content: a rename degrades
to a thinner card, never blocks the feed.

Columns: emoji | title | body | link_text | link_url | display | show_until

show_until (added 2026-09-05) is the expiry date. The web bar is for NEWS —
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
        {"field": "show_until", "check": "iso_date_or_blank",
         "severity": "warn", "scope": "field",
         "why": "If set, must be a real date (YYYY-MM-DD). Blank never expires."},
    ],
}
