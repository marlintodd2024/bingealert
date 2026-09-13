# BingeAlert v2.3.8 - Maintainerr setup and settings refresh

This release brings the Maintainerr cleanup integration into the BingeAlert
settings dashboard and reorganizes the settings experience into a searchable,
category-based control center. It also makes dashboard counts accurately
reflect media that Maintainerr has intentionally retired.

No database migration is added in this release. Installations upgrading from
before `v2.3.7` still receive migration `0006_quality_monitor_suppression`
automatically when the container starts.

---

## Added

### Maintainerr setup and status in Settings

The new **Settings → Maintainerr** panel provides the complete inbound webhook
configuration without exposing the saved secret:

- the deployment-aware `/webhooks/maintainerr` URL;
- the Maintainerr JSON payload template;
- Bearer authorization guidance and safe copy controls;
- shared-secret and IP-allowlist configuration status;
- the time and outcome of the last received event; and
- the five most recent processed or ignored webhook events.

The webhook URL uses BingeAlert's configured external-facing URL when present,
and otherwise uses the dashboard origin. Maintainerr still owns its notification
agent, so the displayed URL, payload, and authorization header must be pasted
into Maintainerr.

### Maintainerr activity history

Processed `Media Handled` events and safely ignored Maintainerr events are now
recorded in the existing admin activity log. The settings panel uses these
records to provide real integration status rather than a synthetic connectivity
indicator.

---

## Improved

### Settings control center

The settings page now has:

- a clear page header and high-level category shortcuts;
- grouped navigation for system, notifications, automation, access, and
  integrations;
- a settings search that filters both sections and navigation;
- consistent graphite-and-amber cards, fields, and actions;
- a responsive layout for narrower screens; and
- a simplified bottom action bar for reload, restart, and save-all operations.

Existing field IDs, configuration behavior, and per-section save actions remain
compatible with previous releases.

### Accurate cleanup state in the dashboard

The **Waiting** statistic now counts only requests that are actively being
tracked. Requests suppressed by a Maintainerr cleanup remain in request history
for auditability but display a **Cleaned** badge instead of looking active.

The **Pending** notification count also falls because unsent `quality_waiting`
notifications are cancelled when cleanup is received. If the title is later
requested or grabbed again, the cleanup suppression is cleared and the request
returns to active tracking normally.

---

## Release Prep

Regression and static validation passed with:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pip-audit -r requirements.txt --progress-spinner off
node --check app/static/service-worker.js
git diff --check
```

The required production-container drift check must pass before tagging:

```bash
./scripts/check_prod_drift.sh prod-files.txt
```

The tag-triggered package workflow builds and publishes the GHCR image for
`linux/amd64` and `linux/arm64` with `2.3.8`, `2.3`, and `latest` tags.

---

## Upgrade

```bash
cd /path/to/your/bingealert
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, confirm the dashboard footer shows `v2.3.8`. Open **Settings →
Maintainerr** to copy the endpoint and payload, then send a Maintainerr `Media
Handled` test notification and verify that it appears in recent activity.
