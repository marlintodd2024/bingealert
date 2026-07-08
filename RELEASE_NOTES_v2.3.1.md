# BingeAlert v2.3.1 - notification dedupe hotfix

Hotfix release for duplicate availability emails after sent-notification
retention cleanup.

This release includes a database migration: `0005_notification_delivery_log`.

---

## Fixed

### Retention-safe notification dedupe

Sent notification rows are no longer the only durable record used to decide
whether a movie or episode availability email was already delivered. Successful
movie and episode sends now record compact rows in `notification_delivery_log`,
which notification retention does not purge.

### Purge then sync duplicate blast

The notification purge job backfills the delivery ledger before deleting old
sent notification rows. Reconciliation also backfills before checking for
missed notifications, so existing sent history is preserved as dedupe state.

### Episode batching fingerprints

Sonarr availability webhooks now queue one notification row per episode, while
the email processor can still batch those rows into a single email. This keeps
the stored dedupe fingerprint per-episode instead of relying on a grouped
subject like `New Episodes: ...`.

### Reconciliation guardrail

Reconciliation now refuses to manufacture availability emails for stale
downloaded backlog outside the configured notification lookback window. It
marks those items handled/available instead of emailing old content after
history cleanup. The lookback is exposed in Settings under Reconciliation &
Issue Cleanup and defaults to the notification retention window.

---

## Release Prep

Static validation passed with:

```bash
python3 -m py_compile app/database.py app/services/notification_history.py app/background/ops_maintenance.py app/services/email_service.py app/routers/webhooks.py app/background/reconciliation.py app/routers/admin.py alembic/versions/0005_notification_delivery_log.py
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
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.3.1|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, run the database migration and confirm the dashboard footer
shows `v2.3.1`.
