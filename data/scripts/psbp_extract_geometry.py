#!/usr/bin/env python3
"""Pull the park's real geometry out of the scrapped KML into data/sources/park_geometry.json.

WHY
    The KML that Randy built in Google Earth — and abandoned — is the ONLY place the park's
    walking paths, pond outlines and building footprints exist. 31 LineStrings, 36 polygons.
    park-library's source archive README has flagged this since 2026-08-28: "Every walking route
    in the park, 8-69 vertices each. Nothing in explore has this."

    Its PLANT pins are unusable (562 stacked on one default coordinate, 630 outside the boundary
    — see the archive README). Its GEOMETRY is hand-drawn by Randy and is the good half.

USAGE
    python3 data/scripts/psbp_extract_geometry.py            # report what it would write
    python3 data/scripts/psbp_extract_geometry.py --write

    Source KML lives in park-library, which is a separate private repo; pass --kml if it has moved.
"""
import argparse, json, os, sys, xml.etree.ElementTree as ET

K = "{http://www.opengis.net/kml/2.2}"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_KML = os.path.join(os.path.dirname(REPO), "park-library", "source archive",
                           "Palma_Sola_Botanic_Park_v232.kml")
OUT = os.path.join(REPO, "data", "sources", "park_geometry.json")

# Park bounds — anything outside is KML staging debris, not real geometry.
N, S, W, E = 27.5155, 27.5118, -82.6620, -82.6575


def coords(text):
    out = []
    for tok in (text or "").split():
        p = tok.split(",")
        if len(p) >= 2:
            try:
                out.append([round(float(p[1]), 7), round(float(p[0]), 7)])   # lat, lng
            except ValueError:
                pass
    return out


def inside(pts):
    return pts and all(S <= a <= N and W <= b <= E for a, b in pts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--kml", default=DEFAULT_KML)
    a = ap.parse_args()
    if not os.path.exists(a.kml):
        print(f"  KML not found: {a.kml}")
        return 1

    root = ET.parse(a.kml).getroot()
    paths, polys, points, dropped = [], [], [], 0
    for pm in root.iter(K + "Placemark"):
        nm = pm.find(K + "name")
        nm = (nm.text or "").strip() if nm is not None else ""
        for ls in pm.iter(K + "LineString"):
            c = ls.find(K + "coordinates")
            pts = coords(c.text if c is not None else "")
            if inside(pts):
                paths.append({"name": nm, "points": pts})
            elif pts:
                dropped += 1
        for pg in pm.iter(K + "Polygon"):
            pts = []
            for c in pg.iter(K + "coordinates"):
                pts += coords(c.text)
            if inside(pts):
                polys.append({"name": nm, "points": pts})
            elif pts:
                dropped += 1
        for pt in pm.iter(K + "Point"):
            c = pt.find(K + "coordinates")
            pts = coords(c.text if c is not None else "")
            # Landmark points only — plant pins are the unusable half of this file.
            if inside(pts) and any(k in nm.lower() for k in
                    ("gazebo", "bridge", "entrance", "parking", "bulletin", "fountain",
                     "restroom", "office", "galleria", "pavilion", "nursery", "island")):
                points.append({"name": nm, "point": pts[0]})
            elif pts:
                dropped += 1

    data = {"meta": {"source": os.path.basename(a.kml),
                     "note": "Hand-drawn park geometry recovered from the scrapped Google Earth "
                             "project. Coordinates are [lat, lng]. Plant pins from the same file "
                             "are deliberately NOT imported — see park-library source archive.",
                     "bounds": {"n": N, "s": S, "w": W, "e": E}},
            "paths": sorted(paths, key=lambda x: x["name"]),
            "polygons": sorted(polys, key=lambda x: x["name"]),
            "points": sorted(points, key=lambda x: x["name"])}

    print(f"  paths    {len(paths):3}   {sum(len(p['points']) for p in paths):5} vertices")
    print(f"  polygons {len(polys):3}   {sum(len(p['points']) for p in polys):5} vertices")
    print(f"  points   {len(points):3}")
    print(f"  dropped (outside park bounds, KML staging debris): {dropped}")
    if not a.write:
        print(f"\n  Dry run. Add --write to create {os.path.relpath(OUT, REPO)}")
        return 0
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"\n  wrote {os.path.relpath(OUT, REPO)}  ({os.path.getsize(OUT)/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
