# BingeAlert v2.3.7 - Maintainerr-aware media cleanup

This release prevents intentionally retired media from being mistaken for an
outstanding quality upgrade after Maintainerr removes it. It combines native
Radarr/Sonarr lifecycle checks with an optional Maintainerr `Media Handled`
webhook, while preserving normal behavior when media is requested again later.

This release includes database migration `0006_quality_monitor_suppression`.
The container runs `alembic upgrade head` automatically before BingeAlert
starts, so normal Docker upgrades require no manual migration command.

It also carries forward the `v2.3.6` notification queue resilience changes;
one malformed notification cannot stop later ready rows from being processed.

---

## Added

### Maintainerr cleanup webhook

A new authenticated endpoint accepts Maintainerr cleanup notifications:

```text
POST /webhooks/maintainerr
```

Configure a Maintainerr Webhook notification agent with this payload:

```json
{
  "notification_type": "{{notification_type}}"
}
```

Enable only **Media Handled** and connect the agent only to rule groups whose
configured action deletes media. Current Maintainerr builds flatten the event's
`mediaItems` metadata into the outgoing payload. BingeAlert accepts both the
flattened form and older templates that preserve the values inside `extra`.

Whole movies and shows are matched by TMDB ID. A handled event:

- records a reversible quality-monitor suppression;
- cancels pending `quality_waiting` notifications for matching requests; and
- remains safe when Maintainerr retries the same event.

Season and episode provider IDs describe child objects rather than the parent
show. Those events deliberately do not suppress an entire TV request, which
would otherwise hide legitimate future episodes.

### Maintainerr-compatible webhook authentication

The shared webhook secret can now also be supplied as:

```text
Authorization: Bearer YOUR_WEBHOOK_SECRET
```

The existing `X-BingeAlert-Webhook-Secret`, `X-Webhook-Secret`, query-token, and
IP/subnet allowlist options continue to work.

---

## Fixed

### SMTP dependency security update

`aiosmtplib` is updated from `5.1.1` to `5.1.2`, resolving
`PYSEC-2026-3805`.

### Intentional cleanup no longer looks like a quality wait

The quality monitor now skips:

- unmonitored Radarr movies;
- unmonitored Sonarr shows and episodes;
- whole movie/show requests suppressed by a Maintainerr handled event; and
- TV episodes already recorded as delivered in BingeAlert's episode tracking or
  durable delivery ledger.

TV requests stay approved so future episodes can still be announced, but an old
episode removed by a retention policy no longer reopens as "waiting for
quality."

The Sonarr quality lookup also now compares the stored Seerr TMDB ID with
Sonarr's `tmdbId`; it previously compared that value with `tvdbId`.

### Delayed-email cleanup race

The notification processor rechecks cleanup suppression immediately before
sending a ready quality-waiting email. This covers the race where the processor
loaded a row just before the Maintainerr webhook cancelled it, while retaining
the per-row failure isolation introduced in `v2.3.6`.

### Re-request lifecycle

Cleanup suppression is automatically cleared by a later:

- Seerr approval for the existing request;
- Sonarr Grab or Download event; or
- Radarr Grab or Download event.

A new Seerr request ID for the same user and title now creates a fresh request
row instead of being merged into the historical request. The new request can
therefore follow the complete availability-notification lifecycle. Historical
delivery dedupe is retained on the old request, so merely re-importing the same
old episode does not spam its original recipient.

---

## Release Prep

Regression and migration validation:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m unittest discover -s tests -v
DATA_DIR=/tmp/bingealert-v2.3.7 .venv/bin/alembic upgrade head
DATA_DIR=/tmp/bingealert-v2.3.7 .venv/bin/alembic downgrade -1
DATA_DIR=/tmp/bingealert-v2.3.7 .venv/bin/alembic upgrade head
node --check app/static/service-worker.js
git diff --check
```

The release must not be tagged until the required production-container drift
check passes:

```bash
./scripts/check_prod_drift.sh prod-files.txt
```

The tag-triggered package workflow builds and publishes the GHCR image for
`linux/amd64` and `linux/arm64` with stable semver tags.

---

## Upgrade

```bash
cd /path/to/your/bingealert
docker compose pull
docker compose up -d --force-recreate
```

The startup migration adds the reversible suppression fields automatically.
After upgrading, confirm the dashboard footer shows `v2.3.7`, configure the
Maintainerr webhook if desired, and send a Maintainerr test notification to
verify authentication and connectivity.
