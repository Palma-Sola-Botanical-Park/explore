#!/usr/bin/env python3
"""
photo_workbench.py  —  PSBP Photo Workbench (YELLOW / review mode)
==================================================================
The review front-end for the PSBP photo pipeline. This is the "yellow" tool:
you scan iNaturalist for a species, look at thumbnails pulled straight from
iNat's CDN (nothing downloads during review), and decide each photo:

    PROMOTE  -> if this is the species' first photo (the hero), the web-res
                file is downloaded into the repo + a row is added to
                photo_credits.json. Otherwise it's a VIRTUAL gallery promote:
                a metadata row is written (photographer, license, CDN url)
                but NO file is downloaded — the site serves gallery images
                from iNat's CDN at runtime. Zero repo weight for galleries.
    SKIP     -> "fine, not needed" — recorded, hidden from future scans
    BLOCK    -> "never show me again" — recorded, hidden from future scans

Every decision is written to photo_workbench.json (NEW). That file is the
"seen it" memory: the next scan hides anything you've already ruled on, so the
backlog only ever shows you genuinely new photos. Both JSON files live in the
repo, so your decisions travel between desktops via git.

WHAT THIS TOOL IS NOT (yet)
    This is review + promote only. Editing promoted photos — re-crowning a
    hero, tagging roles, nudging the focus point — is the GREEN job and still
    lives in photo_review.py. Eventually the two merge into one app with a
    mode switch; for now they're siblings that both respect the single-writer
    rule on photo_credits.json (only PROMOTE here ever appends to it).

THE TWO HARD RULES (from the roadmap, enforced structurally here)
    1. Only heroes touch the repo. Gallery photos are virtual: metadata in
       photo_credits.json, images served from iNat's CDN at runtime. A PROMOTE
       for a species that already has a hero writes a registry row but downloads
       nothing. This keeps repo size proportional to species count (~1 MB each),
       not total photo count.
    2. Web-res only. Even hero downloads use the "large" rendering (~1024px).
       Originals stay on iNat under the same CC license, reachable via the
       credit link. The park is not a high-res distribution point.

RUN
    python3 photo_workbench.py
    then open http://localhost:8001

CONFIG: the only things you might change are the three constants below
(REPO, WORKSPACE, INAT_PROJECT_ID). Everything else is derived from REPO.
"""

import http.server
import json
import os
import sys
import time
import datetime
import urllib.parse
import urllib.request
import urllib.error

# ===========================================================================
# CONFIG  —  the only lines you should ever need to touch
# ===========================================================================
# REPO path now comes from psbp_common.py — change it THERE.
from psbp_common import (
    REPO as _REPO,
    PLANT_SIGNAGE_JSON, WILDLIFE_SIGNAGE_JSON,
    PHOTO_CREDITS_JSON, PHOTO_WORKBENCH_JSON as WORKBENCH_JSON,
    PHOTOS_DIR,
    load_json, write_json_atomic,
    display_name, build_credit_line, CC_LICENSES,
)

REPO = str(_REPO)                           # workbench uses string paths throughout
PLANT_JSON    = str(PLANT_SIGNAGE_JSON)      # local alias — signage, not search index
WILDLIFE_JSON = str(WILDLIFE_SIGNAGE_JSON)   # local alias — signage, not search index
SOURCES       = str(PLANT_SIGNAGE_JSON.parent)

WORKSPACE = os.path.expanduser("~/Documents/PSBP_photo_workspace")

# Your iNat project's slug or numeric id. Find it in the project URL:
#   https://www.inaturalist.org/projects/<THIS-PART>
# Coverage (the top half of the dashboard) works WITHOUT this. Scanning for
# new photos needs it — leave blank and the app will tell you to set it.
# Can also be supplied via the INAT_PROJECT_ID environment variable.
INAT_PROJECT_ID = os.environ.get("INAT_PROJECT_ID", "")

PORT = 8001

CACHE_DIR = os.path.join(WORKSPACE, "cache")

API_DELAY      = 1.0   # between iNat API pages
DOWNLOAD_DELAY = 0.3   # between web-res downloads

# Threshold for the "thin" bucket: a species with a hero but this many or
# fewer promoted photos is flagged as wanting more gallery shots.
THIN_AT = 2


# ===========================================================================
# SMALL HELPERS
# ===========================================================================

def today():
    return datetime.date.today().isoformat()


# ===========================================================================
# WORKBENCH + CREDITS
# ===========================================================================

def load_workbench():
    wb = load_json(WORKBENCH_JSON, None)
    if wb is None:
        wb = {"meta": {"cursors": {}}, "decisions": {}}
    wb.setdefault("meta", {}).setdefault("cursors", {})
    wb.setdefault("decisions", {})
    return wb


def load_credits():
    pc = load_json(PHOTO_CREDITS_JSON, None)
    if pc is None:
        pc = {"meta": {"photo_count": 0}, "photos": []}
    pc.setdefault("photos", [])
    pc.setdefault("meta", {})
    return pc


def decided_photo_ids(workbench, credits):
    """A photo is 'decided' if it has a workbench verdict OR is already a
    promoted row in the registry (covers photos promoted by older scripts)."""
    ids = set(str(k) for k in workbench["decisions"].keys())
    for p in credits["photos"]:
        if p.get("photo_id"):
            ids.add(str(p["photo_id"]))
    return ids


def credits_coverage(credits):
    """psbp_id -> {'promoted': n, 'has_hero': bool}

    Heroes must have a real file on disk to count. Virtual gallery photos
    (served from iNat CDN) count as promoted without a local file."""
    cov = {}
    for p in credits["photos"]:
        pid = p.get("psbp_id")
        if not pid:
            continue
        is_virtual = p.get("virtual", False)
        if not is_virtual:
            fn = p.get("filename") or ""
            if not (fn and os.path.isfile(os.path.join(PHOTOS_DIR, pid, fn))):
                continue
        c = cov.setdefault(pid, {"promoted": 0, "has_hero": False})
        c["promoted"] += 1
        if p.get("hero"):
            c["has_hero"] = True
    return cov


# ===========================================================================
# SIGNAGE
# ===========================================================================

def signage_path(corpus):
    return PLANT_JSON if corpus == "plants" else WILDLIFE_JSON


def credit_type(corpus):
    return "Plant" if corpus == "plants" else "Wildlife"


def sci_name_of(sp):
    return sp.get("botanical_name") or sp.get("scientific_name") or ""


def load_species(corpus):
    data = load_json(signage_path(corpus), {"species": []})
    return data.get("species", [])


def species_by_id(corpus, psbp_id):
    for sp in load_species(corpus):
        if sp.get("id") == psbp_id:
            return sp
    return None


# ===========================================================================
# iNATURALIST
# ===========================================================================

def inat_get(url):
    headers = {
        "Accept": "application/json",
        "User-Agent": "PSBP-PhotoWorkbench/1.0 (palmasolabp.org)",
    }
    token = os.environ.get("INAT_TOKEN")
    if token:
        headers["Authorization"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:160]}")
        return None
    except Exception as e:
        print(f"    request failed: {e}")
        return None


def inat_observations(taxon_id):
    """All park observations for one taxon (project-scoped), paginated."""
    out, page = [], 1
    while True:
        url = ("https://api.inaturalist.org/v1/observations"
               f"?project_id={urllib.parse.quote(str(INAT_PROJECT_ID))}"
               f"&taxon_id={taxon_id}&per_page=200&page={page}"
               "&order=desc&order_by=created_at")
        data = inat_get(url)
        if not data:
            break
        results = data.get("results", [])
        out.extend(results)
        if len(results) < 200:
            break
        page += 1
        if page > 10:
            break
        time.sleep(API_DELAY)
    return out


def cc_photos_from_observations(obs_list):
    """Flatten observations -> CC photo records ready for the triage grid.
    Non-CC photos are counted but not shown (the park only uses CC)."""
    cc, non_cc = [], 0
    for obs in obs_list:
        user = obs.get("user") or {}
        login = user.get("login") or "unknown"
        name = display_name(login, user.get("name"))
        observed_on = obs.get("observed_on") or (obs.get("time_observed_at") or "")[:10] or None
        shared_on = (obs.get("created_at") or "")[:10] or None
        obs_id = str(obs.get("id", ""))
        src = f"https://www.inaturalist.org/observations/{obs_id}"
        for p in obs.get("photos", []):
            lic = (p.get("license_code") or "")
            if lic.lower() not in CC_LICENSES:
                non_cc += 1
                continue
            base_url = p.get("url", "") or ""
            cc.append({
                "photo_id":          str(p.get("id", "")),
                "obs_id":            obs_id,
                "thumb_url":         base_url.replace("/square.", "/medium."),
                "large_url":         base_url.replace("/square.", "/large."),
                "license":           lic,
                "photographer":      login,
                "photographer_name": name,
                "observed_on":       observed_on,
                "shared_on":         shared_on,
                "source_url":        src,
            })
    return cc, non_cc


# ===========================================================================
# SCAN CACHE  (workspace, outside the repo — throwaway, re-fetchable)
# ===========================================================================

def cache_path(corpus, psbp_id):
    return os.path.join(CACHE_DIR, corpus, f"{psbp_id}.json")


def read_cache(corpus, psbp_id):
    return load_json(cache_path(corpus, psbp_id), None)


def write_cache(corpus, psbp_id, cc, non_cc):
    payload = {
        "psbp_id": psbp_id,
        "scanned_at": datetime.datetime.utcnow().isoformat() + "Z",
        "cc": cc,
        "cc_count": len(cc),
        "non_cc_count": non_cc,
    }
    write_json_atomic(cache_path(corpus, psbp_id), payload)
    return payload


def scan_species(corpus, sp):
    """Hit iNat for one species, refresh its cache. Returns the cache payload
    or an error dict."""
    if not INAT_PROJECT_ID:
        return {"error": "INAT_PROJECT_ID is not set — see the config block at "
                         "the top of photo_workbench.py."}
    taxon_id = sp.get("inat_taxon_id")
    if not taxon_id:
        return {"error": f"{sp.get('id')} has no inat_taxon_id in the signage JSON."}
    obs = inat_observations(taxon_id)
    cc, non_cc = cc_photos_from_observations(obs)
    return write_cache(corpus, sp["id"], cc, non_cc)


# ===========================================================================
# STATE BUCKETS
# ===========================================================================

def compute_state(status, has_hero, promoted, available_cc, to_review):
    """available_cc / to_review are None until a species has been scanned."""
    if not has_hero:
        if available_cc == 0:
            return "starved"          # scanned, no CC photos exist — needs a shooter
        if status == "html":
            return "bare"             # live page with no hero — the emergency
        return "needs_hero"           # not public yet, still wants a hero
    if to_review and to_review > 0:
        return "backlog"              # covered, but new photos are waiting
    if promoted <= THIN_AT:
        return "thin"                 # has a hero, skinny gallery
    return "caught_up"


# ===========================================================================
# DASHBOARD DATA
# ===========================================================================

def build_dashboard(corpus):
    species   = load_species(corpus)
    credits   = load_credits()
    workbench = load_workbench()
    cov       = credits_coverage(credits)
    decided   = decided_photo_ids(workbench, credits)

    rows = []
    waiting_total = 0
    for sp in species:
        psbp_id = sp.get("id")
        if not psbp_id:
            continue
        status      = sp.get("status", "")
        c           = cov.get(psbp_id, {"promoted": 0, "has_hero": False})
        promoted    = c["promoted"]
        has_hero    = c["has_hero"]

        cache = read_cache(corpus, psbp_id)
        if cache:
            cc_ids       = [p["photo_id"] for p in cache["cc"]]
            available_cc = len(cc_ids)
            to_review    = len([pid for pid in cc_ids if pid not in decided])
            non_cc       = cache.get("non_cc_count", 0)
            scanned_at   = cache.get("scanned_at")
        else:
            available_cc = None
            to_review    = None
            non_cc       = None
            scanned_at   = None

        if to_review:
            waiting_total += to_review

        rows.append({
            "id":           psbp_id,
            "common_name":  sp.get("common_name", ""),
            "status":       status,
            "promoted":     promoted,
            "has_hero":     has_hero,
            "available_cc": available_cc,
            "to_review":    to_review,
            "non_cc":       non_cc,
            "scanned":      cache is not None,
            "scanned_at":   scanned_at,
            "state":        compute_state(status, has_hero, promoted, available_cc, to_review),
        })

    metrics = {
        "total":      len(rows),
        "live_pages": sum(1 for r in rows if r["status"] == "html"),
        "have_hero":  sum(1 for r in rows if r["has_hero"]),
        "need_photos": sum(1 for r in rows if not r["has_hero"]),
        "waiting":    waiting_total,
        "project_set": bool(INAT_PROJECT_ID),
    }
    return {"corpus": corpus, "metrics": metrics, "species": rows}


def build_species_view(corpus, psbp_id, show_decided=False):
    sp = species_by_id(corpus, psbp_id)
    if not sp:
        return {"error": f"{psbp_id} not found in {corpus} signage."}
    credits   = load_credits()
    workbench = load_workbench()
    cov       = credits_coverage(credits).get(psbp_id, {"promoted": 0, "has_hero": False})
    decided   = decided_photo_ids(workbench, credits)

    cache = read_cache(corpus, psbp_id)
    photos = []
    if cache:
        for p in cache["cc"]:
            is_decided = p["photo_id"] in decided
            if is_decided and not show_decided:
                continue
            verdict = workbench["decisions"].get(p["photo_id"], {}).get("decision")
            if not verdict and is_decided:
                verdict = "promoted"   # already in registry, no workbench row
            item = dict(p)
            item["decision"] = verdict
            photos.append(item)

    return {
        "id":            psbp_id,
        "common_name":   sp.get("common_name", ""),
        "scientific_name": sci_name_of(sp),
        "status":        sp.get("status", ""),
        "type":          credit_type(corpus),
        "promoted":      cov["promoted"],
        "has_hero":      cov["has_hero"],
        "scanned":       cache is not None,
        "scanned_at":    cache.get("scanned_at") if cache else None,
        "non_cc":        cache.get("non_cc_count", 0) if cache else None,
        "photos":        photos,
    }


# ===========================================================================
# DECISIONS
# ===========================================================================

def download_web_res(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": "PSBP-PhotoWorkbench/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"    download failed: {e}")
        return False


def apply_decision(payload):
    """payload carries everything needed so we never re-hit the API here."""
    decision = payload.get("decision")
    pid      = str(payload.get("photo_id", ""))
    psbp_id  = payload.get("psbp_id", "")
    corpus   = payload.get("corpus", "plants")
    promoted_as_hero = False
    if decision not in ("promoted", "skip", "block") or not pid or not psbp_id:
        return {"ok": False, "error": "bad decision payload"}

    # PROMOTE: heroes download a file into the repo; gallery photos are virtual
    # (metadata row only, served from iNat's CDN at runtime — zero repo weight).
    if decision == "promoted":
        credits = load_credits()
        has_hero = any(p.get("psbp_id") == psbp_id and p.get("hero")
                       for p in credits["photos"])
        # don't double-add if somehow already present
        if any(str(p.get("photo_id")) == pid for p in credits["photos"]):
            return {"ok": False, "error": "already in registry"}

        is_hero = not has_hero
        promoted_as_hero = is_hero
        name = payload.get("photographer_name") or display_name(
            payload.get("photographer"), "")
        lic = payload.get("license", "")

        # Heroes get a real file in the repo; gallery stays virtual.
        if is_hero:
            filename = f"{pid}.jpg"
            dest = os.path.join(PHOTOS_DIR, psbp_id, filename)
            if not download_web_res(payload.get("large_url", ""), dest):
                return {"ok": False, "error": "web-res download failed — nothing recorded"}
            time.sleep(DOWNLOAD_DELAY)
        else:
            filename = None   # no file on disk — served from iNat CDN

        entry = {
            "psbp_id":           psbp_id,
            "type":              payload.get("type", credit_type(corpus)),
            "common_name":       payload.get("common_name", ""),
            "scientific_name":   payload.get("scientific_name", ""),
            "role":              ["whole", "gallery"] if is_hero else ["gallery"],
            "primary_for":       ["whole"] if is_hero else [],
            "hero":              is_hero,
            "focus":             "50% 50%" if is_hero else None,
            "tags":              [],
            "photographer":      payload.get("photographer", ""),
            "photographer_name": name,
            "license":           lic.upper() if lic else "",
            "publish_ok":        True,
            "status":            "OK",
            "credit_line":       build_credit_line(name, lic),
            "observed_on":       payload.get("observed_on"),
            "shared_on":         payload.get("shared_on"),
            "photo_url":         payload.get("large_url", ""),
            "source_url":        payload.get("source_url", ""),
            "observation_id":    payload.get("obs_id", ""),
            "photo_id":          pid,
            "filename":          filename,
            "used_by":           [],
            "virtual":           not is_hero,
        }
        credits["photos"].append(entry)
        credits.setdefault("meta", {})["photo_count"] = len(credits["photos"])
        write_json_atomic(PHOTO_CREDITS_JSON, credits)

    # Every decision (including promote) is recorded in the workbench.
    wb = load_workbench()
    wb["decisions"][pid] = {
        "decision":     decision,
        "reviewed_on":  today(),
        "psbp_id":      psbp_id,
        "obs_id":       payload.get("obs_id", ""),
        "photographer": payload.get("photographer", ""),
        "license":      payload.get("license", ""),
        "note":         "",
    }
    write_json_atomic(WORKBENCH_JSON, wb)

    # Hand back fresh coverage so the UI can update without a full reload.
    credits = load_credits()
    cov = credits_coverage(credits).get(psbp_id, {"promoted": 0, "has_hero": False})
    return {"ok": True, "decision": decision, "psbp_id": psbp_id,
            "promoted": cov["promoted"], "has_hero": cov["has_hero"],
            "is_hero": promoted_as_hero}


# ===========================================================================
# HTML
# ===========================================================================

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PSBP Photo Workbench</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a1a; color: #e0e0e0; }

.header { background: #4a3a0f; padding: 12px 20px; display: flex; align-items: center; gap: 14px; position: sticky; top: 0; z-index: 50; border-bottom: 2px solid #c5922a; }
.header h1 { font-size: 17px; color: #e8c869; white-space: nowrap; }
.flag { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #1a1a1a; background: #c5922a; padding: 3px 8px; border-radius: 4px; font-weight: 700; }
.toggle { display: inline-flex; border: 1px solid #6a5414; border-radius: 6px; overflow: hidden; }
.toggle button { padding: 6px 14px; border: none; background: transparent; color: #d6c389; cursor: pointer; font-size: 14px; }
.toggle button.on { background: #c5922a; color: #1a1a1a; font-weight: 600; }
.header .right { margin-left: auto; display: flex; gap: 10px; align-items: center; }
button.act { padding: 7px 14px; border-radius: 6px; border: 1px solid #4a4a4a; background: #2a2a2a; color: #e0e0e0; cursor: pointer; font-size: 13px; }
button.act:hover { background: #333; }
.status-msg { font-size: 12px; color: #aaa; }

.wrap { padding: 20px; max-width: 1100px; margin: 0 auto; }

.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 22px; }
.metric { background: #242424; border-radius: 8px; padding: 14px 16px; }
.metric .label { font-size: 12px; color: #999; }
.metric .num { font-size: 26px; font-weight: 600; margin-top: 2px; }
.metric .num.warn { color: #e07a5f; }

h2 { font-size: 15px; color: #d6c389; margin: 0 0 10px; font-weight: 600; }

.srow { display: flex; align-items: center; gap: 12px; background: #242424; border: 1px solid #3a2f10; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; }
.srow .warn-i { color: #e24b4a; font-size: 18px; }
.srow .body { flex: 1; min-width: 0; }
.srow .name { font-size: 14px; }
.srow .meta { font-size: 12px; color: #999; margin-top: 1px; }
button.review { padding: 6px 16px; border-radius: 6px; border: none; background: #c5922a; color: #1a1a1a; font-weight: 600; cursor: pointer; font-size: 13px; }
button.review:hover { background: #e0a93a; }

.filters { display: flex; gap: 8px; flex-wrap: wrap; margin: 18px 0 10px; }
.chip { font-size: 13px; padding: 4px 12px; border: 1px solid #3a3a3a; border-radius: 6px; background: transparent; color: #aaa; cursor: pointer; }
.chip.on { background: #2a2a2a; color: #e0e0e0; border-color: #555; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 500; color: #888; padding: 8px 6px; border-bottom: 1px solid #444; }
td { padding: 9px 6px; border-bottom: 1px solid #2c2c2c; vertical-align: middle; }
tr.clickable:hover td { background: #232323; cursor: pointer; }
.pid { font-size: 11px; color: #777; }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #333; color: #ccc; }
.star { color: #c5922a; }
.dash { color: #666; }
.badge { font-size: 11px; padding: 3px 9px; border-radius: 10px; white-space: nowrap; }
.b-bare      { background: #3a1414; color: #f3a0a0; }
.b-thin      { background: #3a2f10; color: #e8c869; }
.b-backlog   { background: #122a3a; color: #8fc4e8; }
.b-caught_up { background: #12302a; color: #6fcaa8; }
.b-starved   { background: #251f3a; color: #b3aae0; }
.b-needs_hero{ background: #2a2a2a; color: #bbb; }
.muted { color: #777; }

/* species view */
.sv-head { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; flex-wrap: wrap; }
.sv-head .big { font-size: 19px; color: #e8c869; }
.sv-head .sci { font-style: italic; color: #999; font-size: 14px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.pcard { background: #242424; border-radius: 10px; overflow: hidden; border: 2px solid transparent; }
.pcard.decided { opacity: .45; }
.pcard.promoted { border-color: #2d6a35; }
.pcard.block { border-color: #8b2020; }
.pcard .imgwrap { position: relative; width: 100%; padding-top: 72%; background: #111; }
.pcard .imgwrap img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.pcard .pbody { padding: 10px 12px; }
.pcard .credit { font-size: 12px; color: #4a9e56; font-weight: 600; }
.pcard .sub { font-size: 11px; color: #999; margin-top: 2px; }
.lic { display: inline-block; font-size: 10px; color: #bbb; background: #333; padding: 1px 6px; border-radius: 8px; margin-top: 6px; }
.pacts { display: flex; gap: 6px; margin-top: 10px; }
.pacts button { flex: 1; padding: 6px 0; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; }
.b-promote { background: #2d6a35; color: #fff; }
.b-promote:hover { background: #38843f; }
.b-skip { background: #3a3a3a; color: #ddd; }
.b-skip:hover { background: #484848; }
.b-block { background: #6b1d1d; color: #fff; }
.b-block:hover { background: #8b2020; }
.verdict { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; padding: 6px 0; text-align: center; color: #999; }
.empty { text-align: center; color: #777; padding: 60px 20px; font-size: 15px; }
.hide { display: none; }
.banner { background: #3a2f10; color: #e8c869; border: 1px solid #6a5414; border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 16px; }
</style>
</head>
<body>

<div class="header">
  <h1>PSBP Photo Workbench</h1>
  <span class="flag">Review mode</span>
  <div class="toggle" id="corpusToggle">
    <button data-corpus="plants" class="on" onclick="setCorpus('plants')">Plants</button>
    <button data-corpus="wildlife" onclick="setCorpus('wildlife')">Wildlife</button>
  </div>
  <div class="right">
    <button class="act" id="scanAllBtn" onclick="scanCorpus()">Scan corpus for new pics</button>
    <span class="status-msg" id="statusMsg"></span>
  </div>
</div>

<div class="wrap">
  <!-- DASHBOARD -->
  <div id="dashboard">
    <div id="projectBanner" class="banner hide">
      INAT_PROJECT_ID isn't set, so scanning is off. Coverage below still works.
      Set it in the config block at the top of photo_workbench.py to enable scans.
    </div>
    <div class="cards" id="cards"></div>
    <h2>Start here &mdash; live pages with no hero</h2>
    <div id="startHere"></div>
    <div class="filters" id="filters">
      <button class="chip on" data-f="all" onclick="setFilter('all')">All species</button>
      <button class="chip" data-f="live" onclick="setFilter('live')">Live pages</button>
      <button class="chip" data-f="nohero" onclick="setFilter('nohero')">No hero</button>
      <button class="chip" data-f="backlog" onclick="setFilter('backlog')">Has backlog</button>
      <button class="chip" data-f="starved" onclick="setFilter('starved')">Starved</button>
    </div>
    <table>
      <thead><tr>
        <th style="width:30%">Species</th><th style="width:12%">Status</th>
        <th style="width:8%">Hero</th><th style="width:9%">Pics</th>
        <th style="width:13%">To review</th><th style="width:28%">State</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>

  <!-- SPECIES TRIAGE -->
  <div id="speciesView" class="hide">
    <div class="sv-head">
      <button class="act" onclick="backToDash()">&larr; Back</button>
      <span class="big" id="svName"></span>
      <span class="sci" id="svSci"></span>
      <span id="svCoverage" class="muted" style="font-size:13px"></span>
      <div class="right" style="margin-left:auto; display:flex; gap:10px; align-items:center">
        <label style="font-size:12px;color:#aaa"><input type="checkbox" id="showDecided" onchange="reloadSpecies()"> show decided</label>
        <button class="act" onclick="scanThis()">Scan this species</button>
      </div>
    </div>
    <div id="svBody"></div>
  </div>
</div>

<script>
let corpus = 'plants';
let filter = 'all';
let dash = null;
let currentSpecies = null;

const STATE_LABEL = {
  bare: ['Bare &amp; public', 'b-bare'],
  thin: ['Thin', 'b-thin'],
  backlog: ['Backlog', 'b-backlog'],
  caught_up: ['Caught up', 'b-caught_up'],
  starved: ['Starved &mdash; needs a shooter', 'b-starved'],
  needs_hero: ['Needs a hero', 'b-needs_hero'],
};

function setStatus(msg) { document.getElementById('statusMsg').textContent = msg || ''; }

function setCorpus(c) {
  corpus = c;
  document.querySelectorAll('#corpusToggle button').forEach(b =>
    b.classList.toggle('on', b.dataset.corpus === c));
  loadDashboard();
}

function setFilter(f) {
  filter = f;
  document.querySelectorAll('#filters .chip').forEach(b =>
    b.classList.toggle('on', b.dataset.f === f));
  renderRows();
}

async function loadDashboard() {
  setStatus('loading...');
  const r = await fetch('/api/dashboard?corpus=' + corpus);
  dash = await r.json();
  renderDashboard();
  setStatus('');
}

function renderDashboard() {
  const m = dash.metrics;
  document.getElementById('projectBanner').classList.toggle('hide', m.project_set);
  document.getElementById('cards').innerHTML = `
    <div class="metric"><div class="label">Live pages</div><div class="num">${m.live_pages}</div></div>
    <div class="metric"><div class="label">Have a hero</div><div class="num">${m.have_hero}</div></div>
    <div class="metric"><div class="label">Need photos</div><div class="num warn">${m.need_photos}</div></div>
    <div class="metric"><div class="label">Waiting to review</div><div class="num">${m.waiting.toLocaleString()}</div></div>`;

  const bare = dash.species
    .filter(s => s.state === 'bare')
    .sort((a, b) => (b.to_review || 0) - (a.to_review || 0));
  const sh = document.getElementById('startHere');
  if (!bare.length) {
    sh.innerHTML = `<div class="srow"><div class="body"><div class="name">All live pages have a hero.</div><div class="meta">Nothing bare in ${corpus}. Top up the thin ones or switch corpus.</div></div></div>`;
  } else {
    sh.innerHTML = bare.slice(0, 6).map(s => {
      const wait = s.to_review == null ? 'not scanned yet' : `${s.to_review} photos waiting`;
      return `<div class="srow"><span class="warn-i">&#9888;</span>
        <div class="body"><div class="name">${s.common_name}</div>
        <div class="meta">${s.id} &middot; ${wait} &middot; no hero yet</div></div>
        <button class="review" onclick="openSpecies('${s.id}')">Review</button></div>`;
    }).join('');
  }
  renderRows();
}

function passFilter(s) {
  if (filter === 'all') return true;
  if (filter === 'live') return s.status === 'html';
  if (filter === 'nohero') return !s.has_hero;
  if (filter === 'backlog') return (s.to_review || 0) > 0;
  if (filter === 'starved') return s.state === 'starved';
  return true;
}

const STATE_ORDER = { bare: 0, starved: 1, backlog: 2, needs_hero: 3, thin: 4, caught_up: 5 };

function renderRows() {
  const rows = dash.species.filter(passFilter)
    .sort((a, b) => (STATE_ORDER[a.state] - STATE_ORDER[b.state]) || a.id.localeCompare(b.id));
  document.getElementById('rows').innerHTML = rows.map(s => {
    const [label, cls] = STATE_LABEL[s.state] || ['&mdash;', ''];
    const hero = s.has_hero ? '<span class="star">&#9733;</span>' : '<span class="dash">&mdash;</span>';
    const statusPill = `<span class="pill">${s.status === 'html' ? 'live' : (s.status || '&mdash;')}</span>`;
    const tr = s.to_review == null ? '<span class="muted">scan</span>' : s.to_review;
    return `<tr class="clickable" onclick="openSpecies('${s.id}')">
      <td><div>${s.common_name}</div><div class="pid">${s.id}</div></td>
      <td>${statusPill}</td><td>${hero}</td><td>${s.promoted}</td>
      <td>${tr}</td><td><span class="badge ${cls}">${label}</span></td></tr>`;
  }).join('');
}

async function scanCorpus() {
  if (!dash.metrics.project_set) { setStatus('set INAT_PROJECT_ID first'); return; }
  const btn = document.getElementById('scanAllBtn');
  btn.disabled = true;
  setStatus('scanning corpus &mdash; this hits iNat once per species, give it a minute...');
  const r = await fetch('/api/scan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ corpus })
  });
  const res = await r.json();
  btn.disabled = false;
  setStatus(res.ok ? `scanned ${res.scanned} species` : ('error: ' + res.error));
  loadDashboard();
}

async function openSpecies(id) {
  document.getElementById('dashboard').classList.add('hide');
  document.getElementById('speciesView').classList.remove('hide');
  currentSpecies = id;
  await reloadSpecies();
}

function backToDash() {
  document.getElementById('speciesView').classList.add('hide');
  document.getElementById('dashboard').classList.remove('hide');
  loadDashboard();
}

async function reloadSpecies() {
  const showDecided = document.getElementById('showDecided').checked;
  setStatus('loading species...');
  const r = await fetch(`/api/species?corpus=${corpus}&id=${currentSpecies}&decided=${showDecided ? 1 : 0}`);
  const sv = await r.json();
  setStatus('');
  renderSpecies(sv);
}

function renderSpecies(sv) {
  document.getElementById('svName').textContent = `${sv.id} \u2014 ${sv.common_name}`;
  document.getElementById('svSci').textContent = sv.scientific_name || '';
  const heroTxt = sv.has_hero ? 'has hero' : 'NO HERO';
  document.getElementById('svCoverage').innerHTML =
    `&middot; ${sv.promoted} promoted &middot; ${heroTxt}` +
    (sv.non_cc ? ` &middot; ${sv.non_cc} non-CC hidden` : '');

  const body = document.getElementById('svBody');
  if (!sv.scanned) {
    body.innerHTML = `<div class="empty">Not scanned yet. Hit &ldquo;Scan this species&rdquo; to pull its iNat photos.</div>`;
    return;
  }
  if (!sv.photos.length) {
    body.innerHTML = `<div class="empty">Nothing left to review here. ${document.getElementById('showDecided').checked ? '' : 'Tick &ldquo;show decided&rdquo; to see past calls.'}</div>`;
    return;
  }
  body.innerHTML = '<div class="grid">' + sv.photos.map(p => cardHtml(sv, p)).join('') + '</div>';
}

function cardHtml(sv, p) {
  const decidedCls = p.decision ? ('decided ' + p.decision) : '';
  const acts = p.decision
    ? `<div class="verdict">${p.decision === 'promoted' ? 'promoted &#9733;' : p.decision}</div>`
    : `<div class="pacts">
         <button class="b-promote" onclick='decide(${js(sv)}, ${js(p)}, "promoted", this)'>${sv.has_hero ? 'Promote (gallery)' : 'Promote as hero &#9733;'}</button>
         <button class="b-skip" onclick='decide(${js(sv)}, ${js(p)}, "skip", this)'>Skip</button>
         <button class="b-block" onclick='decide(${js(sv)}, ${js(p)}, "block", this)'>Block</button>
       </div>`;
  const date = p.observed_on ? ('shot ' + p.observed_on) : '';
  return `<div class="pcard ${decidedCls}" id="card-${p.photo_id}">
    <div class="imgwrap"><a href="${p.source_url}" target="_blank" rel="noopener">
      <img src="${p.thumb_url}" loading="lazy"></a></div>
    <div class="pbody">
      <div class="credit">${p.photographer_name}</div>
      <div class="sub">${date}</div>
      <span class="lic">${(p.license || '').toUpperCase()}</span>
      ${acts}
    </div></div>`;
}

function js(o) { return JSON.stringify(o).replace(/'/g, "&#39;"); }

async function decide(sv, p, decision, btn) {
  const card = document.getElementById('card-' + p.photo_id);
  card.querySelector('.pacts').innerHTML = '<div class="verdict">saving...</div>';
  const payload = {
    corpus, decision,
    photo_id: p.photo_id, psbp_id: sv.id, obs_id: p.obs_id,
    large_url: p.large_url, source_url: p.source_url,
    photographer: p.photographer, photographer_name: p.photographer_name,
    license: p.license, observed_on: p.observed_on, shared_on: p.shared_on,
    common_name: sv.common_name, scientific_name: sv.scientific_name, type: sv.type,
  };
  const r = await fetch('/api/decide', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const res = await r.json();
  if (!res.ok) {
    card.querySelector('.verdict').textContent = 'error: ' + res.error;
    return;
  }
  card.classList.add('decided', decision);
  card.querySelector('.pbody').lastElementChild.outerHTML =
    `<div class="verdict">${decision === 'promoted' ? (res.is_hero ? 'hero &#9733;' : 'gallery') : decision}</div>`;
  const heroTxt = res.has_hero ? 'has hero' : 'NO HERO';
  document.getElementById('svCoverage').innerHTML =
    `&middot; ${res.promoted} promoted &middot; ${heroTxt}` + (sv.non_cc ? ` &middot; ${sv.non_cc} non-CC hidden` : '');
  // After a hero promote, flip remaining undecided buttons to gallery mode.
  if (res.is_hero) {
    sv.has_hero = true;
    document.querySelectorAll('.pcard:not(.decided) .b-promote').forEach(btn => {
      btn.textContent = 'Promote (gallery)';
    });
  }
  setStatus(`${decision}: ${p.photo_id}`);
}

async function scanThis() {
  setStatus('scanning ' + currentSpecies + '...');
  const r = await fetch('/api/scan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ corpus, id: currentSpecies })
  });
  const res = await r.json();
  setStatus(res.ok ? `found ${res.cc_count} CC photos` : ('error: ' + res.error));
  reloadSpecies();
}

loadDashboard();
</script>
</body>
</html>
"""


# ===========================================================================
# HTTP SERVER
# ===========================================================================

class Handler(http.server.SimpleHTTPRequestHandler):

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            return

        if path == "/api/dashboard":
            corpus = (q.get("corpus", ["plants"])[0])
            self._json(build_dashboard(corpus))
            return

        if path == "/api/species":
            corpus = q.get("corpus", ["plants"])[0]
            psbp_id = q.get("id", [""])[0]
            show_decided = q.get("decided", ["0"])[0] == "1"
            self._json(build_species_view(corpus, psbp_id, show_decided))
            return

        if path.startswith("/photos/"):
            fp = os.path.join(REPO, path.lstrip("/"))
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    self._send(200, "image/jpeg", f.read())
                return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(body)
        except Exception:
            req = {}

        if parsed.path == "/api/scan":
            corpus = req.get("corpus", "plants")
            if req.get("id"):
                sp = species_by_id(corpus, req["id"])
                if not sp:
                    self._json({"ok": False, "error": "species not found"})
                    return
                res = scan_species(corpus, sp)
                if "error" in res:
                    self._json({"ok": False, "error": res["error"]})
                else:
                    self._json({"ok": True, "cc_count": res["cc_count"]})
                return
            # corpus-wide
            if not INAT_PROJECT_ID:
                self._json({"ok": False, "error": "INAT_PROJECT_ID not set"})
                return
            n = 0
            for sp in load_species(corpus):
                if not sp.get("inat_taxon_id"):
                    continue
                scan_species(corpus, sp)
                n += 1
                time.sleep(API_DELAY)
            self._json({"ok": True, "scanned": n})
            return

        if parsed.path == "/api/decide":
            self._json(apply_decision(req))
            return

        self.send_error(404)

    def log_message(self, fmt, *args):
        if args and isinstance(args[0], str) and ("/api/" in args[0] or "/photos/" in args[0]):
            return
        super().log_message(fmt, *args)


def preflight():
    problems = []
    if not os.path.isdir(REPO):
        problems.append(f"REPO not found: {REPO}")
    if not os.path.isfile(PLANT_JSON):
        problems.append(f"missing {PLANT_JSON}")
    if not os.path.isfile(WILDLIFE_JSON):
        problems.append(f"missing {WILDLIFE_JSON}")
    if problems:
        print("\n  STARTUP CHECK FAILED:")
        for p in problems:
            print("   - " + p)
        print("\n  Fix the REPO path in the config block and try again.\n")
        sys.exit(1)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(PHOTOS_DIR, exist_ok=True)


def main():
    preflight()
    print("\n  PSBP Photo Workbench  (YELLOW / review mode)")
    print(f"    repo:      {REPO}")
    print(f"    workspace: {WORKSPACE}")
    print(f"    project:   {INAT_PROJECT_ID or '(not set — scanning disabled)'}")
    print(f"\n    open: http://localhost:{PORT}")
    print("    Ctrl+C to stop\n")
    srv = http.server.HTTPServer(("", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        srv.server_close()


if __name__ == "__main__":
    main()
