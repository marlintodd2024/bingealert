# BingeAlert v2.3.6 - notification queue resilience

Hotfix release for notification rows that stayed pending even after their
scheduled send time.

No database migration is added in this release. Installations upgrading from
before `v2.3.1` still need the `0005_notification_delivery_log` migration.

---

## Fixed

### Pending notification queue stalls

The pending-notification processor now isolates failures to the affected row or
episode batch. If one notification hits an unexpected processing error, the
processor records a visible `Processing failed: ...` message on that row and
continues sending the rest of the queue.

This prevents one malformed notification, stale related request, or unexpected
SMTP path from leaving every later email stuck in `Pending`.

The TV episode path also no longer shadows the module-level `settings` object
inside the poster lookup block. On v2 this could raise before any ready episode
notification was sent.

### Processor diagnostics

The background notification worker now logs full stack traces for processor
level failures. This makes production log review much more useful when a queue
run fails before it reaches row-level handling.

### Admin shell cache refresh

The service-worker cache name was bumped so browsers refresh the cached admin
dashboard shell after upgrading.

---

## Release Prep

Static and regression validation passed with:

```bash
python3 -m compileall app
node --check app/static/service-worker.js
.venv/bin/python -m unittest tests.test_email_processor
git diff --check
```

The v2.3.6 branch was created from `main` at `v2.3.5` to avoid shipping the
separate v3 staging work.

The required production container drift check could not run in this workspace
because `prod-files.txt` is not present. Capture the production file inventory
and run the check before tagging:

```bash
./scripts/check_prod_drift.sh prod-files.txt
```

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.3.6|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, confirm the dashboard footer shows `v2.3.6`. Then either wait
for the next notification processor interval or click **Process Notifications**
from the admin notifications view to drain ready rows.
