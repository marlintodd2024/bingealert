# BingeAlert v2.1.2 — CodeQL DOM-XSS cleanup

A patch release that resolves the two HIGH-severity CodeQL alerts on `app/static/admin.html` (#19 at line 2919, #20 at line 3032). Same root cause in both, plus one third site CodeQL didn't flag but had the same idiom — all three fixed in one line each.

In-place upgrade, no data migration, no compose changes.

---

## What changed

### `app/static/admin.html` — `parseInt` the user-controllable `days` selector

Three call sites read `days` from the `#upcomingDays` `<select>` value:

```js
const days = document.getElementById('upcomingDays')?.value || 30;
```

Two of them then interpolated `days` into `innerHTML` (the empty-state hints in `renderUpcomingGrouped` and `renderUpcoming`). CodeQL flagged both as "DOM text reinterpreted as HTML" — a user with devtools could overwrite the `<select>`'s value with HTML, and the next render would `innerHTML` it back into the document.

The third site (`loadUpcoming`) interpolated `days` into a `fetch()` URL — not a CodeQL flag, but the same defensive idiom applies.

All three now coerce to integer at the source:

```js
const days = parseInt(document.getElementById('upcomingDays')?.value, 10) || 30;
```

`parseInt` returns `NaN` for non-numeric junk; `|| 30` falls back to the default. The integer that survives can never carry HTML or query-string injection.

Net: 2 CodeQL HIGH findings cleared, plus one defensive bonus.

---

## CodeQL state after this patch

Expected once GitHub re-runs CodeQL on the push:

| | v2.1.1 | v2.1.2 |
|---|---|---|
| Open HIGH alerts (admin.html) | 2 | 0 |

---

## Upgrade

In-place, no data migration, no compose changes:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.1\.[0-9]+|bingealert:2.1.2|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

The dashboard footer will read `© 2026 BingeAlert v2.1.2` once it's running. Hard-refresh any open admin tabs (Cmd/Ctrl-Shift-R) so the new HTML is fetched.

---

## What didn't change

- No backend behavior. The `?days=` query parameter the API receives is unchanged for any well-formed selector value.
- No new dependencies, no framework introduced.
- Other `innerHTML` sinks in `admin.html` (rendering API-supplied series titles, user emails, etc.) are out of scope for this patch — CodeQL didn't flag them, and their data flow does not originate from a directly user-editable DOM input. A future hardening pass could escape every interpolation defensively.

---

## Caveats

- **Only the three `upcomingDays` reads were touched.** `admin.html` has many other `innerHTML` template-literal renders. If a future CodeQL run flags any, they need their own audit — the right fix depends on whether the source is API data (escape with `escapeAttr` / `textContent`) or DOM input (`parseInt` / type coercion).
- **CodeQL re-scan happens on push, not on tag.** The two open alerts will close once GitHub re-runs the workflow against `main` after the v2.1.2 push completes.
