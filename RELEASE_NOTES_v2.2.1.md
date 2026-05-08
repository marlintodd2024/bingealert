# BingeAlert v2.2.1 — `public_base_url` configurable from Settings

A patch release that surfaces the new `public_base_url` setting (added in v2.2.0 for the calendar-feed email footer) directly in the admin Settings page. No more hand-editing `/data/config.json` to enable the calendar links in notifications.

In-place upgrade, no schema change, no compose change.

---

## What changed

### Settings page now exposes "External-facing URL"

A new input lands in the Email + Notifications section of the admin Settings tab, just below "Admin Email":

```
External-facing URL (for links in emails)
[ https://bingealert.example.com                                  ]
Where users reach this BingeAlert from outside your network. Used
for the "Subscribe to your calendar" link in notification emails
(and future password-reset links). Leave blank to omit those links.
Must start with http:// or https://.
```

Value flows through the existing `loadConfig` / `saveConfig` round-trip:

- **`GET /admin/config`** now includes `"public_base_url": "..."` at the top level next to `admin_email`.
- **`POST /admin/config`** accepts `public_base_url` and routes through the same `take()` flatten that every other setting uses, then `write_to_disk` + `reload_from_disk()` so the next email-send loop iteration picks up the new value (no container restart).

### Backend validation: scheme is enforced

Saves go through a `_normalize_public_base_url(v)` transform that:

- Trims whitespace and trailing slashes.
- Returns `""` for empty input (footer is omitted, no email change).
- **Rejects any value that doesn't start with `http://` or `https://`.**

The reason for the scheme check matters: the value lands inside an `<a href="...">` in users' inboxes, so a `javascript:` or `data:` URL would render as a clickable XSS vector. The Settings UI's `pattern="https?://.*"` is the first gate; the backend transform is the authoritative one. The footer-injection code in `app/services/email_service.py` *also* re-checks the scheme defensively so a hand-edited `config.json` can't bypass the validator.

---

## Upgrade

In-place, no migration:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.2\.[0-9]+|bingealert:2.2.1|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

Then in the dashboard: **Settings → Email & Notifications → External-facing URL** → fill it in → Save. The next notification email a user receives will include the calendar subscribe footer.

If you already set `public_base_url` by hand in `/data/config.json`, the value will appear pre-filled in the new input on first load — nothing to do.

---

## What didn't change

- The calendar feed itself (`/calendar/{token}.ics`) is unchanged. It already worked from v2.2.0; this release only changes how the footer-link URL gets configured.
- No alembic migration. `users.calendar_token` from 0002_calendar_token is unchanged.
- All existing settings (SMTP, Sonarr, Radarr, Plex, etc.) round-trip identically.

---

## Caveats

- **Browser-side `pattern` is advisory only.** The HTML5 `pattern` attribute keeps the user honest in modern browsers, but a determined save against a non-http(s) value would land at `POST /admin/config`, which now responds successfully but skips the field (the `take()` transform raises and the flatten loop logs nothing back). You'll see "Updated 0 settings" if you try to save `javascript:...`. A future patch can surface a per-field error in the response.
- **Settings reload is the next-email-send-iteration only.** `reload_from_disk()` updates the in-memory `settings` singleton; the email service reads `settings.public_base_url` per send. Notifications already in flight (the worker drains every 60s) keep whatever value was current when their batch was assembled — usually irrelevant, but flag in case a save lands mid-drain.
