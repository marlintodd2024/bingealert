# 📬 BingeAlert

A self-hosted notification service for Plex media servers that watches **Jellyseerr/Overseerr**, **Sonarr**, and **Radarr** webhooks and sends polished, timely email alerts when requested content is actually playable in Plex.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![GHCR](https://img.shields.io/badge/ghcr.io-published-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.138-009688.svg)

> **v3.0** turns BingeAlert into a Plex request operations dashboard while keeping the single-container SQLite install, first-run setup wizard, and required auth defaults introduced in v2. If you're upgrading from v1.5.x, see [Migrating from v1](#migrating-from-v1).

---

## Why BingeAlert?

Your friends request movies and shows through Seerr. Sonarr and Radarr download them. But nobody knows when their stuff is actually ready — Plex's built-in notifications are flaky, Seerr's are basic, and you keep getting "is my show ready yet?" pings.

BingeAlert sits between your media stack and your users. It listens to every webhook, waits for Plex to actually index the file, then sends a polished email with a deep link. It also handles the messy edge cases — stuck downloads, failed imports, unreleased content, wrong-quality grabs, and reported issues — so you're not babysitting the stack.

With the v3.0 ops cockpit work, BingeAlert is also the place an admin can answer: what happened after the request, what is still waiting, which notifications failed, and which connected services need attention.

## v3 at a glance

![BingeAlert v3 Daily Admin Home](docs/screenshots/v3-admin-home.png)

The Daily Admin Home keeps the current operating picture and prioritized action queue together without turning the dashboard into a wall of alerts.

![BingeAlert v3 mobile user status portal](docs/screenshots/v3-user-status-mobile.png)

Each user gets a revocable mobile-friendly status page for requests, notification history, calendar access, issue reporting, and delivery preferences.

---

## Features

- **Smart email notifications** — HTML emails with TMDB posters and Plex deep links. Episodes from the same show are batched into one email.
- **Plex availability check** — Notifications wait until Plex has actually indexed the file, with retry/backoff.
- **Daily Admin Home** — One screen for fulfilled requests, new requests, notification risk, open issues, unhealthy services, and worker problems.
- **Request timeline** — Per-request history across Seerr, Sonarr/Radarr, Plex checks, notification queueing, sends, issue reports, and replayed webhooks.
- **Webhook inbox and replay** — Searchable Sonarr/Radarr/Seerr webhook history with sanitized payload viewing and guarded replay for missed or failed events.
- **Reports tab** — Daily trend, weekly operations report, top requesters, recurring failures, oldest waiting requests, and one-click daily/weekly admin report emails.
- **Quality & release monitoring** — "Coming Soon" emails for unreleased content; "Quality Waiting" emails when a grab doesn't match the quality profile. Cancelled automatically when a real download starts.
- **Import failure auto-fix** — When Sonarr/Radarr import fails, the bad release is blocklisted and re-searched. Admin email when it happens.
- **Issue auto-fix** — Issues reported in Seerr (bad audio, wrong subs, corrupted file) trigger a blacklist + re-search. Configurable as manual review, full auto, or auto-with-notification.
- **Stuck download detection** — Background worker every 30 min; TBA episode titles are auto-fixed by refreshing metadata, true stalls trigger an admin alert.
- **System and ops health** — Periodic checks for Plex, Seerr, Sonarr, Radarr, SMTP, workers, acquisition queues, root-folder storage, and import-to-Plex lag.
- **Pushover push alerts** — Optional admin push notifications for service-health events, availability events, and Seerr issue activity.
- **User status portal** — Revocable magic-link page where users can see request status and history, subscribe to their calendar, and choose instant or daily digest delivery, quiet hours, full-season waits, and quality updates.
- **Shared requests** — Multiple users on a single request all get notified.
- **Anime routing** — Auto-detects anime via TMDB metadata and routes to a dedicated Sonarr instance.
- **Maintenance windows** — Schedule downtime with announcement, reminder, and completion emails. Pauses background workers automatically.
- **Reconciliation** — Catches missed webhooks every 2h.
- **Operations automation** — Configurable notification batching, daily admin and user digests, sent-notification and webhook-history retention, scheduled backups, and focused per-section settings saves.
- **Daily and weekly admin reports** — One-click sends from Reports, optional scheduled daily admin delivery, and a scheduled Sunday report covering request volume, fulfillment time, notification delivery, top requesters, and recurring failures.
- **First-run wizard** — Web-based setup; no `.env` editing required.
- **Required auth by default** — bcrypt password + HMAC session cookie + local-network CIDR bypass + optional Cloudflare Turnstile.
- **PWA** — Installable web app with mobile-friendly admin dashboard.

---

## Quick start

### Requirements

- Docker + Docker Compose
- Reachable URLs + API keys for **Jellyseerr/Overseerr**, **Sonarr**, and **Radarr**
- An SMTP relay (Gmail App Password, SMTP2GO, your provider, etc.)
- Optionally: Plex `X-Plex-Token` (used for the availability check), a second Sonarr instance for anime, Cloudflare Turnstile keys

### Install

```bash
mkdir -p bingealert && cd bingealert

# Grab the published compose file
curl -O https://raw.githubusercontent.com/marlintodd2024/bingealert/main/docker-compose.ghcr.yml

# Create the data directory and chown it for the non-root container user
mkdir -p data && sudo chown -R 1000:1000 data

# Pass the host's docker group GID so the container can read docker.sock
# (powers the admin dashboard's Logs tab; skip if you don't need it).
export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)

# Bring it up
docker compose -f docker-compose.ghcr.yml up -d
```

Open `http://your-host:8000`. The setup wizard runs on first boot; fill in six steps and click **Save & Start**. The container restarts and lands you on the login page.

### What lives where

```
./data/
├── bingealert.db       # SQLite -- all your tracked requests, episodes, notifications
├── bingealert.db-wal   # SQLite write-ahead log (don't delete)
├── bingealert.db-shm   # SQLite shared memory (don't delete)
└── config.json         # Settings written by the wizard. Edit + restart to change.
```

The `./data` directory is your full backup target — copy it somewhere safe.

---

## Quick demo path

For a first walkthrough after setup:

1. Open **Admin → Health** and run **Check Now** to verify Plex, Seerr, Sonarr, Radarr, SMTP, workers, queue health, storage, and import-to-Plex lag.
2. Open **Admin → Reports** to review the 7-day operations report: request volume, fulfillment time, notification delivery, top requesters, recurring failures, and oldest waiting requests.
3. Open any request in **Admin → Requests** and click **Timeline** to see the end-to-end path from Seerr request to webhook, Plex availability check, notification queue, and delivery.
4. Open **Admin → Webhooks** to inspect recent Sonarr/Radarr/Seerr events and replay safe failed events.
5. Send a **Daily Digest** or **Weekly Report** from the Reports tab to see the admin-facing email summary.

This is the v3.0 pitch: your Plex request ops dashboard, built to show what happened after the request and debug notification gaps in one place.

---

## Webhook configuration

Once the wizard is done, configure your upstream services to POST here:

| Service | URL on your BingeAlert host |
|---|---|
| Jellyseerr / Overseerr | `http://YOUR_HOST:8000/webhooks/jellyseerr` |
| Sonarr (Connect → Webhook → On Grab + On Import Complete) | `http://YOUR_HOST:8000/webhooks/sonarr` |
| Radarr (Connect → Webhook → On Grab + On File Import) | `http://YOUR_HOST:8000/webhooks/radarr` |

> **Important:** Radarr's setting is **"On File Import"**, not "On Import Complete". They're different events.

If you have multiple Sonarr instances (anime), point both to the same `/webhooks/sonarr` URL — BingeAlert routes by the payload's series metadata.

---

## Configuration

All configuration lives in `./data/config.json`, written by the setup wizard. To change settings post-install:

1. **Easiest:** use **Admin → Settings**. Pick one section, save it from the section header, then restart the container for settings that require a reload.
2. Edit `./data/config.json` directly, then `docker compose restart`.
3. Re-run the wizard with `rm ./data/config.json && docker compose restart` — your DB and notification history are preserved.

### Optional environment-variable fallback

If you'd rather pre-configure without the wizard (for IaC / automated deploys), copy [`.env.example`](.env.example) to `.env`, fill in the values, and uncomment `env_file: .env` in your `docker-compose.ghcr.yml`. The precedence is:

```
/data/config.json   >   environment variables   >   built-in defaults
```

### Auth

Auth is **required by default**. The wizard collects an admin password (bcrypt-hashed), generates an HMAC key for session cookies, and saves both to `config.json`.

By default the local network CIDRs `192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, 127.0.0.0/8` bypass the password — useful for home installs. Narrow them in the wizard if you prefer.

To enable Cloudflare Turnstile on the login page, drop your site/secret keys into `config.json` and restart.

### Operations automation

The admin dashboard's **Settings** tab shows one selected section at a time. Each editable section has one save button in its header, so operations, batching, email, quality, issue handling, anime routing, reconciliation, auth, security, and connection settings no longer form one long page.

Under **Settings → Operations Automation** you can configure:

- **Service Health** — periodic reachability checks for Plex, Seerr, Sonarr, Radarr, SMTP, and supporting services. SMTP failures are shown in System Health and can route to webhooks/Pushover, but BingeAlert does not try to email you through SMTP when SMTP itself is down.
- **Alert Webhook** — generic JSON, Discord, Slack, or Pushover alert delivery for admin/operator alerts.
- **Digest Delivery** — optional daily admin operations email plus the UTC delivery hour used by users who select Digest.
- **Retention & Backups** — automatic sent-notification cleanup, 30-day sanitized webhook-history retention by default, and scheduled local backups.

Under **Settings → Notification Batching** you can tune the initial availability delay, batching extension window, maximum wait, and processor check frequency. These settings control how long BingeAlert waits for nearby episode imports before sending a consolidated notification.

User digest and full-season preferences are enforced by a dedicated worker. Full-season delivery waits until Sonarr reports that every monitored episode in that season has a file; quiet hours are checked again immediately before the grouped email is sent.

### Pushover push alerts

BingeAlert can send operator push notifications through Pushover for service-health failures/recoveries, new episode/movie availability, and Seerr issues reported/resolved. This is an admin alert channel; user-facing availability emails still go through SMTP.

1. Create or log into your Pushover account and make sure the Pushover app is installed on the devices that should receive BingeAlert alerts.
2. Register a Pushover application at [pushover.net/apps/build](https://pushover.net/apps/build). Name it `BingeAlert` and optionally upload an icon. Copy the generated **Application API Token**.
3. Copy your **User Key** from the Pushover dashboard, or use a Pushover group key if multiple operators should receive the same alerts.
4. In BingeAlert, open **Admin → Settings → Operations Automation**.
5. Under **Alert Webhook**, enable **Webhook Alerts**, set **Alert Provider** to **Pushover**, then paste:
   - **Pushover App Token** = the application API token from step 2.
   - **Pushover User/Group Key** = the user or group key from step 3.
   - **Pushover Sound** = optional sound name such as `pushover`, `siren`, or `none`.
6. Hover or focus the small `i` beside each Pushover field if you need an in-app reminder of where to get the value.
7. Click **Send Test Push**. You should receive `BingeAlert test push`.
8. Click **Save Operations**, then restart the container so the running settings singleton reloads.

For config-file or environment-based installs, the relevant keys are:

```json
{
  "alert_webhook_enabled": true,
  "alert_webhook_type": "pushover",
  "pushover_app_token": "APP_TOKEN_FROM_PUSHOVER",
  "pushover_user_key": "USER_OR_GROUP_KEY_FROM_PUSHOVER",
  "pushover_sound": "pushover"
}
```

Equivalent `.env` names are `ALERT_WEBHOOK_ENABLED`, `ALERT_WEBHOOK_TYPE`, `PUSHOVER_APP_TOKEN`, `PUSHOVER_USER_KEY`, and `PUSHOVER_SOUND`.

Pushover treats application tokens and user/group keys as private secrets. Leave saved secret fields blank in the BingeAlert UI unless you are replacing them.

---

## How it fits with your Plex stack

| Tool | What it is great at | Where BingeAlert fits |
|---|---|---|
| Seerr / Overseerr / Jellyseerr | Request intake, approvals, and user-facing request discovery | Tracks what happens after approval, verifies Plex availability, sends richer ready emails, mirrors reported issues, and gives admins timelines/reporting. |
| Sonarr / Radarr | Grabs, imports, quality profiles, and library management | Consumes grab/import/failure webhooks, detects queue/storage/import problems, and links those events back to the requesting user. |
| Tautulli | Plex watch history, stream monitoring, and user playback analytics | BingeAlert focuses before playback: requests, downloads, imports, availability, notifications, and issue remediation. |
| Notifiarr / general alert hubs | Broad homelab alert routing across many apps | BingeAlert is narrower and request-aware: alerts are tied to media requests, users, notifications, webhooks, and Plex availability. |

BingeAlert is not trying to replace those tools. It fills the gap between "someone requested something" and "the right person knows it is actually ready in Plex."

---

## Migrating from v1

If you're running v1.5.x with the Postgres `bingealert-db` container, follow this:

1. **Snapshot prod off-box** — `docker exec bingealert-db pg_dump -U notifyuser notifications > prod.sql`. Also copy your existing `.env`.
2. **Stop v1** — `docker compose down`.
3. **Bring up the current BingeAlert container** but **don't open the wizard yet** — just let it create an empty `./data/`.
4. **Stop the container** — `docker compose down`.
5. **Run the migration script** to copy your Postgres data into a fresh SQLite at `./data/bingealert.db`:

   ```bash
   pip install psycopg2-binary sqlalchemy alembic
   rm ./data/bingealert.db   # the script refuses to overwrite
   python scripts/migrate_from_v1.py \
     --postgres "postgresql://notifyuser:PASSWORD@HOST:5432/notifications" \
     --sqlite ./data/bingealert.db
   ```

   Expect to see `OK` per table and a `TOTAL` row that matches between source and destination.
6. **Re-run the wizard** to populate `./data/config.json` from your old `.env` values, OR copy `.env` and `env_file: .env` it through Docker Compose.
7. **Bring BingeAlert up** and verify in the admin dashboard that your users / requests / notifications survived.

---

## Editing settings post-install

Three options, in order of convenience:

1. **Admin dashboard → Settings tab.** `POST /admin/config` writes through to `./data/config.json`. Restart the container after saving so the in-memory `settings` singleton reloads.
2. **Edit `./data/config.json` directly**, then `docker compose restart`.
3. **Re-run the wizard:** `rm ./data/config.json && docker compose restart`. Your DB and notification history are preserved.

`/admin/logs` and `/admin/logs/stream` work via the Docker SDK reading `/var/run/docker.sock`. The compose file already mounts the socket — if you removed that mount, those endpoints return 503 with a clear message and you can use `docker logs bingealert -f` from the host instead.

---

## Backup & restore

`./data/` is the entire app state. To back up:

```bash
docker compose stop bingealert
tar -czf bingealert-$(date +%F).tar.gz ./data/
docker compose start bingealert
```

The admin dashboard's **Backups** tab does the same internally — backups land in `./data/backups/`.

---

## Development

```bash
git clone https://github.com/marlintodd2024/bingealert.git
cd bingealert
docker compose up --build       # uses ./docker-compose.yml
```

The dev compose bind-mounts `./app` and `./alembic` into the container, so edits hot-reload through `uvicorn --reload` (set `ENVIRONMENT=development` in `.env`).

CI builds the image on every push and PR — see [`.github/workflows/build-check.yml`](.github/workflows/build-check.yml).

---

## Security

Issues should be reported privately to the address in [SECURITY.md](SECURITY.md). Public-facing notes:

- API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled when `ENVIRONMENT=production` (the default).
- Webhook routes can be IP-allowlisted via `webhook_allowed_ips` and can require a shared `webhook_secret`; send the secret as `X-BingeAlert-Webhook-Secret`, `X-Webhook-Secret`, or `?token=...`.
- Reverse-proxy client-IP headers are trusted only when the immediate peer matches `trusted_proxy_cidrs`, preventing direct clients from spoofing the local-network auth bypass.
- The `app_secret_key` HMAC is auto-generated by the wizard and never logged.

---

## License

MIT. See [LICENSE](LICENSE).
