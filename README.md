# 📬 BingeAlert

A self-hosted notification system for Plex media servers that integrates with **Seerr** (Jellyseerr/Overseerr), **Sonarr**, and **Radarr** to send intelligent email notifications when requested content is ready to watch.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![GHCR](https://img.shields.io/badge/ghcr.io-published-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)

---

## Why BingeAlert?

You run a Plex server. Your friends and family request movies and shows through Seerr. Content downloads through Sonarr and Radarr. But nobody knows when their stuff is actually ready — Plex's built-in notifications are unreliable, Seerr's are basic, and your users keep asking "is my show ready yet?"

BingeAlert fixes this. It sits between your media stack and your users, watching every webhook and sending polished, timely email notifications the moment content hits Plex. It handles the edge cases too — stuck downloads, failed imports, unreleased content, wrong quality files — so you're not babysitting your server.

---

## Features

### 📧 Smart Email Notifications
Beautiful HTML emails with movie posters and direct Plex deep links. Episodes from the same show are batched into a single email (no getting spammed with 10 emails for a season drop). A configurable delay lets Plex finish indexing before the notification goes out, so users never click a link to content that isn't ready yet.

### 🎯 Quality & Release Monitoring
Automatically tracks whether requested content is actually available. If a movie hasn't been released yet, users get a "Coming Soon" email with the release date. If content is available but not in the requested quality profile, users get a "Quality Waiting" email. When a download starts (Grab webhook), quality notifications are automatically cancelled so users aren't spammed.

### 🔧 Import Failure Auto-Fix
When Sonarr or Radarr downloads a file that can't be imported ("no files found are eligible for import"), BingeAlert detects it, removes the bad release from the queue, blocklists it so it won't be grabbed again, and triggers a search for a new release — all automatically. You get an email when it happens.

### 🩺 Issue Auto-Fix
When users report issues in Seerr (bad audio, wrong subtitles, corrupted file), BingeAlert can automatically blacklist the problematic file, trigger a new search in Sonarr/Radarr, and notify the user when the replacement downloads. Configurable as manual review, fully automatic, or automatic with admin notification.

### 🔄 Stuck Download Detection
A background monitor checks Sonarr and Radarr queues every 30 minutes for downloads that are stalled, failed, or stuck with TBA episode titles. TBA issues are auto-fixed by refreshing series metadata. Stuck downloads trigger an admin alert email with details.

### 👥 Shared Requests
Multiple users can be attached to the same request. When content becomes available, everyone on that request gets notified — not just the person who originally requested it.

### 🕵️ Reconciliation
A background worker periodically scans Sonarr and Radarr for content that downloaded but never triggered a webhook notification. Catches anything that slipped through the cracks. Also automatically cleans up stale issues that were never resolved.

### 🧠 Queue-Aware Intelligence
The quality monitor checks whether content is actively in the download queue before sending notifications. If something is downloading (even if stuck), users won't receive a confusing "waiting for quality" email — the stuck download monitor handles it instead.

### 📊 Admin Dashboard
A full web UI with real-time stats, user management, request tracking, notification history, upcoming episode calendar, database backup/restore, configurable settings, and live log streaming. Everything is manageable from the browser.

### 🔐 Security Hardened
Optional authentication with bcrypt password hashing, HMAC-signed session cookies, login rate limiting, local network bypass, Cloudflare Turnstile bot protection, webhook IP allowlisting, and API docs disabled in production. Passed a 16-point security audit with CodeQL scanning enabled.

### 🧙 Setup Wizard
A guided 6-step setup for new installations that walks through connecting Seerr, Sonarr, Radarr, Plex, and SMTP — testing each connection as you go. No manual config file editing required.

### 📬 Weekly Summary
Every Sunday, admins receive a summary email with stats on requests processed, notifications sent, and any issues that need attention.

### 🎬 Request on Behalf
Admins can create requests on behalf of other users directly from the dashboard — paste a Seerr URL, pick a user, and go.

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Seerr (Jellyseerr or Overseerr)
- Sonarr and Radarr
- Plex Media Server
- SMTP email credentials (Gmail App Password, SMTP2GO, etc.)

### Option A: Docker Pull (Recommended)

No cloning needed — just download two files and go:

```bash
# Download the compose file and config template
curl -O https://raw.githubusercontent.com/marlintodd2024/bingealert/main/docker-compose.ghcr.yml
curl -O https://raw.githubusercontent.com/marlintodd2024/bingealert/main/.env.example

# Configure
cp .env.example .env
nano .env   # Fill in your settings

# Start
docker compose -f docker-compose.ghcr.yml up -d
```

### Option B: Build from Source

```bash
git clone https://github.com/marlintodd2024/bingealert.git
cd bingealert
cp .env.example .env
nano .env   # Fill in your settings
docker compose up -d
```

### Setup Wizard

Navigate to `http://your-server:8000` — the setup wizard walks you through connecting all your services and verifying everything works.

### ⚠️ Security Setup (Important)

After installation, take these steps to secure your instance:

1. **Generate a strong secret key** — This signs your session cookies. Never use the default.
   ```bash
   # Generate and add to your .env:
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

2. **Set `ENVIRONMENT=production`** in your `.env` — This disables the Swagger API docs (`/docs`, `/redoc`, `/openapi.json`) which expose your full API schema publicly.

3. **Restrict webhook IPs** — Add `WEBHOOK_ALLOWED_IPS` to your `.env` with the IP address of your Sonarr/Radarr/Seerr server. This prevents unauthorized webhook submissions.
   ```env
   WEBHOOK_ALLOWED_IPS=192.168.1.100
   ```

4. **Enable authentication** — In Settings → Authentication & Security, enable auth and set an admin password. Configure your local network CIDR so LAN connections bypass login.

5. **Use HTTPS** — Place BingeAlert behind a reverse proxy (Nginx Proxy Manager, Traefik, Cloudflare Tunnel) with SSL/TLS. Configure `X-Real-IP` and `X-Forwarded-For` headers.

All security settings are also configurable from the **Settings** tab in the admin dashboard.

> 📖 See [SECURITY.md](SECURITY.md) for the full security guide.

### Configure Webhooks

Set up webhooks in each service pointing to your portal:

#### Seerr (Jellyseerr / Overseerr)
- **Settings → Notifications → Webhook**
- URL: `http://your-server:8000/webhooks/jellyseerr`
- Enable: Media Requested, Media Approved, Media Auto-Approved, Issue Created, Issue Resolved

#### Sonarr
- **Settings → Connect → Add → Webhook**
- URL: `http://your-server:8000/webhooks/sonarr`
- Enable: **On Grab** ✅ and **On Import Complete** ✅

#### Radarr
- **Settings → Connect → Add → Webhook**
- URL: `http://your-server:8000/webhooks/radarr`
- Enable: **On Grab** ✅ and **On File Import** ✅ (not "On Import Complete")

> **Important:** The "On Grab" webhook is required for quality monitoring to work properly — it cancels "Quality Waiting" notifications when a download starts.

### Sync & Test

From the dashboard:
1. Click **Sync Users** and **Sync Requests** to import your existing data
2. Click **Send Test Email** to verify SMTP
3. Request something in Seerr and watch it flow through

### Updating

**Docker Pull users:**
```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

**Build from Source users:**
```bash
git pull
docker compose up -d --build
```

---

## How It Works

```
User requests content in Seerr
        │
        ▼
   Seerr webhook ──→ Portal stores request
        │                    │
        │              Quality check (10s delay)
        │              ├─ Not released → "Coming Soon" email
        │              └─ Wrong quality → "Quality Waiting" email (cancelable)
        │
   Content downloads in Sonarr/Radarr
        │
        ├─ Grab webhook ──→ Cancel quality waiting notification
        │
        └─ Import webhook ──→ Create notification
                                  │
                            7-min batch window
                            (groups episodes)
                                  │
                            Check Plex availability
                                  │
                            Send email ✉️
```

---

## Architecture

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI (Python 3.11) |
| Database | PostgreSQL 15 |
| Frontend | Vanilla HTML/CSS/JS |
| Deployment | Docker + Docker Compose |
| Email | SMTP (any provider) |

---

## Configuration

All settings are configurable from the **Settings** tab in the admin dashboard. Changes are saved to `.env` (for service connections) or the database (for auth, reconciliation, etc.).

### Settings Sections

- **Smart Batching** — Initial delay, extension delay, max wait, check frequency
- **Email / SMTP** — Server, port, credentials, sender info
- **Quality Monitoring** — Enable/disable, check interval, waiting delay
- **Issue Auto-Fix** — Manual, auto, or auto + notify modes
- **Reconciliation** — Check interval, issue fixing/reported/abandon cutoffs
- **Authentication** — Enable/disable, admin password, local network CIDR, session timeout, Cloudflare Turnstile
- **Connected Services** — Seerr, Sonarr, Radarr, Plex URLs and API keys

### Environment Variables

See [`.env.example`](.env.example) for all available options. The minimum required:

```env
DB_PASSWORD=your_password
JELLYSEERR_URL=http://your-seerr:5055
JELLYSEERR_API_KEY=your_key
SONARR_URL=http://your-sonarr:8989
SONARR_API_KEY=your_key
RADARR_URL=http://your-radarr:7878
RADARR_API_KEY=your_key
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=BingeAlert <you@gmail.com>
APP_SECRET_KEY=random_string_here
```

---

## Authentication

Authentication is **off by default**. Enable it in Settings → Authentication & Security.

- **Local network bypass** — Connections from your LAN (e.g. `192.168.1.0/24`) skip login entirely
- **External access** — Requires admin password via a login page
- **Cloudflare Turnstile** — Optional bot protection on the login page (free)
- **Session timeout** — Configurable from 1 hour to 7 days
- **Always public** — Webhook endpoints, health checks, and the setup wizard never require auth

---

## Background Workers

| Worker | Interval | Purpose |
|--------|----------|---------|
| Notification Processor | 60 seconds | Sends queued emails when `send_after` time is reached |
| Reconciliation | 2 hours (configurable) | Catches missed webhooks, resolves stale issues |
| Quality Monitor | 24 hours (configurable) | Checks pending requests for release/quality status |
| Stuck Download Monitor | 30 minutes | Detects TBA titles and stuck downloads |
| Weekly Summary | Sundays 9 AM UTC | Sends activity summary to admin |

---

## API Documentation

Interactive API docs are available in development mode (`ENVIRONMENT=development`):
- Swagger UI: `http://your-server:8000/docs`
- ReDoc: `http://your-server:8000/redoc`

These are disabled by default in production for security.

---

## Updating

```bash
docker-compose down
git pull
docker-compose build --no-cache
docker-compose up -d
```

Database migrations run automatically on startup.

---

## Troubleshooting

**No notifications sending?**
Check Settings → Send Test Email. Verify SMTP credentials. Check the Notifications tab for error messages.

**Webhooks not arriving?**
Check that the portal is reachable from Sonarr/Radarr/Seerr (same Docker network or correct IP). Check logs in the Logs tab.

**Quality notifications not canceling?**
Verify "On Grab" is enabled in both Sonarr and Radarr webhook settings.

**Users not appearing?**
Click Sync Users on the dashboard. Check the Seerr API key in Settings.

**Stale issues not resolving?**
The reconciliation worker handles this. Check Settings → Reconciliation to see/adjust the intervals. You can also trigger it manually from the dashboard.

---

## Security

BingeAlert includes comprehensive security hardening as of v1.2.0 (16 audit findings resolved). Key protections:

- **Authentication middleware** with bcrypt password hashing and HMAC-signed session cookies
- **Login rate limiting** — 5 attempts per IP per 5-minute window
- **Local network bypass** — Configurable CIDR ranges skip login automatically
- **Webhook IP allowlisting** — Restrict which IPs can submit webhooks via `WEBHOOK_ALLOWED_IPS`
- **API docs disabled in production** — Set `ENVIRONMENT=production` (default)
- **Setup wizard locked** after initial setup is complete
- **No secrets in API responses** — Config endpoint fully masks all keys/passwords
- **No secrets in backups** — Database exports never include `.env`
- **Backup restore validation** — ZIP integrity, path traversal protection, file type allowlisting
- **Generic error messages** — Internal details logged server-side only
- **Cloudflare Turnstile** — Optional bot protection on the login page (free)

For production, always place BingeAlert behind a reverse proxy with HTTPS. See [SECURITY.md](SECURITY.md) for the complete security guide.

---

## Project Structure

```
bingealert/
├── app/
│   ├── main.py              # FastAPI app, lifespan, auth routes
│   ├── auth.py              # Authentication middleware & helpers
│   ├── config.py            # Pydantic settings from .env
│   ├── database.py          # SQLAlchemy models
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── routers/
│   │   ├── webhooks.py      # Seerr/Sonarr/Radarr webhook handlers
│   │   ├── admin.py         # Admin API + config endpoints
│   │   ├── health.py        # Health check
│   │   └── sse.py           # Server-sent events for live updates
│   ├── services/
│   │   ├── email_service.py # Email rendering + SMTP
│   │   ├── jellyseerr_sync.py
│   │   ├── seerr_service.py # Seerr issue resolution API
│   │   ├── sonarr_service.py
│   │   ├── radarr_service.py
│   │   ├── plex_service.py
│   │   └── tmdb_service.py  # Poster fetching via Seerr
│   ├── background/
│   │   ├── reconciliation.py
│   │   ├── quality_monitor.py
│   │   ├── stuck_monitor.py
│   │   └── weekly_summary.py
│   └── static/
│       ├── admin.html       # Admin dashboard
│       ├── login.html       # Login page
│       └── setup.html       # Setup wizard
├── alembic/                 # Database migrations
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Acknowledgments

Built with [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://www.sqlalchemy.org/), and [PostgreSQL](https://www.postgresql.org/).

Integrates with [Jellyseerr](https://github.com/Fallenbagel/jellyseerr) / [Overseerr](https://overseerr.dev/), [Sonarr](https://sonarr.tv/), [Radarr](https://radarr.video/), and [Plex](https://www.plex.tv/).
