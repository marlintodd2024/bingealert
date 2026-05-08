# BingeAlert v2.1.3 — No more pop-in tab counts

A patch release that kills the layout shift on tab clicks: every tab-count badge now populates on initial page load instead of waiting for the user's first click.

In-place upgrade, no data migration, no compose changes.

---

## What changed

### `/admin/stats` now returns `issues` count

The dashboard already pulled most counts from `/admin/stats` (users, requests total, notifications total). Added one more cheap DB count for `ReportedIssue` so every tab badge has a server-side value to display upfront.

### Frontend pre-populates every tab-count badge on load

`loadStats()` now fans the stats response out to `updateTabCount('users' | 'requests' | 'notifications' | 'issues', …)` after filling the dashboard cards. Result: the moment the page paints, every tab label already shows its count — no "Users" → click → "Users **42**" pop-in / layout shift.

### Upcoming fetches in the background after page load

`Upcoming Episodes` is the one count `/admin/stats` can't supply cheaply — computing it requires a Sonarr calendar call. Rather than leave the badge blank until the tab is clicked (the previous lazy-load behavior, which had the same pop-in problem), the page-load init now fires `loadUpcoming()` non-blocking right after kicking off the active tab's fetch. The badge populates a few seconds after the page paints, no clicks needed, and the existing 30s `_upcomingCache` makes subsequent navigations instant.

### Trade-off: one extra Sonarr call per page load

This is a deliberate departure from the lazy-load promise in v2.1.0 ("initial page load only fetches `/admin/stats` and the currently-visible tab's data"). The v2.1.0 design saved one Sonarr call per dashboard load; v2.1.3 spends it back to eliminate the visual jank. A self-hoster's dashboard isn't loaded thousands of times a minute — the right call is the smoother UX.

If you want the lazy-load behavior back (for example, you're getting Sonarr-rate-limited on dashboard loads), the change is a single line in `app/static/admin.html`'s page-load handler — drop the trailing `if (activeId !== 'upcoming') loadUpcoming();`.

---

## Upgrade

In-place, no data migration:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.1\.[0-9]+|bingealert:2.1.3|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

Hard-refresh open admin tabs (Cmd/Ctrl-Shift-R) so the new HTML + the new `/admin/stats` shape land together.

---

## What didn't change

- Per-tab Refresh buttons still bypass cache via `force=true`.
- 30s TTL cache on `_upcomingCache` is unchanged — only the *first* call to `loadUpcoming` per page load pays the Sonarr cost; subsequent calls within 30s return cached data.
- Other tabs (Backup, Maintenance, Settings, Logs) remain lazy — they don't carry counts and don't have the pop-in problem.
- No backend behavior change beyond the new `issues` field in `/admin/stats`.

---

## Caveats

- **Old admin.html with new backend, or vice versa, both still work.** The frontend tolerates a missing `issues` field (`data.issues || 0`); the backend still returns the rest of the stats payload to clients that ignore the new field. So a partial deploy doesn't break anything — but you'll keep seeing the pop-in until both sides upgrade.
- **The badge for `issues` now shows `0` on dashboards with no reported issues.** Previously it stayed hidden until you clicked the Issues tab. If a permanent visible "Issues 0" feels noisy, a future patch can hide the badge when count is zero.
