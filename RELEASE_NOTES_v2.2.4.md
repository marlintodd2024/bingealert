# BingeAlert v2.2.4 — security hardening and settings coverage

A patch release focused on addressing security scanner findings and making the
new controls fully manageable from the admin Settings page.

In-place upgrade, no database migration required. Restart the container after
upgrading so the updated dependency set and any changed runtime settings are
loaded cleanly.

---

## Security hardening

### Webhook access controls

- Webhook IP allowlisting now reads from the v2 settings object
  (`webhook_allowed_ips`) instead of the legacy `WEBHOOK_ALLOWED_IPS`
  environment-only path.
- Added optional `webhook_secret`. When configured, Sonarr, Radarr, and Seerr
  webhook requests must send the secret as one of:
  - `X-BingeAlert-Webhook-Secret`
  - `X-Webhook-Secret`
  - `?token=...`
- Invalid webhook secrets and rejected IPs return `403 Forbidden`.

### Proxy header trust

- `X-Forwarded-For`, `X-Real-IP`, and `CF-Connecting-IP` are now trusted only
  when the immediate peer matches `trusted_proxy_cidrs`.
- This prevents direct clients from spoofing proxy headers to trigger the
  local-network auth bypass or bypass webhook IP checks.

### SSRF guardrails

- Added shared URL validation for configured Jellyseerr, Sonarr, Radarr, Plex,
  and public-base URLs.
- Blocks non-HTTP schemes, loopback, link-local, wildcard, `localhost`,
  metadata-service hosts, embedded credentials, and URL fragments.
- Private LAN targets remain supported because BingeAlert commonly talks to
  media services on home-network addresses.

### HTML and log safety

- Weekly summary emails now HTML-escape usernames, email addresses, and
  notification subjects.
- Maintenance-window titles are sanitized before logging to prevent multiline
  log injection.
- Admin issue notifications now sanitize configured admin email addresses
  before sending/logging.

### Dependency update

- Upgraded FastAPI/Starlette pins to:
  - `fastapi==0.115.6`
  - `starlette==0.41.3`
- This clears the Starlette multipart DoS advisory fixed upstream in Starlette
  `0.40.0`.

---

## Settings page

The admin Settings page now exposes and saves all new security controls:

- `webhook_allowed_ips`
- `webhook_secret` with masked loading, clearing, and a Generate button
- `trusted_proxy_cidrs`
- existing app secret key generation remains available

The Security Status checklist now reports whether webhook IP allowlisting,
webhook shared secret, trusted proxy CIDRs, auth, production mode, and app
secret strength are configured.

---

## Email copy

- Updated the default episode and movie email footer from
  `This is an automated notification from your BingeAlert` to
  `This is an automated notification from BingeAlert`.

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.4|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

If you enable `webhook_secret`, update your Sonarr, Radarr, and Seerr webhook
configuration to send the same value before or immediately after the upgrade.
