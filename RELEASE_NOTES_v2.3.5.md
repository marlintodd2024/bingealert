# BingeAlert v2.3.5 - settings save button cleanup

Hotfix release for duplicated save buttons in the Settings page after `v2.3.4`.

No database migration is added in this release. Installations upgrading from
before `v2.3.1` still need the `0005_notification_delivery_log` migration.

---

## Fixed

### Duplicate per-section save buttons

Settings sections now show a single save action at the bottom of each editable
section. The duplicate header save buttons were removed, while non-save header
actions such as **Validate Config** remain in place.

The save helper also now finds a section's footer save button when called
programmatically, so section saves still show the correct saving state.

### Admin shell cache refresh

The service-worker cache name was bumped again so browsers refresh the cached
admin dashboard shell after upgrading from `v2.3.4`.

---

## Release Prep

Static validation passed with:

```bash
python3 -m py_compile app/__init__.py
node --check app/static/service-worker.js
node -e '... admin.html script parse ...'
node -e '... settings div balance check ...'
git diff --check
```

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.3.5|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, confirm the dashboard footer shows `v2.3.5`. If the old
dashboard shell is still visible, refresh the page once after the container is
back up.
