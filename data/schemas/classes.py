"""
data/schemas/classes.py  —  validation rules for the `classes` tab.

Classes are standing weekly rules (same weekday + time + instructor), NOT dated
events. So: no date/date_end, no series foreign key, no closes_park. The one
field that drives everything is `weekday` — the expander turns it into dated
instances inside the 2-week window — so a blank/bad weekday is a row-fatal error.

Same engine as events.py; only the rule list differs. For the rule anatomy and
the gate's exact semantics — quarantine fires ONLY on severity:"error" +
scope:"row"; file-level blocking comes ONLY from required_headers, never a rule;
`why` is the plain-language reason shown on the drill-down page — see events.py's
header or SHEET_SYNC_ARCHITECTURE.md §3 "As-built schema contract".
"""

CATEGORIES = [
    "Fitness & Wellness", "Talks & Learning", "Workshops", "Family & Kids",
    "Arts & Music", "Community", "Volunteer", "Private",
]
DISPLAY_VALUES = ["web", "both", "screen", "off"]
YES_NO_BLANK = ["yes", "no", ""]   # vocab check is case-insensitive in the engine
# weekday is a single-select dropdown in the sheet — exactly one three-letter
# code per cell (Mon..Sun). No comma-lists, no "Monday" long form.
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

SCHEMA = {
    "tab": "classes",

    "human": "Standing weekly classes — one row per weekday+time slot.",

    # Two same-titled classes can differ by weekday/time (Basic Hatha is Mon 4PM
    # AND Wed 9AM), so identity must include all three or the diff miscounts.
    "identity": ["title", "weekday", "time"],

    "required_headers": ["display", "weekday", "title"],

    # off / blank display rows never reach any page.
    "drop_when_display": ["off", ""],

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- the scheduling rule (row-fatal: no weekday = can't be placed) ----
        {"field": "weekday", "check": "required",            "severity": "error", "scope": "row",
         "why": "Can't be blank."},
        {"field": "weekday", "check": "in_vocab", "arg": WEEKDAYS,
         "severity": "error", "scope": "row",
         "msg": "weekday must be one of Mon..Sun (single code)",
         "why": "Must be one of Mon, Tue, Wed, Thu, Fri, Sat, Sun."},
        {"field": "title",   "check": "required",            "severity": "error", "scope": "row",
         "why": "Can't be blank."},

        # --- season window (optional; if set, must be a real date) ------------
        {"field": "active_from", "check": "iso_date_or_blank", "severity": "error", "scope": "row",
         "why": "If set, must be a real date (YYYY-MM-DD)."},
        {"field": "active_to",   "check": "iso_date_or_blank", "severity": "error", "scope": "row",
         "why": "If set, must be a real date (YYYY-MM-DD)."},
        {"field": "active_to",   "check": "ge_field", "arg": "active_from",
         "severity": "error", "scope": "row",
         "msg": "active_to is before active_from",
         "why": "If set, can't be earlier than active_from."},

        # --- controlled vocab (warn) -----------------------------------------
        {"field": "category", "check": "in_vocab", "arg": CATEGORIES,    "severity": "warn", "scope": "field",
         "why": "Must be one of the 8 approved categories."},
        {"field": "display",  "check": "in_vocab", "arg": DISPLAY_VALUES, "severity": "warn", "scope": "field",
         "msg": "unknown display value — a typo here hides the row from everyone",
         "why": "Must be web, both, screen, or off — a typo hides the row from everyone."},
        {"field": "kid_friendly", "check": "in_vocab", "arg": YES_NO_BLANK, "severity": "warn", "scope": "field",
         "why": "Must be yes, no, or blank."},

        # --- format (warn) ----------------------------------------------------
        {"field": "registration_url", "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},
        {"field": "link_url",         "check": "url_or_blank", "severity": "warn", "scope": "field",
         "why": "If set, must start with http:// or https://."},

        # note: `cost`, `time`, `instructor`, `day` are free text — no rules.
    ],
}
