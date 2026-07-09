# BingeAlert v2.3.3 - operations polish and Pushover alerts

Release focused on the admin operations experience: cleaner Settings navigation,
per-section saves, real notification batching controls, safer service-health
alert routing, and optional Pushover push alerts.

No database migration is added in this release. Installations upgrading from
before `v2.3.1` still need the `0005_notification_delivery_log` migration.

---

## Added

### Pushover alert provider

BingeAlert can now route admin/operator alerts through Pushover. When selected
under **Settings -> Operations Automation -> Alert Webhook**, Pushover can
receive:

- service-health failures and recoveries
- new episode availability events
- new movie availability events
- Seerr issues reported
- Seerr issues resolved

The Settings UI includes fields for the Pushover app token, user/group key, and
optional sound, plus a **Send Test Push** button. Each Pushover field has an
inline `i` help tooltip explaining where to get the required value.

### Operations and alerting configuration examples

`README.md` now documents Operations Automation, notification batching, and
Pushover setup. `.env.example` now includes the service-health, alert-provider,
Pushover, notification-retention, backup-schedule, and batching environment
variables for config-file/IaC installs.

---

## Changed

### Settings page cleanup

Settings is now split into anchored sections with a side navigation and
section-level save buttons. The previous single save button is still available
as **Save All**, but day-to-day updates can be saved from the section being
edited.

### Notification batching settings now apply

The dashboard timing controls now feed the runtime behavior:

- initial availability delay
- batching extension delay
- maximum batching wait
- notification processor check frequency

This makes the visible Settings values match the worker behavior instead of
showing static defaults.

### Service-health alert routing

SMTP health failures no longer try to send an email through SMTP. SMTP outages
stay visible in System Health and can still route through webhook/Pushover
providers.

### Admin card hover behavior

The summary cards no longer translate upward on hover, avoiding the clipped
top-row effect on smaller or tighter viewports.

---

## Fixed

### GitHub CodeQL stack-trace exposure alert

Config validation no longer returns raw exception text or service probe error
details in the validation response. Detailed errors stay in logs/System Health,
while the admin validation UI receives generic, user-safe messages.

---

## Release Prep

Static validation passed with:

```bash
python3 -m py_compile app/__init__.py app/config.py app/main.py app/background/system_health.py app/routers/admin.py app/routers/webhooks.py app/services/email_service.py app/services/pushover_service.py
node -e '... admin.html script parse ...'
node -e '... settings div balance check ...'
git diff --check
```

The required prod drift check must pass before tagging:

```bash
./scripts/check_prod_drift.sh prod-files.txt
```

In this workspace, `prod-files.txt` is not present and the Docker CLI is not
available, so the tag/GitHub release should wait until the production file list
is captured from the running container and checked.

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.3.3|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, confirm the dashboard footer shows `v2.3.3`.
