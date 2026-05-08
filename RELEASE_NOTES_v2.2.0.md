# BingeAlert v2.2.0 — Per-user calendar feed

A minor release that adds a personal `.ics` calendar subscription for every user. Subscribe once from any notification-email footer and your calendar app fills in upcoming episodes for the next 60 days, with no further configuration on either side. No login required — the URL itself carries a per-user secret.

In-place upgrade with one alembic migration. No compose changes unless you want to set the new `PUBLIC_BASE_URL` (without it the calendar feed still works server-side; the email footer just won't include the link).

---

## What's new

### `GET /calendar/{token}.ics` — public, token-authenticated calendar feed

A new public router serves an RFC 5545 `text/calendar` response per user. The token in the URL is a stable random secret stored on `users.calendar_token`, generated either by migration 0002 (existing rows) or by the SQLAlchemy `default=` on the column (new rows). The URL is the credential — anyone with it can read your upcoming episodes — so it's treated like a bearer token. 24 url-safe bytes (~192 bits) makes guessing infeasible.

Output:
- One `VEVENT` per upcoming episode in the next 60 days, intersecting that user's tracked TV `MediaRequest` rows with the Sonarr / Sonarr-Anime calendar window.
- `DTSTART` from `airDateUtc`, `DTEND` from `runtime` (default 60 min if Sonarr doesn't supply one).
- `SUMMARY` reads "Severance S02E05 — Cold Harbor".
- `STATUS:CONFIRMED` once the episode has a file in Sonarr, else `STATUS:TENTATIVE` so calendar apps can render unfetched-yet episodes differently.
- `Cache-Control: public, max-age=900` — calendar apps poll on their own schedule (Apple hourly, Google every few hours), so 15 minutes is the right tradeoff between freshness and Sonarr load.
- Sonarr fetch failures degrade gracefully: still return a syntactically-valid empty `VCALENDAR` so the user's calendar subscription doesn't break on a transient outage.

Scoping is **personal only** — shared `MediaRequest`s are deliberately excluded so households with one feed per person don't double-list the same episode. (If you want a household-wide single feed, the schema supports it — file an issue and we can add a per-user toggle.)

### Notification emails: footer with per-user calendar link

Every notification email (episode-batch, single episode, movie, weekly summary, quality-waiting, coming-soon) gets a small footer injected just before `</body>`:

```
📅 Subscribe to your upcoming-episodes calendar.
   Add to your calendar app  (webcal:// link)
   Or paste this URL into your calendar app: https://your-host/calendar/<token>.ics
```

The `webcal://` scheme triggers most OS-level "Subscribe to calendar?" prompts. The `https://` URL is the literal fallback for clients that strip schemes they don't recognize.

Footer injection is gated on three conditions; missing any one of them silently omits the footer (the email still goes out unaffected):

1. The `User` row is passed into `send_email()`.
2. `users.calendar_token` is non-NULL.
3. `settings.public_base_url` is configured.

### New setting: `public_base_url`

The external-facing URL of your BingeAlert instance, e.g. `https://bingealert.example.com`. Used by the calendar footer; future password-reset / "click here" emails will reuse it. Set via the `/data/config.json` file, or `PUBLIC_BASE_URL` env var. **If unset, the calendar feed itself still works** — you just won't see the link in emails until you configure it.

### `users.calendar_token` column (alembic 0002_calendar_token)

Adds `users.calendar_token` (TEXT, unique, indexed, nullable). Existing rows get backfilled in the migration's data step using `secrets.token_urlsafe(24)`. New users created via the ORM after this migration get a token from the column's `default=` — no application-level token-generation code needed.

### Version footer fix

The dashboard footer in v2.1.1 / v2.1.2 / v2.1.3 was reading `v2.1.0` because `app/__init__.py` wasn't bumped on those tags. **v2.2.0 corrects this** — the footer will now match the running tag.

---

## Upgrade

In-place, with one alembic migration on first start:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.1\.[0-9]+|bingealert:2.2.0|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After the new container starts, alembic auto-runs `0002_calendar_token` (additive — adds a column, backfills tokens, no destructive changes). Existing users immediately have working calendar URLs the next time they receive a notification.

**To activate the email footer**, set the new `PUBLIC_BASE_URL`. Easiest:

```bash
# Edit /data/config.json (where the wizard writes settings) and add:
#   "public_base_url": "https://bingealert.example.com"
```

Or set the env var in your compose file. No restart required for `config.json` updates — `reload_from_disk()` picks them up on the next email-send loop iteration. (Env-var changes do need a `docker compose up -d --force-recreate`.)

---

## Subscribing a calendar app (user-facing)

1. Open any BingeAlert notification email.
2. Scroll to the bottom — the footer says "📅 Subscribe to your upcoming-episodes calendar."
3. Click "Add to your calendar app" — most modern OS calendar apps (macOS Calendar, iOS Calendar, Google Calendar, Outlook with `webcal://` configured) will prompt to subscribe.
4. If the click doesn't trigger a prompt, copy the `https://...calendar/...ics` URL from the second line and paste it into your calendar's "Add subscription" / "From URL" dialog.

The calendar updates on its own thereafter — no further BingeAlert interaction needed.

---

## What didn't change

- Existing notification flow (when emails get sent, who they go to, what the body looks like) is unchanged. The footer is purely additive.
- Existing `/admin/upcoming-episodes` endpoint — same shape, same behavior. The calendar feed has its own per-user code path; admin-only logic isn't touched.
- Auth model — only `/calendar/` is added to `_PUBLIC_PATHS`; the rest of the auth gate is untouched.

---

## Caveats

- **Calendar app polling cadence varies.** Apple Calendar polls hourly by default; Google polls less frequently. A new episode appearing in Sonarr's calendar may take up to an hour to surface in the user's calendar app. The 15-minute server cache means a poll-storm by the user's calendar isn't going to repeatedly hit Sonarr.
- **Token rotation isn't UI-exposed yet.** If a user reports their calendar URL leaked, rotate manually:
  ```sql
  UPDATE users SET calendar_token = '<new random>' WHERE id = <id>;
  ```
  An admin "regenerate calendar URL" button can land in a future patch if it becomes a real workflow.
- **Empty-Sonarr fallback returns a valid empty calendar.** A transient Sonarr outage won't break user subscriptions — they'll just see no events until the next successful fetch. This is by design (a 500 would cause some calendar apps to unsubscribe).
- **Per-event runtime defaults to 60 min** when Sonarr doesn't supply one. For 22-min comedies this overshoots; the calendar event will look longer than the actual airing window. Acceptable rough-cut; could be tightened later.
