# BingeAlert v2.3.4 - admin UI hotfix

Hotfix release for two admin dashboard issues found after `v2.3.3`.

No database migration is added in this release. Installations upgrading from
before `v2.3.1` still need the `0005_notification_delivery_log` migration.

---

## Fixed

### System Health non-JSON responses

The System Health tab no longer crashes with a browser parser error like:

```text
Unexpected token '<', "<html>..." is not valid JSON
```

The admin UI now asks the endpoint for JSON and validates the response before
parsing. If a proxy, login page, setup page, or HTML error page is returned, the
toast now shows a useful HTTP/content-type diagnostic instead of the raw JSON
parse failure.

### Settings section save buttons

Every editable Settings section now has a visible save button at the bottom of
the section in addition to the header action. This makes the per-section save
flow obvious even in long sections or layouts where the header action is easy to
miss.

The clicked save button now shows its own saving state instead of always
disabling the first matching header button.

### Admin shell cache refresh

The service-worker cache name was bumped so browsers are nudged to refresh the
cached admin shell after upgrading.

---

## Release Prep

Static validation passed with:

```bash
node --check app/static/service-worker.js
node -e '... admin.html script parse ...'
node -e '... settings div balance check ...'
git diff --check
```

The local ASGI smoke test could not run in this workstation environment because
the system Python does not have FastAPI installed.

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.3.4|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, confirm the dashboard footer shows `v2.3.4`. If the old
dashboard shell is still visible, refresh the page once after the container is
back up.
