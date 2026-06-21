#!/usr/bin/env python3
"""
fetch_sheets.py  —  STAGE 1 of the sheet -> JSON pipeline.

Pulls each tab's CSV from the published Google Sheet and writes a *faithful*
mirror to data/staging/<tab>.json. NO validation happens here on purpose: this
is the "what did the sheet literally say at fetch time" snapshot. All the
error-checking lives in stage 2 (validate_promote.py).

This is also where the fragile parse that used to run client-side in every
visitor's browser now lives ONCE, server-side, under test. (The header-row
detection is ported straight out of site.js fetchTab(), which is what kept the
News feed alive on 2026-06-14 — we keep that smarts, just move it here.)

Staging file shape:
    { "headers": [...lowercased header names...], "rows": [ {col: val, ...}, ... ] }

Headers are recorded separately from rows so stage 2 can tell the difference
between "the column is missing" (file-level error) and "the column is present
but every cell is blank" (fine). Run metadata (timestamps) is intentionally NOT
written here — it would dirty every git diff. It goes in _runlog.json instead.

Network: uses only the stdlib (urllib) so there are no pip dependencies to
install in CI. The CSV export URL is already public (the browser uses it today),
so no auth/secrets are needed.

Testing without network: set PSBP_FIXTURE_DIR=/path/to/fixtures and each tab is
read from <dir>/<tab>.csv instead of the network. That's how the gate gets
proven locally before it's ever load-bearing.
"""

import csv
import io
import json
import os
import sys
import urllib.request

SHEET_ID = "12gRB-c4gND8qJWPmwBoV2X4adqTfRROYHtA8jR4-kS4"

# gid map — mirror of TAB in site.js. fetch_sheets uses it to know what to pull.
TAB = {
    "events":           992316234,
    "classes":          141740803,
    "series":           926436540,
    "volunteer":        269225929,
    "announcements":    673905300,
    "newsletters":      1749891854,
    "news":             195499912,
    "venues":           1744975586,
    "wedding_calendar": 1260078193,
    "wedding_gallery":  874456476,
}

# Which tabs this run should pull. PILOT = just events (+ series, which events'
# referential-integrity check needs to resolve its `series` foreign key).
# As tabs are templated, add them here.
PILOT_TABS = ["events", "classes", "series", "volunteer", "announcements",
              "newsletters", "news", "venues", "wedding_calendar", "wedding_gallery"]

# Column-name tokens seen across the live tabs. We locate the header row by its
# CONTENT (the row matching the most of these) rather than trusting a fixed row
# index — so an inserted blank row or a "Convert to table" wrapper can't shove
# the feed off its rails. Ported + extended from site.js KNOWN_HEADERS.
KNOWN_HEADERS = {
    "display", "date", "date_end", "time", "title", "instructor", "category",
    "description", "series", "weekday", "day", "link_url", "link_text",
    "registration_url", "cost", "fundraiser", "kid_friendly", "save_the_date",
    "closes_park", "public_note", "close_time", "active", "active_from",
    "active_to", "name", "blurb", "flyer_url", "flyer_text", "status", "note",
    "pinned", "headline", "subhead", "hero_image", "photo", "photo_url", "url",
    "order", "id", "scope", "duration", "capacity", "includes", "deposit",
    "manager", "insurance", "caption", "image",
}


def norm_header(s):
    """Lowercase, trim, spaces -> underscores. Matches site.js normHeader."""
    return (s or "").strip().lower().replace(" ", "_")


def parse_sheet_csv(text):
    """
    Pure function: CSV text -> {"headers": [...], "rows": [...]}.

    Convention: row 0 = section title, row 1 = headers, row 2 = hint row, then
    data. But we DON'T trust those positions — we find the header row by content
    (the row with the most KNOWN_HEADERS hits in the first 12 rows) and take
    data two rows below it (skipping the hint row directly beneath the header).
    """
    grid = list(csv.reader(io.StringIO(text)))
    if not grid:
        return {"headers": [], "rows": []}

    header_idx, best = 1, 0
    for i in range(min(len(grid), 12)):
        hits = sum(1 for c in grid[i] if norm_header(c) in KNOWN_HEADERS)
        if hits > best:
            best, header_idx = hits, i
    if best < 2:
        header_idx = 1  # not confident -> fall back to the documented layout

    headers = [norm_header(c) for c in grid[header_idx]]
    data_start = header_idx + 2  # skip the hint row directly beneath the header

    rows = []
    for raw in grid[data_start:]:
        if not any((c or "").strip() for c in raw):
            continue  # skip fully-blank rows
        row = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            row[h] = (raw[i] if i < len(raw) else "").strip("\ufeff").rstrip("\n")
        rows.append(row)

    return {"headers": [h for h in headers if h], "rows": rows}


def fetch_csv_text(tab):
    """Get the CSV text for a tab — from a fixture dir if set, else the network."""
    fixture_dir = os.environ.get("PSBP_FIXTURE_DIR")
    if fixture_dir:
        with open(os.path.join(fixture_dir, f"{tab}.csv"), encoding="utf-8") as f:
            return f.read()
    gid = TAB[tab]
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def main(tabs=None, staging_dir="data/staging"):
    tabs = tabs or PILOT_TABS
    os.makedirs(staging_dir, exist_ok=True)
    prev_dir = os.path.join(staging_dir, ".prev")
    os.makedirs(prev_dir, exist_ok=True)

    failures = []
    for tab in tabs:
        out_path = os.path.join(staging_dir, f"{tab}.json")
        try:
            parsed = parse_sheet_csv(fetch_csv_text(tab))
        except Exception as e:  # noqa: BLE001 — a fetch/parse failure must not
            # overwrite good staging; leave the prior file in place and report.
            failures.append((tab, str(e)))
            print(f"  ! {tab}: fetch/parse failed ({e}) — keeping prior staging")
            continue

        # Snapshot the previous staging (if any) so stage 2 can diff for the
        # dashboard's edit counts. .prev is per-run scratch (gitignored).
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                prev = f.read()
            with open(os.path.join(prev_dir, f"{tab}.json"), "w", encoding="utf-8") as f:
                f.write(prev)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"  staged {tab}: {len(parsed['rows'])} rows, "
              f"{len(parsed['headers'])} columns")

    if failures:
        # Non-zero exit signals trouble, but staging for healthy tabs is written.
        print(f"  {len(failures)} tab(s) failed to fetch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
