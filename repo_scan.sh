#!/usr/bin/env bash
# PSBP repo scanner — run from the repo root:  bash repo_scan.sh
# Produces repo_scan.txt. Paste that file back into the chat.
# Summarizes the bulky content folders (photos/plants/wildlife) instead of
# listing every file; full detail everywhere else.

set -u
OUT="repo_scan.txt"
BULK_DIRS=("photos" "plants" "wildlife")

# Build the prune expression for find (skip .git + bulk dirs in the full listing)
PRUNE=( '(' -path './.git' )
for d in "${BULK_DIRS[@]}"; do PRUNE+=( -o -path "./$d" ); done
PRUNE+=( ')' )

{
echo "================================================================"
echo "PSBP REPO SCAN"
echo "Generated : $(date)"
echo "Root      : $(pwd)"
echo "================================================================"
echo

echo "### 1. TOP-LEVEL SIZE OVERVIEW ###"
du -sh ./* 2>/dev/null | sort -h
echo
echo "Total repo size (excluding .git):"
du -sh --exclude=.git . 2>/dev/null || du -sh . 2>/dev/null
echo

echo "### 2. BULK CONTENT FOLDERS (summarized, not listed) ###"
for d in "${BULK_DIRS[@]}"; do
  if [ -d "$d" ]; then
    files=$(find "$d" -type f | wc -l | tr -d ' ')
    subs=$(find "$d" -type d | wc -l | tr -d ' ')
    size=$(du -sh "$d" 2>/dev/null | cut -f1)
    echo "-- $d/  : $files files, $subs folders, $size"
    echo "   extensions:"
    find "$d" -type f | sed 's/.*\.//' | tr 'A-Z' 'a-z' | sort | uniq -c | sort -rn | sed 's/^/     /'
    echo "   sample names:"
    find "$d" -type f | head -5 | sed 's/^/     /'
    echo
  fi
done

echo "### 3. FULL FILE LISTING — everything except bulk folders & .git ###"
echo "(size in KB, then path)"
find . "${PRUNE[@]}" -prune -o -type f -print | sort | while read -r f; do
  sz=$(du -k "$f" 2>/dev/null | cut -f1)
  printf "%8s  %s\n" "${sz:-?}" "$f"
done
echo

echo "### 4. EVERY .json IN THE REPO (size in KB) ###"
echo "(stray troubleshooting JSONs tend to live in data/sources)"
find . -path './.git' -prune -o -type f -name '*.json' -print | sort | while read -r f; do
  sz=$(du -k "$f" 2>/dev/null | cut -f1)
  printf "%8s  %s\n" "${sz:-?}" "$f"
done
echo

echo "### 5. EVERY .py IN THE REPO (size in KB) ###"
find . -path './.git' -prune -o -type f -name '*.py' -print | sort | while read -r f; do
  sz=$(du -k "$f" 2>/dev/null | cut -f1)
  printf "%8s  %s\n" "${sz:-?}" "$f"
done
echo

echo "### 6. SUSPICIOUS / LIKELY-CRUFT FILENAMES ###"
echo "(copy, backup, old, tmp, test, draft, __N, (N), .bak/.orig/.swp, .DS_Store, trailing ~)"
find . -path './.git' -prune -o -type f -print \
  | grep -iE '(copy|backup|[^a-z]old[^a-z]|tmp|temp|draft|conflicted|__[0-9]|\([0-9]+\)|\.bak|\.orig|\.swp|\.DS_Store|~$)' \
  | sort | sed 's/^/   /'
echo "   (also check for ' test ' / scratch scripts manually)"
echo

echo "### 7. 30 LARGEST FILES (bloat check, excludes .git) ###"
find . -path './.git' -prune -o -type f -print0 \
  | xargs -0 du -k 2>/dev/null | sort -rn | head -30 \
  | awk '{ printf "%10s KB  ", $1; $1=""; sub(/^ /,""); print }'
echo

echo "### 8. DIRECTORY TREE (folders only, no .git) ###"
find . -path './.git' -prune -o -type d -print | sort | sed 's/^/   /'
echo

echo "================================================================"
echo "END OF SCAN"
echo "================================================================"
} > "$OUT" 2>&1

echo "Done. Wrote $OUT ($(wc -l < "$OUT" | tr -d ' ') lines)."
echo "Open it, skim it, then paste its contents back into the chat."
