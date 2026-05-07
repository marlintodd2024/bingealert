# BingeAlert v2.1.0 — Admin Dashboard UX Overhaul

A minor release focused entirely on making the admin dashboard faster, cleaner, and easier to navigate at scale. Twelve quality-of-life improvements landed in one batch. No backend or data-model changes — pure frontend polish on `app/static/admin.html`.

In-place upgrade, no data migration, no compose changes.

---

## Highlights

If you don't read anything else, these four are the biggest wins:

- **Pagination on Notifications + Requests** — 50 rows per page. Prods with thousands of rows no longer dump the whole DOM on first paint.
- **Toasts everywhere** — every `alert()` and `confirm()` got replaced with a styled toast + modal-confirm. Settings save shows a green toast, preserves your scroll position, and stays where you were.
- **Lazy tab loads + caches** — only the visible tab fetches on initial dashboard load. Subsequent tabs fetch on first click and cache for the session. The expensive `/admin/upcoming-episodes` endpoint also gets a 30s client-side TTL cache.
- **Keyboard shortcuts** — `g u/r/n/e/i/s/b/m/l` jumps between tabs, `/` focuses the visible search box, `?` opens a help dialog, `Esc` closes any modal. Subtle "Press `?` for shortcuts" hint at the bottom-left.

---

## Full changelog

### Tables & data display

- **Pagination** on Notifications + Requests with prev/next buttons and a "Showing X–Y of N · Page A of B" label below each table. Page resets when you change the search/filter/sort.
- **Row counts in tab labels** — tabs now read "Notifications **5,630**", "Requests **579**", "Issues **7**", etc. Numbers refresh after every fetch.
- **Sort + filter persistence** across tab switches via `sessionStorage`. Sort by date → switch tabs → come back, your sort is still there. Same for search input and per-tab filters.
- **Sticky table headers** — `<th>` rows stay visible while scrolling long lists. Per-tab `max-height: 70vh; overflow: auto;` with an opaque header background so rows don't bleed through.
- **Empty states** — friendly copy instead of a blank table body. Examples: "No reported issues — your library is in good shape.", "No notifications yet — they will appear here once webhooks arrive.", "No upcoming episodes in the next 30 days."
- **Notification status pills** — Pending = amber (`#e5a00d`), Sent = green (`#4caf50`), Failed = red (`#f44336`), each with a colored dot. Replaces the previous plain-text status. Failed status is currently a client-side heuristic (`!sent && send_after > 24h ago`) since `/admin/notifications` doesn't expose `error_message` — flag for a future patch if you want a backend-driven signal.

### Navigation, feedback, perf

- **Toast notification system** — top-right slide-in stack, types `success` / `error` / `warn` / `info`, auto-dismiss after ~4s, click-`×` to close. Exposed as `showToast(type, message)`.
- **Every `alert()` / `confirm()` migrated** — ~20 call sites converted to toasts + a styled `showConfirm()` modal returning `Promise<boolean>` (Esc cancels, Enter confirms, backdrop dismiss, focus trap). Destructive actions (Clear All, Delete Backup, Restore Backup, Cancel/Delete Maintenance, Blacklist & re-search, etc.) keep their explicit yes/no prompt. **0 naked `alert()` call sites remain**; the only remaining literal `confirm(` is the intentional fallback inside `showConfirm` itself if the modal DOM isn't ready.
- **Settings save UX** — pre-save `confirm()` removed, success path shows a green toast, scroll position is captured before the request and restored after, no navigation, no reload. Edit one field deep in the page, save, stay where you were.
- **Lazy tab loads** — initial page load only fetches `/admin/stats` and the currently-visible tab's data. Other tabs fetch on first click and cache for the session. Each list tab gets its own 🔄 Refresh button for explicit invalidation. `refreshData()` (the global Refresh button) invalidates every cache and reloads stats + the visible tab.
- **30s TTL cache for `/admin/upcoming-episodes`** keyed by the `?days=` selector value. The endpoint hits Sonarr + Sonarr-Anime + a `/series` call on every load — caching saves the round-trip on tab revisits within a 30-second window. Bypassed by the per-tab Refresh button.
- **Compact action toolbar** — the four full-width action cards (SYNC / NOTIFICATIONS / MONITORING / CLEANUP) used to eat ~25% of viewport above the fold. Replaced with a single "Admin Actions" expander that defaults closed. Same buttons inside, just out of the way until you need them.
- **Keyboard shortcuts** — global chord handler that ignores typing in `<input>` / `<textarea>` / contenteditable. Jumps:
  - `g u` → Users
  - `g r` → Requests
  - `g n` → Notifications
  - `g e` → Upcoming Episodes
  - `g i` → Issues
  - `g s` → Settings
  - `g b` → Backup
  - `g m` → Maintenance
  - `g l` → Logs
  - `/` → focus the visible tab's search input
  - `?` → open shortcut help dialog
  - `Esc` → close help/confirm modals

---

## What didn't change

- No backend changes. Every admin endpoint behaves the same.
- No new dependencies, no framework introduced. Still vanilla JS, single-file `admin.html`. (Bigger refactor — single-page-app reshell — was discussed and deferred; this release was scoped to non-disruptive polish.)
- Login page, setup wizard, email templates, service worker, webhook handlers, background workers, migration script — all unchanged.

---

## Upgrade

In-place, no data migration:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.0\.[0-9]+|bingealert:2.1.0|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After it's running, the dashboard footer reads `© 2026 BingeAlert v2.1.0`. Hit `?` on any tab to see the keyboard shortcut help dialog.

If you have an admin tab open in a browser when you upgrade, hard-refresh (Cmd/Ctrl-Shift-R) so the new HTML + the new service worker (no service-worker changes in this release, but cached assets refresh on hard reload) get loaded.

---

## Caveats

- **Failed notification status is heuristic.** The status pill labels a notification "Failed" when `!sent && send_after > 24h ago`. The model has an `error_message` column populated by the email-send code, but `/admin/notifications` doesn't currently include it in the response. A future patch can expose it for an authoritative Failed signal.
- **Action toolbar defaults closed.** Existing users who relied on muscle memory for the action cards will need to click "Admin Actions" once. The shortcuts (`g s`, `g r`, etc.) are the faster path going forward.
- **Lazy tab cache is per-page-session.** Reloading the page clears all per-tab caches. The Refresh buttons exist for in-session forced refetch.

---

## Acknowledgements

Built via two parallel sub-agents on isolated worktrees, each owning six tasks on a non-overlapping slice of `admin.html`. The agent-A (`ux/tables`) and agent-B (`ux/nav`) commits remain in the merge graph for posterity. Conflict resolution combined A's `updateTabCount + applyHeaderSortIndicators + filter-pipeline render` with B's `tabCache.X = true` flag in each `loadX()` function.
