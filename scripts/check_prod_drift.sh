#!/usr/bin/env bash
# Compare files present in the running prod container against git ls-files.
#
# Usage:
#   1. On the prod box, capture the file list:
#        docker exec bingealert find /app -type f \
#          \( -name "*.py" -o -name "*.html" -o -name "*.json" \
#             -o -name "*.css" -o -name "*.js" -o -name "*.mako" \
#             -o -name "*.ini" -o -name "*.txt" \) \
#          -not -path "*/node_modules/*" -not -path "*/__pycache__/*" \
#          | sort > prod-files.txt
#   2. Copy prod-files.txt to this repo root.
#   3. Run: ./scripts/check_prod_drift.sh prod-files.txt
#
# Output sections:
#   [PROD-ONLY] files present on prod but not tracked in git -- the dangerous bucket
#   [GIT-ONLY]  files tracked in git but not on prod -- usually obsolete, sometimes orphaned
#   [SUMMARY]   counts
#
# The find on prod runs from /app inside the container, where the WHOLE repo
# is the WORKDIR. So /app/app/main.py = repo app/main.py, /app/alembic/env.py
# = repo alembic/env.py, /app/requirements.txt = repo requirements.txt. We
# strip the leading /app/ entirely (no replacement) to get repo-relative paths.
#
# We also ignore /app/backups/** -- those are container-side backup copies
# (e.g. pre-security-patch snapshots) and aren't expected to be in git.

set -euo pipefail

PROD_LIST="${1:-prod-files.txt}"

if [[ ! -f "$PROD_LIST" ]]; then
  echo "error: prod file list not found: $PROD_LIST" >&2
  echo "see usage at top of $0" >&2
  exit 2
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: must be run from inside the git repo" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# Normalize prod paths: strip leading /app/ entirely so /app/app/main.py
# becomes app/main.py and /app/alembic/env.py becomes alembic/env.py.
# Drop blanks, lines that don't start with /app/, and the backups/ tree.
sed -n 's|^/app/||p' "$PROD_LIST" \
  | grep -v '^backups/' \
  | sort -u > "$tmpdir/prod.txt"

# Compare against ALL tracked files, then filter to the same extensions the
# prod find used so the comparison is symmetric.
git ls-files \
  | grep -E '\.(py|html|json|css|js|mako|ini|txt)$' \
  | sort -u > "$tmpdir/git.txt"

prod_only="$(comm -23 "$tmpdir/prod.txt" "$tmpdir/git.txt")"
git_only="$(comm -13 "$tmpdir/prod.txt" "$tmpdir/git.txt")"

echo "=== [PROD-ONLY] files on prod but NOT in git (potential drift) ==="
if [[ -z "$prod_only" ]]; then
  echo "  (none -- repo covers everything prod has)"
else
  echo "$prod_only" | sed 's/^/  /'
fi
echo

echo "=== [GIT-ONLY] files in git but NOT on prod (likely obsolete or never deployed) ==="
if [[ -z "$git_only" ]]; then
  echo "  (none)"
else
  echo "$git_only" | sed 's/^/  /'
fi
echo

prod_count=$(wc -l < "$tmpdir/prod.txt" | tr -d ' ')
git_count=$(wc -l < "$tmpdir/git.txt" | tr -d ' ')
prod_only_count=$(printf '%s' "$prod_only" | grep -c . || true)
git_only_count=$(printf '%s' "$git_only" | grep -c . || true)

echo "=== [SUMMARY] ==="
echo "  prod files (excl backups/):  $prod_count"
echo "  git files (matching exts):   $git_count"
echo "  prod-only (drift):           $prod_only_count"
echo "  git-only (obsolete?):        $git_only_count"

# Exit non-zero if there's drift -- useful for CI later.
if [[ "$prod_only_count" -gt 0 ]]; then
  exit 1
fi
