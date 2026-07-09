# BingeAlert v3.0.0 - Plex request operations dashboard

BingeAlert v3.0.0 expands the project from a request-notification relay into an
operations dashboard for Plex homelabs using Seerr, Sonarr, and Radarr.

The release answers three practical questions in one place: what happened after
the request, why a notification did or did not send, and what the admin needs to
look at today.

## Added

- Daily Admin Home with a prioritized action queue and 24-hour operating metrics.
- Per-request timelines across requests, webhooks, imports, availability,
  notifications, and reported issues.
- Sanitized Webhook Inbox with filters, payload inspection, guarded replay, and
  30-day default retention.
- Queue, root-folder storage, import, Plex-lag, service, and worker health views.
- Reports tab with trends, fulfillment time, delivery rate, top requesters,
  recurring failures, and oldest waiting requests.
- Revocable user status portal with request history, calendar link, issue link,
  quiet hours, digest mode, full-season waits, and quality-update preferences.
- Optional scheduled daily admin digest and scheduled weekly operations report.
- Grouped user digests and full-season delivery, plus a manual user-digest action.
- Pushover operator alerts for availability, service health, and Seerr issues.
- CI regression workflow for application tests and static JavaScript parsing.

## Changed

- Settings now shows one selected section at a time, with exactly one save action
  in each editable section header.
- Coming-soon notifications use the shared queue so quiet hours, digest mode, and
  retry behavior apply consistently.
- Movie-only notification processing no longer initializes Sonarr clients.
- Successful instant and grouped availability mail updates one shared durable
  delivery state path.
- The README now positions BingeAlert alongside Seerr, Sonarr/Radarr, Tautulli,
  Notifiarr, and broader alert hubs.

## Fixed

- Normal notification delivery could crash because a function-local `settings`
  import shadowed the configured application settings.
- Digest, full-season, and quality-update preferences were stored but did not
  affect delivery behavior.
- SMTP outages can never try to report themselves through SMTP email; webhook or
  Pushover remains available for that alert.
- Durable notification dedupe remains intact after sent-notification cleanup.
- Settings no longer requires scrolling through every configuration category or
  shows duplicated save actions.

## Database Upgrade

The container runs `alembic upgrade head` automatically. Upgrading from v2.3.5
adds:

- `0006_webhook_event_log`
- `0007_user_status_preferences`

The release-candidate test suite performs a real upgrade from
`0005_notification_delivery_log` through head and verifies existing users receive
safe defaults and revocable status tokens.

Back up `./data/` before upgrading. The SQLite database, WAL files, and
`config.json` should be backed up together.

## Upgrade

For installs following `latest`:

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d --force-recreate
```

For pinned installs, use:

```yaml
image: ghcr.io/marlintodd2024/bingealert:3.0.0
```

After startup, confirm the footer and `/api/version` show `v3.0.0`, then open
Health, Reports, Webhooks, and Settings once to verify the new views.

## Verification

Release-candidate validation includes:

```bash
python -m unittest discover -s tests -v
python -m compileall -q app alembic tests
node --check app/static/service-worker.js
git diff --check
```

Desktop and mobile browser checks cover the Daily Admin Home, focused Settings
sections and save actions, Reports, System Health JSON handling, and the user
status portal.

## Release Gate

Per project release policy, capture a fresh production container file list and
run the drift check immediately before tagging:

```bash
./scripts/check_prod_drift.sh prod-files.txt
```

Do not create the `v3.0.0` tag until that check passes. The tag triggers the
multi-architecture GHCR package build for `linux/amd64` and `linux/arm64`.
