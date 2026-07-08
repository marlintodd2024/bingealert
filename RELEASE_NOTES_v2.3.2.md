# BingeAlert v2.3.2 - reconciliation ledger fix

Hotfix release for a `v2.3.1` reconciliation regression where duplicate
historical notification rows could trip the new delivery ledger's unique
constraint during backfill.

No new database migration is added in this release. Installations upgrading
from before `v2.3.1` still need the `0005_notification_delivery_log` migration.

---

## Fixed

### Reconciliation delivery-ledger collisions

Delivery ledger writes now use SQLite's conflict-tolerant insert path. If
reconciliation or retention backfill sees duplicate sent notifications for the
same user/request/type/episode, the second insert is treated as "already
recorded" instead of failing the whole reconciliation cycle.

This fixes errors like:

```text
sqlite3.IntegrityError: UNIQUE constraint failed:
notification_delivery_log.user_id,
notification_delivery_log.request_id,
notification_delivery_log.notification_type,
notification_delivery_log.dedupe_key
```

### Activity advanced logs

The raw line-by-line log viewer is now under Activity as `Advanced Logs`, with
the old terminal-style row rendering restored. The standalone Logs tab was
removed, and the top health summary card now says `Service Problems` to avoid
confusing service-health failures with reported media issues.

---

## Release Prep

Static validation passed with:

```bash
python3 -m py_compile app/services/notification_history.py app/background/reconciliation.py app/background/ops_maintenance.py app/services/email_service.py app/routers/admin.py app/routers/webhooks.py app/database.py app/__init__.py
node -e '... admin.html script parse ...'
git diff --check
```

The required prod drift check is still blocked unless `prod-files.txt` is
captured from the production container and copied into the repo root:

```bash
./scripts/check_prod_drift.sh prod-files.txt
```

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.3.2|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, confirm the dashboard footer shows `v2.3.2`.
