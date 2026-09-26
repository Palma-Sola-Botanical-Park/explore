# CLAUDE.md — explore (Palma Sola Botanical Park)

The public website of a free, volunteer-run, ten-acre botanical park in Bradenton, Florida.
Static GitHub Pages. Species field-guide pages that QR signs point at, Bev's events and news
from a Google Sheet, and the decks that play on the office and galleria TVs.

Randy is the only maintainer. He decides. You propose, build, verify, and report plainly.

**THIS REPO IS PUBLIC.** Board members and visitors can read every commit message, HTML comment,
JSON field, and this file. Reasoning that is internal, political, or undecided goes to the
private `park-library` repo (`../park-library/system docs/`) or nowhere. Never here.

---

## 1. Working with Randy

**How he works**
- He never uses a terminal. GitHub Desktop, buttons, double-clicks. A command line is not a
  deliverable for him. Do the thing yourself, or build it into Species Manager as a button.
- He commits and pushes himself. Stage nothing, commit nothing, push nothing unless he asks.
  When he does ask, show what will go in first.
- He reads the first few lines of a message and replies. Lead with the answer. Short.
  A question buried at the bottom will not be seen.
- He reports symptoms ("the mix feels wrong", "I don't see changes"). They are almost always
  precise. Go look at the data or the console before adjusting anything by feel.
- He runs your work past ChatGPT or Gemini and brings the notes back. Treat those as claims to
  verify against the source, not as votes. They have been wrong here more than once.
- When he says "don't build anything" or "don't do anything", stop. Report exactly what already
  landed. Offer finish / revert / leave. Do not decide for him.
- On a phone in the park ("field mode"): one line, confirm and stop. Save the analysis.

**How to work**
- Talk an idea through before building it. Name the specific case where the current thing fails;
  if you can't, say it's fine and move on. Offer the smaller fix first, file the bigger one.
- Good and simple beats perfect. Store the words, not a recipe for the words. If closing the last
  10% means another script, flag, or special case, stop and say so.
- One finding at a time, each with a concrete proposal, then wait. Never "shall I fix all eight?"
- Complete drop-in files or exact edits. Never fragments he has to splice by hand.
- Before touching more than one file, say which and why.
- Name features by where he sees them ("the flip cards on the home page"), then the mechanism.
- Build a demo he can click for edge cases (a `foo2.html` with a DEMO banner and fake data).
  He judges by poking, not by reading a paragraph.
- Do not report unused fields, odd keys, or "these two could drift". Only wrong information that
  reaches a visitor's page is a finding.
- Do not call his data machine-generated or low-confidence because a label says `kml` or
  `import`. Ask what the label means first.
- Infrastructure (DNS, hosting): lead with what cannot break, what is reversible, and who is
  needed for how long. Mechanism only if asked.
- A vague symptom gets a question, never an edit. NEVER revert or restore a file on a hunch.
  Nothing here is production. There is no outage clock. Understand first, then change.
- If a page you write will be read by others (Bev, the board, a printer), it is finished only
  when it is in the repo or handed over as a file, not when it exists in chat.

---

## 2. Hard rules (never break)

1. **QR URLs are permanent.** Hundreds of printed signs point at
   `plants/PSBP-xxxxx-Common-Name.html`. Never rename, move, or delete a published species page.
   A common-name change on a `status: html` record changes the filename: stop and ask.
2. **PSBP IDs are permanent and only Species Manager mints them.** `PSBP-00001`–`89999` plants,
   `PSBP-90000`–`99999` wildlife. Never assign one by hand. Never key anything on a common name.
3. **Masters in `data/sources/`, everything else is generated.** Never hand-edit `plants.json`,
   `wildlife.json`, or anything under `plants/` or `wildlife/`. Never scrape HTML back into data.
4. **Re-read a source JSON inside the same script that writes it.** Randy edits the same files
   through Species Manager while you work. A copy parsed earlier in the session will clobber his
   work silently. After writing, `git diff --stat` the file and confirm the change count.
5. **Never write `data/sources/placements.json`.** Not a row, not a field. He resolves species at
   the tree, in iNaturalist. Reading it is fine.
6. **Sheet feeds are generated.** `data/published/*.json` comes from Bev's Google Sheet via the
   GitHub Action every ten minutes. Fix content in the Sheet, never in the JSON. The exceptions are
   the hand-maintained files listed in §4.
7. **No photo publishes without a `photo_credits.json` record** with a Creative Commons licence
   and a credit line. `display_name()` in `psbp_common.py` is the only way to resolve a credit.
8. **Relative paths only** (`images/…`, `../css/…`). The site lives at
   `palma-sola-botanical-park.github.io/explore/` today and moves to `palmasolabp.org`; a
   root-absolute `/explore/…` breaks on cutover. There are currently zero; keep it that way.
9. **Nothing large enters git.** The pre-commit guard (`.githooks/pre-commit`, wired by
   `core.hooksPath`) blocks photos over 500 KB, images over 1 MB, docs over 3 MB, data masters
   over 5 MB. It is absolute. Never suggest `--no-verify`. Shrink with `data/scripts/shrink.sh`.
   Originals, generated PDFs, venvs, and triage folders stay outside the repo.
10. **Quarantine before delete.** Retired script → `XXX` suffix. Duplicate species →
    `DUPLICATE — see PSBP-xxxxx`, kept. Report, don't erase.
11. **No secrets.** `ANTHROPIC_API_KEY` and `INAT_TOKEN` come from the environment. Nothing
    with a key in it is ever written to the tree.
12. **Minors.** Bright Futures students are credited by first name and initial, or as a group.
    No profile page, no full name in a story, no contact. Randy deliberately has no channel to any
    student outside Wednesday sessions; never propose reaching one. Photos of students live in
    gitignored `images/brightfutures-local/`, never in the public tree.
13. **Don't start servers for him.** Species Manager, `http.server`, anything holding a port.
    Print the command. If you must run one to verify, say so, name the port, kill it after.

---

## 3. Layout

```
index.html visit.html nature.html events.html news.html get-involved.html
contact.html venue.html photographers.html rarefruit.html get-started.html
viewer.html          PDF viewer with park chrome (PDF.js); Drive /pub links route here
walkabout.html       park-walk map of iNat pins, park polygon, accuracy rings
data-health.html     feed pipeline status board
screen.html members.html    redirect stubs — keep them; old kiosks and QR codes land here
plants.json wildlife.json   GENERATED card/search indexes read by nature.html and the screens
plants/  wildlife/          GENERATED species pages, PSBP-xxxxx-Common-Name.html
photos/PSBP-xxxxx/<inat_photo_id>.jpg   web-res copies, ~300 KB, one folder per species
images/              site media; images/brightfutures-local/ is gitignored on purpose
docs/                finished PDFs and press images (tours, news, get-involved)
css/  js/            psbp.css + site.js are the shared chrome; species-v2.* the page layout
fonts/               Playfair Display TTFs (signs and print)
screens/             the TV decks — see §6
data/sources/        THE MASTERS (see §4)
data/published/      what the site reads; feeds from the Sheet + a few hand files
data/staging/        faithful mirror of each Sheet tab, pre-validation
data/schemas/        one .py per Sheet tab, the validation rules
data/scripts/        tooling, everything imports psbp_common.py
data/scripts/signs/  print toolchain (ReportLab + Pillow, the one non-stdlib exception)
.githooks/           the size guard
.github/workflows/sync-sheet.yml   the ten-minute Sheet sync
```

Run scripts from the repo root: `python3 data/scripts/<name>.py`. They self-locate via
`REPO = Path(__file__).resolve().parents[2]` in `psbp_common.py`.

---

## 4. Data model

**Masters (`data/sources/`)**

| File | Shape | Notes |
|---|---|---|
| `plant_signage.json` | `{meta, species:[…]}` | ~300 records, `status` spotted or html |
| `wildlife_signage.json` | `{meta, species:[…]}` | ~115 records; `animal_group` drives theme |
| `research.json` | `{meta, species:[…]}` | ~550 candidates, status research |
| `photo_credits.json` | `{meta, photos:[…]}` | ~1,640 rows keyed by `psbp_id` + `photo_id` |
| `photographer_names.json` | `{login: {display_name, credit_as?, note}}` | keyed by lowercase iNat login |
| `placements.json` | `{meta, placements:[…]}` | where each specimen stands; READ ONLY for you |
| `landmarks.json`, `park_geometry.json` | | the park polygon and named places |
| `publish_state.json` | machine-owned | fingerprints for staleness; safe to delete |
| `provenance/PSBP-xxxxx.json` | | AI draft/revise event log per species |

**Species lifecycle:** `research → spotted → html`. Identity (name, filename, QR) is free to
change at `spotted` and expensive after. Get the binomial right before promoting; check the
accepted name (POWO, Palmpedia) first, it has caught a superseded name every time.

**Species record, the fields that matter:** `id`, `common_name`, `botanical_name`
(`scientific_name` on wildlife), `inat_taxon_id`, `taxonomy.family`, `form`, `status`,
`teaser` (sign copy, 175-char cap, plants only), `quick_hits` (the real prose facts),
`safety_note` (the one safety field that publishes; the older `edibility`/`toxicity` dicts are
still read as a fallback, don't add to them), `watch_invasive` (one loose boolean, by decision),
`page` (the authored layer, below).

**`page.*` is the authored layer** and it is locked (`PAGE_BLOCK_CONTRACT.md`). Eight section
keys for plants, six for wildlife, nothing else. A section is a list of blocks. A block is prose
OR a photograph, never both: the renderer keeps the figure and silently drops the text. Key
absent → old machine fields render. Key present as `[]` → section hidden. Enforced by
`audit_psbp.py --only PAGE`.

**Photos:** `role` is an array (`gallery`, `whole`, `flower`…), `primary_for` names the roles it
leads, `hero: true` is the card and page lead image, `publish_ok` gates publishing. Web-res only
in `photos/`; the source is an iNat URL in `photo_url`.

**Wildlife theme** is derived, not stored: `ANIMAL_GROUP_TO_THEME` in `psbp_common.py` maps
`animal_group` → `bird | butterfly | other`. `check_animal_group` fails closed on an unknown group.

**Sheet feeds (`data/published/`):** events, classes, series, volunteer, announcements, news,
newsletters, venues, wedding_calendar, wedding_gallery, right_now, tour_stops, tours,
organization, photographers. Each has a schema in `data/schemas/` and a health report in
`data/published/health/<tab>.json`. `right_now` is the flip cards on the home page.

**Hand-maintained files in `data/published/` (not from the Sheet):** `display_choices.json`
(the TV controller menu), `brightfutures_today.json`, `brightfutures_showcase.json`. Editing these
is editing the live screens.

---

## 5. Tooling

| Script | Port | Does |
|---|---|---|
| `species_manager.py` | 8700 | the dashboard: Overview, Intake, Photos, Cultivated, Phenology, Preview & Publish, Verify. Mints IDs. Has the "Work on this page" AI button. Holds JSON in browser memory (see rule 4). |
| `plant_publisher.py` | 8701 | `--generate PSBP-xxxxx`, `--generate-all`, `--demote`; writes `plants/` and `plants.json` |
| `wildlife_publisher.py` | 8702 | same for wildlife |
| `psbp_placements.py` | 8701 | placement pinning UI (collides with the plant publisher's port; run one at a time) |
| `audit_psbp.py` | | read-only; sections CREDITS CONTENT LINK PUBLISH FK TAXA META PAGE, `--only X`, `--json` |
| `psbp_page_drift.py` | | read-only; the TRUE staleness check (renders and compares) |
| `fetch_sheets.py` → `validate_promote.py` | | the sync, run by the Action |
| `shrink.sh` | | JPEG size budget via sips; `DRY_RUN=1` prints a banner but still writes, verify before trusting |

- Species Manager's Health tab shells out to `audit_psbp.py --json`. One implementation of every
  rule. A useful new check becomes a tab or button there, never a loose script or `.app`.
- `--generate-all` is always safe: unchanged pages come out byte-identical.
- The `PUBLISH` audit compares a template fingerprint and flags hundreds of pages after any edit
  to a publisher. That is a false alarm. `psbp_page_drift.py` is the answer.
- Audits detect disagreement between two sources, never wrongness. A clean audit is necessary,
  not sufficient.
- Python: standard library only for the pipeline and servers. GitHub Desktop runs hooks with
  Apple's `/usr/bin/python3` (3.9), not Homebrew's; write hooks to fail open with a note.
- Species Manager is the only tool that writes to iNaturalist (the Cultivated tab's "not wild"
  vote). Every other iNat use is read-only.

**iNat rules of thumb**
- Park scope for READS is the polygon in code (`PARK_POLYGON` in `species_manager.py` and
  `walkabout.html`), pin-only. Never `project_id` or `place_id` for reads: both apply iNat's
  accuracy rule and drop long-lens birds and insects. WRITES use the project on purpose.
- "Casual" grade on a park record means cultivated. Not a quality signal. Never surface it.
- Agreement is not evidence. Count identifiers who knew there was a look-alike and said why they
  ruled it out, not identifiers who agreed.
- The three regular nature photographers upload on request: trust the date, never the clock
  time, never the pin.
- Dave Vanderbilt has two iNat accounts (phone observes, computer identifies). Check both.

---

## 6. Screens

`screens/screen.html` is the everyday galleria/office deck (`?mode=office`). Event decks live in
`screens/events/` on the shared `event-deck.js` engine. `screens/screen-brightfutures.html` is the
Wednesday volunteer deck, standalone. All read the same published JSON as the site, filtered to
`display ∈ {screen, both}`.

- A screen has no viewer: every slide is complete alone, few words, huge, readable across a room.
- Content test: does it only make sense because you are standing in the park? Otherwise, website.
- Asks are capped at a quarter of the deck and spaced out.
- Each slide renders inside a try/catch so one bad row can't take the wall down. That also hides
  failures: when the mix "feels wrong", read the console before touching weights.
- **New screen file → add its row to `data/published/display_choices.json` in the same commit,
  unasked.** The office mini-PC controller reads that file after its ten-minute pull.
- Kiosks and GitHub Pages cache CSS for ten minutes. Bump `?v=` on the stylesheet link when you
  change a deck's CSS, or "I don't see changes" follows.
- Nothing under 17 px at 1080p. Names never in a headline. Caption bottom-left, on a plate.

---

## 7. Copy and design

**Type and colour.** Playfair Display for headings, Source Sans 3 for body. Tokens in
`css/site.css` and `css/psbp.css`: `--green-deep #1a3a1f`, `--green-mid #2d6a35`, `--gold #c5922a`,
`--gold-light #eabc5a`, `--cream #faf8f3`. Body text is `--t-base` (17 px). **Nothing a reader
follows goes below it**: captions, tips, list items, instructions are reading text. Write
`var(--t-base)`, never a hardcoded rem. He raises this every session; fix the habit, not the instance.

**Phone first.** Most visitors arrive from a QR sign, one-handed, in the sun.

**Voice on species pages.** A stroll with someone who knows the plant and is easy to talk to.
Terse, thorough, fun to read, accurate. The good fact is a mechanism or a consequence, not a
category. Never "according to Kew" or "UF/IFAS says" in prose; state what we believe. Never an
iNat detail (observation, grade, identifier) in visitor copy. Two to four photos per page, never
two adjacent, each after the prose it illustrates. Captions add a detail, never carry the point.
Don't rewrite what is already good; fix what's wrong, write what's missing, say what changed.

**Facts.** Never invent a park observation ("three or four species at once" from "multiple").
Never infer where a plant stands from a photo. Where-to-find-it comes from Randy or
`park-library/system docs/PARK_TOUR_BEARINGS.md`. When sources conflict, say so plainly and land
on what is not in dispute; never silently pick a number. Describe the tagged species with
confidence and log ID doubt in `BOTANICAL_QUESTIONS.md`, not on the page.

**Units.** Imperial, spelled out, rounded to the scale of the number, never alongside metric.
Small things get a comparison ("pea-sized"). Areas get a local yardstick, not a figure.

**Standing wording rules.**
- Admission is free in the present tense ("free to visit"). Never "always free" or "free forever".
  Never hedge either ("free for now").
- "We", not "the park", when the park addresses the reader. "Our park" in mission and invitation
  lines only, never globally, never in species prose.
- Invasives and problem plants: state the observable fact. Never "kept as an educational
  example", never "we are fighting it".
- Donors: name the donor and what they gave. Never the amount, never Randy's matching gift.
- No chips, badges, or traffic lights for anything contested. Invasive status, toxicity, and
  edibility are prose by decision. Do not propose new true/false or enum fields for nuance.
- One "little challenge" per page at most, anchored in something real about this specimen,
  never one that damages the plant.
- A "series" is a real program Bev would describe to a donor. Several classes sharing a flyer
  are not a series.

**Photos on the site.** A hero shows the plant in the park (pond, path, building behind it),
not a book specimen. The only test is whether the species is identifiable. Detail shots go in the
gallery. A photo in the where-to-find-it section needs a caption that says where.

**Where files live.** A finished file (flyer, PDF, sign) → this repo. A living document Bev edits
→ Google Doc "Publish to web", `/pub` URL. Never a Drive `/file/d/` share link. Anything with a
date, time, and title is a Sheet row, not a document.

---

## 8. The species-page routine

1. Randy clicks "Work on this page" in Species Manager, with notes.
2. He runs the result past ChatGPT, then Work again. Two or three rounds.
3. He adds photos to `photo_credits.json` and picks the hero. AI never places photographs.
4. Claude does finals: cut repetition, cut what another section says better, check each section
   still does its own job, verify claims against the record, place in-flow photos.
5. He previews and publishes.

ChatGPT drafts arrive as `park-library/drafts chatgpt/*_FINISHED.md`. Before reading one, check
its line-2 botanical name against the record. Read the "Unverified" list at the bottom first.
Pace is about two per day; old pages are fine as they are.

---

## 9. Where the deeper docs are (private repo, `../park-library/system docs/`)

- `HANDOFF.md` — **read first.** Where every open thread stands, rewritten at the end of each
  session. When he says "wrap up" or "handoff", rewrite it: done, next, decisions with reasons,
  dead ends.
- `CLAUDE_ONBOARDING.md` — the people, the history, the working relationship in full
- `PSBP_ARCHITECTURE.md` + `_APPENDIX.md` — deep technical reference
- `PSBP_ACTIONS.md` — the live priority list; **Randy reads only the index rows**, ~80 chars each
- `BOTANICAL_QUESTIONS.md` — open ID questions, for the horticulture crew
- `PAGE_BLOCK_CONTRACT.md`, `PAGE_COPY_FLAGS.md`, `SPECIES_PAGE_RECIPE.md`, `WRITING_UNITS.md`
- `PARK_TOUR_BEARINGS.md` — where things are, in words, by placement area
- `WEB_HOW_IT_WORKS.md`, `WEB_CUTOVER_PREP.md`, `WEB_MIGRATION_MASTER.md` — the domain move
- `PSBP_Mini_PC_Display_System_Deployment_Plan_UPDATED.md` — the TV computers
- `BRIGHT_FUTURES_DECK.md` — every slide of the Wednesday deck, its words, timing and weight
- `../prototypes/` — demo pages and print drafts kept out of the public site, each folder has a README

Any specific claim in those docs is a lead, not a fact. Verify against the code or the JSON
before acting on it or repeating it, and cite the file and line when you do.

**Doc discipline:** index rows are his interface and must stay one line. Detail below the tables
is for the next Claude and can be long. Record decisions and constraints, not arguments.
When he says where something is in the park, it goes in `PARK_TOUR_BEARINGS.md` that day.

---

## 10. Before you finish a task

- Syntax-check anything you wrote (`python3 -m py_compile`, `node --check`, `json.tool`).
- Verify in the browser on `localhost`, by reading the DOM, not by trusting a screenshot's timing.
- `git status`. Tell him what is modified and what to commit. Do not commit for him.
- If a claim in this file turned out wrong, fix this file in the same change.
