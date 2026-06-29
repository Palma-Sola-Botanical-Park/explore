"""
data/schemas/right_now.py

Schema for the 'Right Now' tab -> data/published/right_now.json.
Read by validate_promote.py against the shared CHECKS engine.

The curated "now-lens": what's worth noticing in the park today -- plant blooms
and notable wildlife sightings. Species facts (photo, latin name, profile/QR link)
are joined from the species data by psbp_id at RENDER time, never restated here.
"""

SCHEMA = {
    "tab": "right_now",                                   # self-doc only; engine ignores it
    "human": "What's worth noticing in the park right now -- blooms and sightings.",

    # missing any of these -> FILE block (red), serve last-known-good. Structural only;
    # display-first, matching events/classes/volunteer.
    "required_headers": ["display", "common_name"],

    "identity":         ["common_name", "area"],          # keys the staging-vs-prior diff
    "drop_when_display": ["off", ""],                      # off OR blank = not live (the toggle)
    "autofix_trim":      True,

    # An empty Right Now is a VALID state (nothing notable this week); the page falls
    # back to the evergreen hero pool. Raise to 1 only if you'd rather an empty feed
    # hold last-known-good and go red on the board.
    "volume_min": 0,

    "rules": [
        {"field": "common_name", "check": "required",
         "severity": "error", "scope": "row",
         "why": "Can't be blank -- it's the card label."},

        {"field": "kind", "check": "in_vocab",
         "arg": ["blooming", "budding", "fruiting", "fading", "sighting"],
         "severity": "warn", "scope": "field",
         "why": "Must be one of: blooming, budding, fruiting, fading, sighting."},

        {"field": "display", "check": "in_vocab",
         "arg": ["web", "both", "screen", "off"],
         "severity": "warn", "scope": "field",
         "why": "Must be web, both, screen, or off."},

        {"field": "peak_start", "check": "iso_date_or_blank",
         "severity": "warn", "scope": "field",
         "why": "Must be a date (YYYY-MM-DD) if set."},

        {"field": "peak_end", "check": "iso_date_or_blank",
         "severity": "warn", "scope": "field",
         "why": "Must be a date (YYYY-MM-DD) if set."},

        {"field": "peak_end", "check": "ge_field", "arg": "peak_start",
         "severity": "warn", "scope": "field",
         "why": "Should be on or after peak_start."},

        # psbp_id has NO rule on purpose: the engine has no regex check, and fk can't
        # target the species data (build_refs hardcodes 'series'). A bad/blank id is
        # handled at RENDER time -- fail-soft to a standalone card + log -- not here.
        # scientific_name / note / area are free text; rules for absent columns are
        # dormant anyway, so nothing to add.
    ],
}
