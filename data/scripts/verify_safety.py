#!/usr/bin/env python3
"""
verify_safety.py  —  step-6 end-to-end proof of the pipeline's safety nets.

NON-DESTRUCTIVE. It copies your real scripts + schemas + current staging into a
throwaway temp folder, deliberately breaks ONE low-stakes feed (newsletters)
there, runs the real engine, and checks that the gate holds last-known-good.
Your actual repo (data/staging, data/published) is NEVER modified. The temp
folder is deleted at the end.

Run it from the repo root:
    python3 data/scripts/verify_safety.py

It proves two things on YOUR real code + schemas:
  1. Volume guard — a feed that fetches ZERO rows goes RED and serves
     last-known-good (doesn't publish an empty feed).
  2. Header block — a feed missing a required column goes RED and serves
     last-known-good (the 2026-06-14 News-blackout catch).
"""

import json, os, shutil, subprocess, sys, tempfile

REPO = os.getcwd()
FEED = "newsletters"          # lowest-stakes feed to break in the copy
GREEN = "\033[32m"; RED = "\033[31m"; DIM = "\033[2m"; OFF = "\033[0m"

def need(p):
    if not os.path.exists(p):
        sys.exit(f"Can't find {p} — run this from the repo root "
                 f"(the folder that contains the 'data' folder).")

def main():
    need("data/scripts/validate_promote.py")
    need("data/schemas")
    need(f"data/staging/{FEED}.json")

    tmp = tempfile.mkdtemp(prefix="psbp_verify_")
    try:
        # mirror just enough of the repo into the temp dir
        for sub in ["data/scripts", "data/schemas", "data/staging/.prev", "data/published"]:
            os.makedirs(os.path.join(tmp, sub), exist_ok=True)
        for f in os.listdir("data/scripts"):
            if f.endswith(".py"):
                shutil.copy(f"data/scripts/{f}", os.path.join(tmp, "data/scripts", f))
        for f in os.listdir("data/schemas"):
            if f.endswith(".py"):
                shutil.copy(f"data/schemas/{f}", os.path.join(tmp, "data/schemas", f))
        for f in os.listdir("data/staging"):
            if f.endswith(".json"):
                shutil.copy(f"data/staging/{f}", os.path.join(tmp, "data/staging", f))

        stg = os.path.join(tmp, "data/staging", f"{FEED}.json")
        pub = os.path.join(tmp, "data/published", f"{FEED}.json")
        good = json.load(open(stg))

        def run():
            subprocess.run([sys.executable, "data/scripts/validate_promote.py"],
                           cwd=tmp, capture_output=True, text=True)
            health = json.load(open(os.path.join(tmp, "data/published/_health.json")))
            feed = next(f for f in health["feeds"] if f["tab"] == FEED)
            published_rows = len(json.load(open(pub))) if os.path.exists(pub) else 0
            return feed, published_rows

        # ---- baseline: real data should publish green ----
        base, base_rows = run()
        print(f"{DIM}baseline: {FEED} is {base['status']} with {base_rows} published rows{OFF}")
        if base_rows == 0:
            sys.exit(f"{FEED} has no published rows even on good data — can't run the held-data check.")

        results = []

        # ---- TEST 1: volume guard (zero rows) ----
        json.dump({"headers": good["headers"], "rows": []}, open(stg, "w"))
        feed, rows_after = run()
        ok1 = feed["status"] == "red" and rows_after == base_rows
        results.append(("Volume guard: empty feed → RED + last-known-good held", ok1,
                        f"status={feed['status']}, published rows still {rows_after} (was {base_rows})"))

        # restore, then TEST 2
        json.dump(good, open(stg, "w"))

        # ---- TEST 2: header block (drop a required column) ----
        req = json.load(open(os.path.join(tmp, "data/staging", f"{FEED}.json")))
        # find a required header to remove (date is required for newsletters)
        drop = "date" if "date" in req["headers"] else req["headers"][0]
        req["headers"] = [h for h in req["headers"] if h != drop]
        for r in req["rows"]:
            r.pop(drop, None)
        json.dump(req, open(stg, "w"))
        feed, rows_after = run()
        ok2 = feed["status"] == "red" and rows_after == base_rows
        results.append((f"Header block: missing '{drop}' → RED + last-known-good held", ok2,
                        f"status={feed['status']}, published rows still {rows_after} (was {base_rows})"))

        # ---- report ----
        print()
        allok = True
        for label, ok, detail in results:
            tag = f"{GREEN}PASS{OFF}" if ok else f"{RED}FAIL{OFF}"
            print(f"  [{tag}] {label}")
            print(f"         {DIM}{detail}{OFF}")
            allok = allok and ok
        print()
        print(f"{GREEN if allok else RED}{'ALL SAFETY NETS HOLD' if allok else 'SOMETHING FAILED — look above'}{OFF}")
        print(f"{DIM}(your real repo was not touched; temp copy removed){OFF}")
        return 0 if allok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    raise SystemExit(main())
