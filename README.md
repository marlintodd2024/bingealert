# 📬 Plex Notification Portal

A self-hosted notification system for Plex media servers that integrates with **Seerr** (Jellyseerr/Overseerr), **Sonarr**, and **Radarr** to send intelligent email notifications when requested content is ready to watch.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![GHCR](https://img.shields.io/badge/ghcr.io-published-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)

---

## Why?

Plex and Seerr don't do a great job of telling users when their requested content is actually ready. This portal bridges that gap — when someone requests a movie or show, they get a polished email the moment it's available in Plex. No more "is my show ready yet?" messages.

---

## Features

**Smart Notifications** — Episodes are batched into a single email (no spam), with a configurable delay to let Plex index the content first. Movies and TV are handled separately with beautiful HTML emails that include posters and direct Plex links.

**Quality & Release Monitoring** — Automatically detects when requested content isn't released yet ("Coming Soon" emails) or isn't available in the requested quality ("Quality Waiting" emails). Grab webhooks cancel quality alerts when downloads begin.

**Issue Auto-Fix** — When users report issues in Seerr (bad audio, wrong subtitles, etc.), the portal can automatically blacklist the file, trigger a new search, and notify the user when the replacement downloads.

**Shared Requests** — Multiple users can be attached to the same request. Everyone gets notified when the content is ready.

**Reconciliation** — A background worker catches missed webhooks by periodically scanning Sonarr/Radarr for content that downloaded but never triggered a notification. Also cleans up stale issues.

**Authentication** — Optional password protection for external access with Cloudflare Turnstile bot protection. Local network connections bypass login automatically.

**Admin Dashboard** — Full web UI for managing users, requests, notifications, issues, upcoming episodes, backups, settings, and real-time logs.

**Setup Wizard** — A guided 6-step setup for new installations that tests each connection as you go.

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
curl -O https://raw.githubusercontent.com/marlintodd/plex-notification-portal/main/docker-compose.ghcr.yml
curl -O https://raw.githubusercontent.com/marlintodd/plex-notification-portal/main/.env.example

# Configure
cp .env.example .env
nano .env   # Fill in your settings

# Start
docker compose -f docker-compose.ghcr.yml up -d
```

### Option B: Build from Source

```bash
git clone https://github.com/marlintodd/plex-notification-portal.git
cd plex-notification-portal
cp .env.example .env
nano .env   # Fill in your settings
docker compose up -d
```

### Setup Wizard

Navigate to `http://your-server:8000` — the setup wizard walks you through connecting all your services and verifying everything works.

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
SMTP_FROM=Plex Notifications <you@gmail.com>
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

Interactive API docs are available at:
- Swagger UI: `http://your-server:8000/docs`
- ReDoc: `http://your-server:8000/redoc`

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

- API keys and passwords stored in `.env` (not in database)
- Passwords displayed as masked values in the settings UI — saving preserves originals
- Auth passwords hashed with bcrypt in the database
- Session tokens are HMAC-signed with your `APP_SECRET_KEY`
- Docker socket mounted read-only (for container restart feature)
- Webhook endpoints are always public (required for Sonarr/Radarr/Seerr)

For production, consider placing the portal behind a reverse proxy (nginx, Traefik, Cloudflare Tunnel) for HTTPS.

---

## Project Structure

```
plex-notification-portal/
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
