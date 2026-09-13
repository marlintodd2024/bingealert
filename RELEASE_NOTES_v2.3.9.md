# BingeAlert v2.3.9 - Maintainerr test connection status

Hotfix release for the Maintainerr integration status introduced in `v2.3.8`.
Maintainerr test notifications are now recognized as successful connection
checks instead of being shown as ignored cleanup events.

No database migration is added in this release.

---

## Fixed

### Maintainerr Test Connection no longer shows Needs Attention

Maintainerr's **Test Connection** action sends its dedicated test notification
type without any media items. BingeAlert previously treated every event other
than `Media Handled` as a warning, so a successful connectivity test appeared
as:

```text
Maintainerr webhook received an event that was not Media Handled
```

BingeAlert now recognizes Maintainerr's numeric and symbolic test-notification
forms and records them as **Connection verified**. A test event never suppresses
a request or cancels a notification; only a real `Media Handled` event can
perform cleanup.

Activity rows created by `v2.3.8` for `TEST_NOTIFICATION` are reclassified when
read, so the settings status corrects itself immediately after upgrading without
a database migration or another test.

The settings instructions now explain the difference between testing delivery
and validating a real cleanup. Unresolved `{{notification_type}}` template text
continues to be treated as a warning because it would prevent real cleanup
events from being identified.

---

## Release Prep

Validation passed with:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pip-audit -r requirements.txt --progress-spinner off
node --check app/static/service-worker.js
git diff --check
```

The tag-triggered package workflow publishes the GHCR image for `linux/amd64`
and `linux/arm64` with `2.3.9`, `2.3`, and `latest` tags.

---

## Upgrade

```bash
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, open **Settings → Maintainerr**. The previous test event should
show **Connection verified**. A later real cleanup will replace it with the
normal processed-event counts.
