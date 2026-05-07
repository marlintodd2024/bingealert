# BingeAlert v2.1.1 — Security patch (Aikido SAST cleanup)

A patch release that resolves the 5 findings from the first Aikido full-scan against the v2 codebase. No behavior changes — both fixes are hardening / pattern-cleanup against false-positive-but-worth-defending flags.

In-place upgrade, no data migration, no compose changes.

---

## What changed

### `scripts/migrate_from_v1.py` — parameterized SQL + table whitelist

The v1→v2 migration helper's `reset_sqlite_sequences()` was bumping `sqlite_sequence` counters using f-string SQL with a table-name placeholder. The names came from a hardcoded `TABLE_ORDER` list (no user input ever reached the function), but Aikido flagged four high-severity `AIK_python_B608` matches on the pattern itself.

Refactored to:

- **`_ALLOWED_TABLES = frozenset(TABLE_ORDER)`** at module scope, with an explicit `ValueError` raised if an unknown name is ever passed in. Defense-in-depth — the caller always passes safe names, but no name reaches a SQL identifier position without the assert clearing first.
- **SQLAlchemy core** (`MetaData.reflect` + `select(func.max(tbl.c.id))`) for the `MAX(id)` query that previously had to interpolate a table name as an identifier. No string-built SQL touches identifiers anymore.
- **Parameterized `text()` with bound parameters** for the three `sqlite_sequence` SELECT/INSERT/UPDATE queries — values now travel as bindparams, not f-string interpolation.

Net: 4 of 5 findings cleared at the source.

### `app/static/service-worker.js` — inline the validation, suppress the SSRF false-positive

The fetch handler was already calling `buildValidatedUrl()` (same-origin + http(s)-only + path-traversal rejection) before every `fetch()`, but Aikido's `AIK_js_ssrf` rule matches the *pattern* `fetch(<expression>)` regardless of upstream validation. Tried two structural fixes (URL object pass-through, inline call) — both still flagged.

Resolution: inline the validation call into the `fetch()` argument and add a single rule-scoped `// nosemgrep: AIK_js_ssrf` suppression with a one-line justification comment pointing at `buildValidatedUrl`. No validation logic changed; the suppression is rule-specific so future Aikido rules will still flag genuine issues in this file.

Net: 5th finding cleared.

---

## Aikido scan results

| | v2.1.0 | v2.1.1 |
|---|---|---|
| SAST findings | 5 | 0 |
| Secrets findings | 0 | 0 |
| IaC findings | 0 | 0 |

Verified end-to-end through the `mcp__aikido-mcp__aikido_full_scan` MCP tool against the patched [@aikidosec/mcp](https://www.npmjs.com/package/@aikidosec/mcp) bundle (see `~/.local/share/aikido-mcp-patched/PATCH_NOTES.md` on the dev box for the local workaround details — Aikido upstream has two macOS arm64 bugs that prevent SAST/IaC from running through Claude Code; the patch is dev-environment only and does not affect this release).

---

## Upgrade

In-place, no data migration, no compose changes:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.1\.[0-9]+|bingealert:2.1.1|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

The dashboard footer will read `© 2026 BingeAlert v2.1.1` once it's running. Hard-refresh any open admin tabs (Cmd/Ctrl-Shift-R) so the new service worker registers.

---

## What didn't change

- No backend behavior. Migration script output is identical for any well-formed input; the only observable difference is a clearer error if a future caller passes an unknown table name (which would have crashed obscurely before).
- No new dependencies. SQLAlchemy `text()` and `func` are already imported elsewhere in the file.
- No frontend / template / webhook / background worker changes.

---

## Caveats

- **The `nosemgrep` suppression is rule-scoped.** Only `AIK_js_ssrf` is silenced on that one line. If Aikido adds a new rule that legitimately matches the same site, it will still surface. If you ever add a *second* dynamic-URL `fetch()` call to the service worker, it will surface and the new call needs its own validator + justification.
- **Table whitelist is the real safety net.** The `_ALLOWED_TABLES` assert is what makes the remaining `MAX(id)` query safe — not the SQLAlchemy refactor on its own (reflection on an attacker-controlled name would still be a problem). Keep the whitelist authoritative.
