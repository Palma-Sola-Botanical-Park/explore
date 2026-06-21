"""
data/schemas/wedding_gallery.py  —  validation rules for the `wedding_gallery` tab.

The venue page's photo strip. NO `display` column — drop_when_display is EMPTY
(drop nothing). A gallery row is nothing but its image, so a row with a blank
`image` is pointless and gets quarantined; order/caption renames just degrade
(unsorted, or no caption).

Columns: order | image | caption

`image` is NOT URL-checked — the paths are in-repo relative
("images/venue/wedding1.jpg"), which a URL check would falsely flag. The required
row-check below catches the only failure that matters: an empty image.

(`why` = the plain-language reason shown on the drill-down; see events.py header.)
"""

SCHEMA = {
    "tab": "wedding_gallery",

    "human": "Venue photo gallery — one row per image.",

    "identity": ["image"],

    "required_headers": ["image"],

    "drop_when_display": [],          # no display column — drop nothing.

    "autofix_trim": True,

    "volume_min": 1,

    "rules": [
        # --- row-fatal: a gallery row with no image is an empty frame --------
        {"field": "image", "check": "required", "severity": "error", "scope": "row",
         "why": "Can't be blank."},
    ],
}
