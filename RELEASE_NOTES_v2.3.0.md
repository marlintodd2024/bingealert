# BingeAlert v2.3.0 - operations health center

A feature release focused on system health, connected-service monitoring,
admin cleanup, and a calmer dashboard.

This release includes a database migration: `0004_system_health`.

---

## Added

### System health monitoring

The admin Health tab now tracks connected service reachability for:

- Jellyseerr / Overseerr
- Sonarr
- optional Sonarr Anime
- Radarr
- Plex
- SMTP

Health checks record status, latency, consecutive failures, recent errors,
24-hour uptime, and recent health events. Background workers also report their
last run, next run, duration, and failure counts.

### Service alerts

Service health alerts can now be sent by email, webhook, or both. Webhook
payloads support generic JSON, Discord, and Slack-style messages.

### Operations automation settings

Settings now includes an Operations Automation panel for:

- health check interval, failure threshold, cooldown, and history retention
- enabling or disabling email service alerts
- webhook alert URL and payload type
- automatic sent-notification retention
- scheduled backups and backup retention
- one-click configuration validation

### Notification retention

Admins can manually purge sent notifications older than a selected number of
days. Optional scheduled retention can keep old sent notifications from growing
forever while leaving pending notifications alone.

### Scheduled backups

An operational maintenance worker can create periodic database/config backups
and prune older backup archives after a configurable count.

### Admin activity log

The new Activity tab records key admin actions, including config saves,
manual health checks, validation runs, notification cleanup, backups,
reconciliation, and maintenance operations.

### Configuration validation

The Settings page can now run a validation pass that checks core config,
security posture, email settings, Docker restart availability, public base URL,
and connected services.

---

## Changed

### Admin interface refresh

The admin dashboard has been tightened up with more compact summary cards,
cleaner tab treatment, improved table scrolling, sticky sortable headers, and
a more consistent dark UI across Settings, Health, Backup, Maintenance, and
list views.

### Sort and scroll behavior

Additional admin lists now preserve sort state and use the shared sortable
table path, including health services, worker health, health events, backups,
maintenance windows, and admin activity.

### Backup implementation

SQLite backups now use SQLite's backup API instead of raw file copying, which
reduces the risk of capturing a busy database in an inconsistent state.

---

## Fixed

### Background database sessions

Background session handling was tightened around webhook follow-up work so
post-response tasks do not leak SQLAlchemy sessions.

---

## Release Prep

Static validation passed with:

```bash
python3 -m py_compile app/config.py app/database.py app/background/system_health.py app/background/ops_maintenance.py app/routers/admin.py app/main.py app/services/admin_activity.py alembic/versions/0004_system_health.py
node -e '... admin.html script parse ...'
git diff --check
```

The required prod drift check was attempted, but blocked because
`prod-files.txt` was not present in the repo root:

```text
error: prod file list not found: prod-files.txt
```

Capture it from the production container before tagging the release:

```bash
docker exec bingealert find /app -type f \
  \( -name "*.py" -o -name "*.html" -o -name "*.json" \
     -o -name "*.css" -o -name "*.js" -o -name "*.mako" \
     -o -name "*.ini" -o -name "*.txt" \) \
  -not -path "*/node_modules/*" -not -path "*/__pycache__/*" \
  | sort > prod-files.txt

./scripts/check_prod_drift.sh prod-files.txt
```

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.3.0|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, run the database migration and confirm the dashboard footer
shows `v2.3.0`.
