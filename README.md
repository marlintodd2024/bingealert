# 📬 BingeAlert

A self-hosted notification service for Plex media servers that watches **Jellyseerr/Overseerr**, **Sonarr**, and **Radarr** webhooks and sends polished, timely email alerts when requested content is actually playable in Plex.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![GHCR](https://img.shields.io/badge/ghcr.io-published-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)

> **v2.0** ships a single-container install with SQLite, a first-run setup wizard, and required auth by default. If you're upgrading from v1.5.x, see [Migrating from v1](#migrating-from-v1).

---

## Why BingeAlert?

Your friends request movies and shows through Seerr. Sonarr and Radarr download them. But nobody knows when their stuff is actually ready — Plex's built-in notifications are flaky, Seerr's are basic, and you keep getting "is my show ready yet?" pings.

BingeAlert sits between your media stack and your users. It listens to every webhook, waits for Plex to actually index the file, then sends a polished email with a deep link. It also handles the messy edge cases — stuck downloads, failed imports, unreleased content, wrong-quality grabs, and reported issues — so you're not babysitting the stack.

---

## Features

- **Smart email notifications** — HTML emails with TMDB posters and Plex deep links. Episodes from the same show are batched into one email.
- **Plex availability check** — Notifications wait until Plex has actually indexed the file, with retry/backoff.
- **Quality & release monitoring** — "Coming Soon" emails for unreleased content; "Quality Waiting" emails when a grab doesn't match the quality profile. Cancelled automatically when a real download starts.
- **Import failure auto-fix** — When Sonarr/Radarr import fails, the bad release is blocklisted and re-searched. Admin email when it happens.
- **Issue auto-fix** — Issues reported in Seerr (bad audio, wrong subs, corrupted file) trigger a blacklist + re-search. Configurable as manual review, full auto, or auto-with-notification.
- **Stuck download detection** — Background worker every 30 min; TBA episode titles are auto-fixed by refreshing metadata, true stalls trigger an admin alert.
- **Shared requests** — Multiple users on a single request all get notified.
- **Anime routing** — Auto-detects anime via TMDB metadata and routes to a dedicated Sonarr instance.
- **Maintenance windows** — Schedule downtime with announcement, reminder, and completion emails. Pauses background workers automatically.
- **Reconciliation** — Catches missed webhooks every 2h.
- **Weekly summary** — Sundays 9am UTC.
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

1. **Easiest:** edit `./data/config.json` directly, then `docker compose restart`.
2. **Re-run the wizard:** `rm ./data/config.json && docker compose restart` — your DB and notification history are preserved; the wizard runs again.

### Optional environment-variable fallback

If you'd rather pre-configure without the wizard (for IaC / automated deploys), copy [`.env.example`](.env.example) to `.env`, fill in the values, and uncomment `env_file: .env` in your `docker-compose.ghcr.yml`. The precedence is:

```
/data/config.json   >   environment variables   >   built-in defaults
```

### Auth

Auth is **required by default**. The wizard collects an admin password (bcrypt-hashed), generates an HMAC key for session cookies, and saves both to `config.json`.

By default the local network CIDRs `192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, 127.0.0.0/8` bypass the password — useful for home installs. Narrow them in the wizard if you prefer.

To enable Cloudflare Turnstile on the login page, drop your site/secret keys into `config.json` and restart.

---

## Migrating from v1

If you're running v1.5.x with the Postgres `bingealert-db` container, follow this:

1. **Snapshot prod off-box** — `docker exec bingealert-db pg_dump -U notifyuser notifications > prod.sql`. Also copy your existing `.env`.
2. **Stop v1** — `docker compose down`.
3. **Bring up v2** but **don't open the wizard yet** — just let it create an empty `./data/`.
4. **Stop v2** — `docker compose down`.
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
7. **Bring v2 up** and verify in the admin dashboard that your users / requests / notifications survived.

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
- Webhook routes can be IP-allowlisted via `webhook_allowed_ips` (comma-separated CIDRs in `config.json`).
- The `app_secret_key` HMAC is auto-generated by the wizard and never logged.

---

## License

MIT. See [LICENSE](LICENSE).
