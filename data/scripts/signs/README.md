# Sign production

Moved into the repo 2026-08-27. These lived in `~/Downloads/PSBP Print Tools`,
which is how Medium #21 came to describe them as living "outside the repo and
running standalone" — true, and the reason nobody could check them.

## What is here, and what is deliberately not

**Here** — the toolchain, about 700 KB:
`make_signs.py` · `make_palm_signs.py` · the `batch*.txt` work lists · `fonts/`

**NOT here, on purpose:**
- **`signs_out/`** — 276 MB of generated PDFs. Output, not source; regenerable
  from these scripts plus `plant_signage.json`. Keep it on disk, out of git.
- **`venv/`** — 38 MB Python environment. Rebuilt on demand, never versioned.

Randy's rule, and it is the right one: *nothing large like PDF signs should
ever go in the repo.*

## Running them

The scripts read the repo READ-ONLY and default to `~/Documents/GitHub/explore`.
Point them elsewhere with `PSBP_REPO=/path/to/explore`. Output goes to their own
`signs_out/`, which should sit outside the repo.

## ⚠ `sign_copy_SUPERSEDED.json`

Kept as a historical record, renamed so nothing loads it by accident.

It was the hand-curated sign copy — a short `origin` and a sign-length `teaser`
per species — and on 2026-08-25 **all of it was merged into
`plant_signage.json` as schema 1.5**, under `origin_short` and `teaser`.

`resolve_copy()` in `make_signs.py` checks `sign_copy.json` FIRST and only then
falls back to the signage file. Since `_ORIGIN_KEYS` already begins with
`origin_short` and `_TEASER_KEYS` contains `teaser`, **the scripts now read the
merged fields with no change** — so a live `sign_copy.json` would only serve to
override maintained data with a frozen copy of itself.

That is the `data/sources/photographers.json` trap exactly: a hand-made file
nothing generates, quietly taking precedence. Hence the rename.
