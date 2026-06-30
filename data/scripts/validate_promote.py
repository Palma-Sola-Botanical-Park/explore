#!/usr/bin/env python3
"""
validate_promote.py  —  STAGE 2 of the sheet -> JSON pipeline.

Reads data/staging/<tab>.json, runs it through the tab's schema, and decides —
per the gate — what reaches the browser:

  * file-level error (missing required column, etc.)  -> BLOCK the whole tab:
        do NOT overwrite data/published/<tab>.json, so the site keeps serving
        last-known-good. The board goes red.
  * VOLUME guard (published row count drops below the schema's floor — default
        zero)                                          -> BLOCK the whole tab:
        an empty feed is almost always a broken fetch, not a real edit, so hold
        last-known-good rather than publish nothing. The board goes red.
  * row-level error (bad/missing date, backwards span) -> QUARANTINE the row:
        drop it from published, keep the rest. The board goes amber.
  * warning (bad vocab, orphan series ref, odd URL)    -> publish + log it.

It also diffs new staging against the previous (the .prev snapshot fetch_sheets
left) to produce the dashboard's edit counts, instruments every rule (pass/flag
counts + examples), and writes THREE persistence layers:

    data/published/<tab>.json        the clean array the browser fetches
    data/published/_health.json      current snapshot of every feed (summary page)
    data/published/_runlog.json      rolling run history (~30 runs)
    data/published/_history.json     ~6-month per-feed time series (sparklines/uptime)
    data/published/health/<tab>.json ~2-week deep per-rule detail (drill-down page)

Retention is pruned at WRITE time here (no separate cleanup job): runlog by
count (30), _history by age (~6mo), each health/<tab> timeline by age (~2wk).

Governing principle: this SCRIPT records FACTS (counts, statuses, timestamps,
reasons). The PAGES compute the cleverness (uptime%, churn, sparkline shapes)
at render time from these files. Keep the pipeline dumb and durable.

No third-party deps; stdlib only.
"""

import datetime as dt
import importlib.util
import json
import os
import re
import sys

STAGING = "data/staging"
PUBLISHED = "data/published"
SCHEMAS = "data/schemas"
HEALTH_DIR = os.path.join(PUBLISHED, "health")   # per-feed detail files

# Tabs this run validates + publishes. Grow as tabs are templated. (series is
# staged for the FK check but never published as its own feed — so it's NOT here.)
PUBLISH_TABS = ["events", "classes", "series", "volunteer", "announcements",
                "newsletters", "news", "venues", "wedding_calendar", "wedding_gallery",
                "tours", "tour_stops", "organization", "photographers", "right_now"]
RUNLOG_CAP = 30        # _runlog.json: keep the last N runs
HISTORY_DAYS = 182     # _history.json: keep ~6 months of per-feed points
TIMELINE_DAYS = 14     # health/<tab>.json edit timeline: keep ~2 weeks
DEFAULT_VOLUME_MIN = 1  # block a feed if fewer than this many rows would publish

# ── Right Now enrichment (publish-time stamp) ─────────────────────────────────
# The right_now feed denormalizes a couple of DISPLAY fields onto each entry at
# publish time, joined from the signage masters by psbp_id — same "the script
# records facts, the pages compute cleverness" principle the rest of the pipeline
# follows. The masters stay the single source of truth; this is a re-derived
# cache that refreshes every run, so editing a quick_hit on the master shows up
# on the featured card within one sync. NOTHING here is authored in the sheet.
SOURCES = "data/sources"                      # where the signage masters live
SIGNAGE_MASTERS = ["plant_signage.json", "wildlife_signage.json"]
RIGHT_NOW_BACK_BUDGET = 340   # chars; the card back fits WHOLE quick_hits to this
                              # budget and never truncates — drop a hit, never
                              # ellipsize. Roomy enough for up to three short facts
                              # (or one long + one medium) to fill the back.

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Plain-language fallback if a rule omits its own `why` (schemas should set it).
DEFAULT_WHY = {
    "required":          "Can't be blank.",
    "iso_date":          "Must be a real date (YYYY-MM-DD).",
    "iso_date_or_blank": "If set, must be a real date (YYYY-MM-DD).",
    "ge_field":          "If set, can't be earlier than the compared date.",
    "in_vocab":          "Must be one of the allowed values.",
    "url_or_blank":      "If set, must start with http:// or https://.",
    "fk":                "Must match a row in the referenced tab, or be blank.",
}


# ── small helpers ─────────────────────────────────────────────────────────────

def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        # A generated file got corrupted (e.g. git left conflict markers in
        # _health.json after a bad merge). These files are all disposable —
        # the pipeline rebuilds them — so degrade to the default and shout,
        # rather than crashing the whole run. A corrupt STAGING file this way
        # reads as empty, which the volume guard then blocks (safe: holds
        # last-known-good) instead of publishing nothing.
        print(f"  ! {path}: not valid JSON ({e}) — treating as missing", file=sys.stderr)
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_schema(tab):
    spec = importlib.util.spec_from_file_location(f"schema_{tab}", os.path.join(SCHEMAS, f"{tab}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SCHEMA


def load_fetch_meta():
    """Pull SHEET_ID + the gid map from fetch_sheets.py (sibling file) so the
    detail files can deep-link straight to Bev's sheet tab. Best-effort: if it
    can't be loaded, links degrade to None rather than crashing the run."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_sheets.py")
        spec = importlib.util.spec_from_file_location("fetch_sheets_meta", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "SHEET_ID", None), getattr(mod, "TAB", {})
    except Exception:                                   # noqa: BLE001
        return None, {}


SHEET_ID, TAB_GIDS = load_fetch_meta()


def parse_date(v):
    if not v or not ISO_DATE.match(v):
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def identity_key(row, fields):
    return tuple((row.get(f) or "").strip().lower() for f in fields)


def prune_by_age(records, days, key="at"):
    """Keep only records whose ISO timestamp `key` is within `days` of now."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    kept = []
    for r in records:
        try:
            t = dt.datetime.fromisoformat(r[key])
        except (ValueError, KeyError, TypeError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        if t >= cutoff:
            kept.append(r)
    return kept


# ── the check library (interpreted from the schema) ───────────────────────────
# Each returns None if the cell passes, or a short human reason if it fails.

def check_required(val, **_):
    return None if (val or "").strip() else "missing required value"

def check_iso_date(val, **_):
    return None if parse_date(val) else f"'{val}' is not a YYYY-MM-DD date"

def check_iso_date_or_blank(val, **_):
    if not (val or "").strip():
        return None
    return check_iso_date(val)

def check_ge_field(val, row, arg, **_):
    other = parse_date(row.get(arg, ""))
    this = parse_date(val)
    if not (val or "").strip():        # blank end date -> not a multi-day row
        return None
    if this is None or other is None:  # other rules report the bad date itself
        return None
    return None if this >= other else f"is before {arg} ({val} < {row.get(arg)})"

def check_in_vocab(val, arg, **_):
    return None if (val or "").strip().lower() in [a.lower() for a in arg] else f"'{val}' is not an allowed value"

def check_url_or_blank(val, **_):
    v = (val or "").strip()
    if not v:
        return None
    return None if v.startswith(("http://", "https://")) else f"'{v[:30]}' doesn't look like a URL"

def check_fk(val, arg, refs, **_):
    v = (val or "").strip()
    if not v:
        return None
    ref_tab, ref_field = arg
    return (None if v.lower() in refs.get(ref_tab, {}).get(ref_field, set())
            else "no matching row in the referenced tab")

CHECKS = {
    "required": check_required,
    "iso_date": check_iso_date,
    "iso_date_or_blank": check_iso_date_or_blank,
    "ge_field": check_ge_field,
    "in_vocab": check_in_vocab,
    "url_or_blank": check_url_or_blank,
    "fk": check_fk,
}


# ── diffing (for the dashboard's edit counts) ─────────────────────────────────

def diff_rows(prev_rows, new_rows, identity):
    """Return {added, changed, removed} by identity key."""
    pk = {identity_key(r, identity): r for r in prev_rows}
    nk = {identity_key(r, identity): r for r in new_rows}
    added = sum(1 for k in nk if k not in pk)
    removed = sum(1 for k in pk if k not in nk)
    changed = sum(1 for k in nk if k in pk and nk[k] != pk[k])
    return {"added": added, "changed": changed, "removed": removed}


def edits_total(ch):
    return ch["added"] + ch["changed"] + ch["removed"]


# ── per-rule instrumentation scaffolding ──────────────────────────────────────

def rule_id(rule, seen):
    """Stable-ish id from field+check; disambiguate the rare duplicate."""
    base = f"{rule['field']}:{rule['check']}"
    if base in seen:
        seen[base] += 1
        return f"{base}#{seen[base]}"
    seen[base] = 0
    return base


def build_rstats(schema, headers):
    """One observable record per rule. A rule whose field isn't in the sheet is
    dormant (gray) — present but inert, so optional columns are safe."""
    seen, rstats = {}, []
    for rule in schema["rules"]:
        dormant = rule["field"] not in headers
        rstats.append({
            "id": rule_id(rule, seen),
            "field": rule["field"],
            "check": rule["check"],
            "arg": rule.get("arg"),
            "severity": rule["severity"],
            "scope": rule["scope"],
            "why": rule.get("why") or DEFAULT_WHY.get(rule["check"], ""),
            "dormant": dormant,
            "evaluated": 0, "passed": 0, "flagged": 0,
            "examples": [],            # up to 3 {row, reason}
        })
    return rstats


def rstat_status(rs, ran):
    if rs["dormant"] or not ran:
        return "gray"                  # column absent, or rules never executed
    return "amber" if rs["flagged"] else "green"


def render_rules(rstats, ran):
    """Public-facing rule list for the detail file (drill-down renders this)."""
    out = []
    for rs in rstats:
        out.append({
            "id": rs["id"],
            "field": rs["field"],
            "check": rs["check"],
            "arg": rs["arg"],
            "why": rs["why"],
            "severity": rs["severity"],
            "scope": rs["scope"],
            "status": rstat_status(rs, ran),
            "evaluated": rs["evaluated"],
            "passed": rs["passed"],
            "flagged": rs["flagged"],
            "examples": rs["examples"],
        })
    return out


# ── detail file (health/<tab>.json) ───────────────────────────────────────────

def make_detail(tab, schema, status, published, links, counts, volume_guard,
                rstats, ran, dropped, timeline, block_reason=None):
    return {
        "tab": tab,
        "human": schema.get("human", ""),
        "generated_at": now_iso(),
        "status": status,
        "published": published,
        "block_reason": block_reason,
        "counts": counts,
        "volume_guard": volume_guard,
        "rules": render_rules(rstats, ran),
        "dropped_rows": [{"label": lbl, "reason": rsn} for lbl, rsn in dropped],
        "timeline": timeline,                       # ~2wk, pruned at write
        "schema": {
            "required_headers": schema["required_headers"],
            "identity": schema["identity"],
            "drop_when_display": (schema.get("drop_when_display", [])
                                  if isinstance(schema.get("drop_when_display", []), list)
                                  else [schema.get("drop_when_display")]),
            "volume_min": schema.get("volume_min", DEFAULT_VOLUME_MIN),
        },
        "links": links,
    }


# ── Right Now enrichment helpers ──────────────────────────────────────────────

_signage_master = None  # lazy, cached: merged {psbp_id -> signage record}


def load_signage_master():
    """One merged lookup over BOTH signage masters, keyed by psbp_id.

    Plant ids (PSBP-000xx) and wildlife ids (PSBP-999xx) never collide, so a
    flat merge is safe and lets a sighting and a bloom resolve through the same
    map. Missing/corrupt master degrades to {} (load_json is hardened), which
    makes the enrichment a no-op rather than crashing the run — fail-soft.
    """
    global _signage_master
    if _signage_master is not None:
        return _signage_master
    master = {}
    for fn in SIGNAGE_MASTERS:
        doc = load_json(os.path.join(SOURCES, fn), {"species": []})
        for rec in (doc.get("species", []) if isinstance(doc, dict) else []):
            sid = rec.get("id")
            if sid:
                master[sid] = rec
    _signage_master = master
    return master


def _plainish(s):
    """Strip the markdown emphasis some quick_hits carry (a stray `**bold**`
    authored upstream) so literal asterisks never land on a card. Idempotent on
    clean text. The proper fix is scrubbing the master; this is the guardrail."""
    s = str(s or "")
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)   # **bold** -> bold
    s = re.sub(r"__(.+?)__", r"\1", s)       # __bold__ -> bold
    return s.strip()


def _fit_quick_hits(hits, budget=RIGHT_NOW_BACK_BUDGET, maxn=3):
    """Pick WHOLE quick_hits that fit the card-back budget. Always take the
    first (the headline fact); take each next one only while the running total
    still fits the budget, up to maxn. Never truncate a quote — a fact's payoff
    is its last few words, so we drop, never clip."""
    out, used = [], 0
    for h in (hits or [])[:maxn]:
        h = _plainish(h)
        if not h:
            continue
        if not out:
            out.append(h); used = len(h)
        elif used + len(h) <= budget:
            out.append(h); used += len(h)
        else:
            break
    return out


def enrich_right_now(rows):
    """Stamp display-only fields onto each right_now entry from the signage
    masters, in place. Status is the contract (verified: status==html <=> the
    record has quick_hits AND a published page exists):

      * status == 'html'  -> stamp fitted `quick_hits` (if any) + has_page=True
                             (so the card may show the 'Full plant page ->' link)
      * status == 'spotted' (in review, no page yet) -> stamp nothing, no link
      * no psbp_id (standalone sighting) / id not in a master -> stamp nothing

    has_page also closes a latent gap: without it the render can't tell a
    page-having species from a 'spotted' one, and would link to a page that
    doesn't exist yet for the in-review case.
    """
    master = load_signage_master()
    for row in rows:
        pid = (row.get("psbp_id") or "").strip()
        if not pid:
            continue                       # standalone (e.g. a passing pelican)
        rec = master.get(pid)
        if not rec:
            continue                       # bad/unknown id -> render fail-soft
        if rec.get("status") != "html":
            continue                       # spotted/in-progress: no facts, no page
        row["has_page"] = True
        fit = _fit_quick_hits(rec.get("quick_hits"))
        if fit:
            row["quick_hits"] = fit
    return rows


# ── the core: validate + promote one tab ──────────────────────────────────────

def process_tab(tab, refs, prev_health):
    schema = load_schema(tab)
    staging = load_json(os.path.join(STAGING, f"{tab}.json"), {"headers": [], "rows": []})
    headers = staging["headers"]
    raw_rows = staging["rows"]

    # autofix: trim every cell on a working copy (staging stays raw)
    rows = raw_rows
    if schema.get("autofix_trim"):
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in raw_rows]

    pub_path = os.path.join(PUBLISHED, f"{tab}.json")
    prev_entry = next((f for f in prev_health.get("feeds", []) if f["tab"] == tab), {})
    prev_good = prev_entry.get("last_good_at")
    prev_changed = prev_entry.get("last_changed_at")

    # edit counts: new staging vs the .prev snapshot
    prev_staging = load_json(os.path.join(STAGING, ".prev", f"{tab}.json"))
    prev_rows = prev_staging["rows"] if prev_staging else []
    changes = diff_rows(prev_rows, raw_rows, schema["identity"])
    first_run = prev_staging is None
    edited = first_run or edits_total(changes) > 0
    last_changed_at = now_iso() if edited else (prev_changed or prev_good or now_iso())

    vmin = schema.get("volume_min", DEFAULT_VOLUME_MIN)
    gid = TAB_GIDS.get(tab)
    links = {
        "sheet": (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid={gid}"
                  if (SHEET_ID and gid is not None) else None),
        "published": f"data/published/{tab}.json",
        "detail": f"data/published/health/{tab}.json",
    }

    # display breakdown over the staged rows (blank shown as "(blank)")
    display_breakdown = {}
    for r in rows:
        d = (r.get("display", "") or "").strip().lower() or "(blank)"
        display_breakdown[d] = display_breakdown.get(d, 0) + 1

    # carry the 2-week edit timeline forward from the prior detail file
    prev_detail = load_json(os.path.join(HEALTH_DIR, f"{tab}.json"), {})
    timeline = prune_by_age(prev_detail.get("timeline", []), TIMELINE_DAYS)
    if edited and not first_run:
        timeline = timeline + [{
            "at": now_iso(),
            "added": changes["added"], "changed": changes["changed"],
            "removed": changes["removed"],
        }]

    rstats = build_rstats(schema, headers)

    # ---- FILE-LEVEL: required headers present? --------------------------------
    missing = [h for h in schema["required_headers"] if h not in headers]
    if missing:
        # BLOCK. Leave published as last-known-good (don't touch pub_path).
        msg = (f"missing column{'s' if len(missing) > 1 else ''}: "
               + ", ".join(f"'{m}'" for m in missing))
        prior_count = len(load_json(pub_path, []))
        counts = {"staged": len(raw_rows), "live": 0, "published": prior_count,
                  "dropped_display": 0, "quarantined": 0, "warned": 0}
        feed = {
            "tab": tab, "status": "red", "published": False,
            "rows": prior_count, "changes": changes,
            "last_good_at": prev_good or "unknown",
            "last_changed_at": last_changed_at, "first_run": first_run,
            "messages": [f"BLOCKED — {msg} — serving last-known-good"],
            "counts": counts, "display_breakdown": display_breakdown,
            "open": {"warnings": 0, "quarantined": 0},
            "schema": {"human": schema.get("human", ""),
                       "required_headers": schema["required_headers"],
                       "rule_count": len(schema["rules"])},
            "links": links,
        }
        volume_guard = {"min": vmin, "live": 0, "published": prior_count,
                        "blocked": False, "reason": None}
        detail = make_detail(tab, schema, "red", False, links, counts, volume_guard,
                             rstats, ran=False, dropped=[], timeline=timeline,
                             block_reason=msg)
        return feed, detail

    # ---- ROW-LEVEL + FIELD-LEVEL ---------------------------------------------
    drop_vals = schema.get("drop_when_display", [])
    if isinstance(drop_vals, str):
        drop_vals = [drop_vals]
    drop_vals = set(drop_vals)

    rstat_by_rule = list(zip(schema["rules"], rstats))

    published_rows = []
    quarantined = []        # (label, reason)
    warned_rows = 0
    warning_samples = []
    dropped_display = 0
    live_rows = 0

    for row in rows:
        # visibility filter happens first — an off/blank row never reaches the
        # browser, and we don't bother validating it.
        if (row.get("display", "") or "").strip() in drop_vals:
            dropped_display += 1
            continue
        live_rows += 1

        row_errors, row_warnings = [], []
        row_label = (row.get("title") or row.get("date") or row.get("headline") or "row").strip()

        for rule, rs in rstat_by_rule:
            field = rule["field"]
            if field not in headers:
                continue  # column not in the sheet -> rule is dormant, not a fail
            rs["evaluated"] += 1
            reason = CHECKS[rule["check"]](
                row.get(field, ""), row=row, arg=rule.get("arg"), refs=refs)
            if reason is None:
                rs["passed"] += 1
                continue
            rs["flagged"] += 1
            if len(rs["examples"]) < 3:
                rs["examples"].append({"row": row_label, "reason": reason})
            label = rule.get("msg") or f"{field}: {reason}"
            if rule["severity"] == "error" and rule["scope"] == "row":
                row_errors.append(label)
            else:
                row_warnings.append(label)

        if row_errors:
            quarantined.append((row_label, row_errors[0]))
            continue  # dropped from published
        if row_warnings:
            warned_rows += 1
            if len(warning_samples) < 3:
                warning_samples.append(f"{row_label} — {row_warnings[0]}")
        published_rows.append(row)

    counts = {"staged": len(raw_rows), "live": live_rows,
              "published": len(published_rows), "dropped_display": dropped_display,
              "quarantined": len(quarantined), "warned": warned_rows}

    # ---- VOLUME GUARD --------------------------------------------------------
    # Zero (or sub-floor) publishable rows almost always means a broken fetch,
    # not a real edit. Hold last-known-good rather than publish an empty feed.
    # Every NON-empty delete still passes — the sheet is the source of truth and
    # the pipeline never pushes back, so blocking a real big delete only buys a
    # window of lying. Only total wipeout is unrecoverable-by-next-sync.
    if len(published_rows) < vmin:
        reason = (f"only {len(published_rows)} row(s) would publish "
                  f"(floor is {vmin}) — almost certainly a broken fetch")
        prior_count = len(load_json(pub_path, []))
        feed = {
            "tab": tab, "status": "red", "published": False,
            "rows": prior_count, "changes": changes,
            "last_good_at": prev_good or "unknown",
            "last_changed_at": last_changed_at, "first_run": first_run,
            "messages": [f"BLOCKED (volume) — {reason} — serving last-known-good"],
            "counts": counts, "display_breakdown": display_breakdown,
            "open": {"warnings": warned_rows, "quarantined": len(quarantined)},
            "schema": {"human": schema.get("human", ""),
                       "required_headers": schema["required_headers"],
                       "rule_count": len(schema["rules"])},
            "links": links,
        }
        volume_guard = {"min": vmin, "live": live_rows, "published": len(published_rows),
                        "blocked": True, "reason": reason}
        detail = make_detail(tab, schema, "red", False, links, counts, volume_guard,
                             rstats, ran=True, dropped=quarantined, timeline=timeline,
                             block_reason=reason)
        return feed, detail

    # ---- write published (clean array the browser fetches) -------------------
    # right_now denormalizes display fields (quick_hits, has_page) from the
    # signage masters here, at publish — see enrich_right_now(). Scoped to the
    # one tab; every other feed writes its validated rows untouched.
    if tab == "right_now":
        published_rows = enrich_right_now(published_rows)
    write_json(pub_path, published_rows)

    # ---- status + messages ---------------------------------------------------
    messages = []
    for label, reason in quarantined:
        messages.append(f"quarantined: {label} ({reason})")
    if warned_rows:
        messages.append(f"{warned_rows} row(s) with warnings"
                        + (f": {warning_samples[0]}" if warning_samples else ""))

    status = "amber" if (quarantined or warned_rows) else "green"
    volume_guard = {"min": vmin, "live": live_rows, "published": len(published_rows),
                    "blocked": False, "reason": None}
    feed = {
        "tab": tab, "status": status, "published": True,
        "rows": len(published_rows), "changes": changes,
        "last_good_at": now_iso(), "last_changed_at": last_changed_at,
        "first_run": first_run, "messages": messages,
        "counts": counts, "display_breakdown": display_breakdown,
        "open": {"warnings": warned_rows, "quarantined": len(quarantined)},
        "schema": {"human": schema.get("human", ""),
                   "required_headers": schema["required_headers"],
                   "rule_count": len(schema["rules"])},
        "links": links,
    }
    detail = make_detail(tab, schema, status, True, links, counts, volume_guard,
                         rstats, ran=True, dropped=quarantined, timeline=timeline)
    return feed, detail


# ── reference data for cross-tab checks (e.g. events.series -> series.name) ────

def build_refs(needed_tabs):
    """{tab: {field: set(lowercased values)}} for cross-tab fk checks."""
    refs = {}
    for tab in needed_tabs:
        staging = load_json(os.path.join(STAGING, f"{tab}.json"))
        if not staging:
            continue
        idx = {}
        for row in staging["rows"]:
            for k, v in row.items():
                idx.setdefault(k, set()).add((v or "").strip().lower())
        refs[tab] = idx
    return refs


# ── run summary for the GitHub Actions run page ($GITHUB_STEP_SUMMARY) ─────────

def render_summary(health):
    dot = {"green": "🟢", "amber": "🟡", "red": "🔴"}
    lines = [f"## Sheet sync — {health['overall'].upper()}",
             f"_run {health['generated_at']}_", "",
             "| Feed | Status | Rows | Edits | Notes |",
             "|---|---|---:|---:|---|"]
    for f in health["feeds"]:
        ch = f["changes"]
        edits = "—" if f.get("first_run") else (edits_total(ch) or "no change")
        note = "; ".join(f["messages"]) or "ok"
        lines.append(f"| {f['tab']} | {dot[f['status']]} {f['status']} "
                     f"| {f['rows']} | {edits} | {note} |")
    return "\n".join(lines) + "\n"


def one_line_summary(health):
    parts = []
    for f in health["feeds"]:
        ch = f["changes"]
        if f["status"] == "red":
            parts.append(f"{f['tab']} BLOCKED")
        elif f.get("first_run"):
            parts.append(f"{f['tab']} initialized ({f['rows']})")
        elif edits_total(ch):
            bits = []
            if ch["added"]:   bits.append(f"+{ch['added']}")
            if ch["changed"]: bits.append(f"~{ch['changed']}")
            if ch["removed"]: bits.append(f"-{ch['removed']}")
            parts.append(f"{f['tab']} {''.join(b for b in bits)}")
    return ", ".join(parts) if parts else "no changes"


# ── persistence: the three layers + retention ─────────────────────────────────

def append_history(health):
    """_history.json: one compact point per feed per reportable run; pruned to
    ~6 months. Powers the summary page's sparklines, uptime%, churn at render."""
    history = load_json(os.path.join(PUBLISHED, "_history.json"), {})
    if not isinstance(history, dict):
        history = {}
    series = history.get("series", {})
    for f in health["feeds"]:
        ch = f["changes"]
        pts = series.get(f["tab"], [])
        pts.append({
            "at": health["generated_at"], "status": f["status"], "rows": f["rows"],
            "added": ch["added"], "changed": ch["changed"], "removed": ch["removed"],
        })
        series[f["tab"]] = prune_by_age(pts, HISTORY_DAYS)
    write_json(os.path.join(PUBLISHED, "_history.json"),
               {"generated_at": health["generated_at"], "series": series})


def write_details(details):
    """health/<tab>.json: deep per-rule detail, one file per feed."""
    for d in details:
        write_json(os.path.join(HEALTH_DIR, f"{d['tab']}.json"), d)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    prev_health = load_json(os.path.join(PUBLISHED, "_health.json"), {"feeds": []})

    # FK checks need other tabs' values (e.g. events.series -> series.name).
    refs = build_refs(["series"])

    feeds, details = [], []
    for tab in PUBLISH_TABS:
        feed, detail = process_tab(tab, refs, prev_health)
        feeds.append(feed)
        details.append(detail)

    overall = ("blocked" if any(f["status"] == "red" for f in feeds)
               else "warn" if any(f["status"] == "amber" for f in feeds)
               else "ok")
    health = {"generated_at": now_iso(), "overall": overall, "feeds": feeds}

    # No-op short-circuit: if nothing meaningful changed since last run — same
    # status + row count + messages per feed AND zero edits AND not a first run —
    # don't rewrite the health files. Identical files = no git diff = no commit,
    # so a commit always means a real change. (Liveness — "is the sync running?"
    # — comes from the Actions tab's run history, not a committed clock.)
    def sig(feeds_):
        return [(f["tab"], f["status"], f["rows"], tuple(f["messages"])) for f in feeds_]
    reportable = (
        sig(feeds) != sig(prev_health.get("feeds", []))
        or any(edits_total(f["changes"]) for f in feeds)
        or any(f.get("first_run") for f in feeds)
    )

    if reportable:
        write_json(os.path.join(PUBLISHED, "_health.json"), health)
        runlog = load_json(os.path.join(PUBLISHED, "_runlog.json"), {"runs": []})
        runlog["runs"].insert(0, {
            "at": health["generated_at"],
            "overall": overall,
            "summary": one_line_summary(health),
        })
        runlog["runs"] = runlog["runs"][:RUNLOG_CAP]
        write_json(os.path.join(PUBLISHED, "_runlog.json"), runlog)
        append_history(health)
        write_details(details)
        summary_md = render_summary(health)
    else:
        summary_md = "## Sheet sync — no changes\n_nothing to publish; last-known-good unchanged._\n"

    # GitHub Actions run-page summary (Surface B)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(summary_md)
    else:
        print(summary_md)

    # exit non-zero on a block so the Action goes red and emails Randy (Surface C)
    return 1 if overall == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
