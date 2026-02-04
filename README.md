# Plex Notification Portal

A notification service that monitors Sonarr/Radarr downloads and sends email notifications to users when their requested content becomes available in Plex.

## Features

- 📺 **Episode-by-episode notifications** - Get notified as each new TV episode becomes available
- 🎬 **Movie notifications** - Instant alerts when requested movies are ready
- 👥 **User management** - Automatically syncs users from Jellyseerr
- 📧 **Email notifications** - Beautiful HTML email templates
- 🔄 **Automatic syncing** - Keeps track of all user requests and content availability
- 🎯 **Smart tracking** - Prevents duplicate notifications and tracks notification history
- 📥 **Existing episode import** - Automatically imports already-downloaded episodes from Sonarr to prevent notification spam for old episodes
- 🎛️ **Web Admin Dashboard** - Beautiful web interface to manage users, requests, and notifications

## How It Works

1. **Jellyseerr** provides user data and tracks content requests
2. **Sonarr** sends webhooks when TV episodes are downloaded
3. **Radarr** sends webhooks when movies are downloaded
4. **Notification Portal** matches downloads to user requests and sends email notifications

### Existing Episode Import

When you sync requests from Jellyseerr, the system automatically:

1. **Checks Sonarr** for each TV show request to see what episodes are already downloaded
2. **Creates tracking records** for existing episodes marked as "already notified" 
3. **Prevents spam** by not sending notifications for episodes that were downloaded before the portal was set up
4. **Only notifies** about NEW episodes that download after the sync

**Example scenario:**
- User requests "Breaking Bad" Season 1 in Jellyseerr (6 months ago)
- Episodes 1-8 are already in Sonarr/Plex
- You set up the notification portal today
- Portal imports episodes 1-8 as "already notified"
- Episode 9 downloads tomorrow → User gets an email notification ✅
- No spam about old episodes 1-8 ❌

## Architecture

```
Jellyseerr (Users & Requests) 
    ↓
Notification Portal API
    ↓
PostgreSQL Database
    ↑
Sonarr/Radarr (Webhooks)
    ↓
Email Notifications → Users
```

## Prerequisites

- Docker & Docker Compose
- Jellyseerr instance (with API access)
- Sonarr instance (with API access)
- Radarr instance (with API access)
- SMTP email server (Gmail, Outlook, etc.)

## Quick Start

### 1. Clone and Configure

```bash
# Clone the repository
git clone <your-repo-url>
cd plex-notification-portal

# Copy environment template
cp .env.example .env

# Edit .env with your configuration
nano .env
```

### 2. Configure Environment Variables

Edit `.env` with your settings:

```env
# Database
DB_PASSWORD=your_secure_password

# Jellyseerr
JELLYSEERR_URL=http://jellyseerr:5055
JELLYSEERR_API_KEY=your_api_key_here

# Sonarr
SONARR_URL=http://sonarr:8989
SONARR_API_KEY=your_api_key_here

# Radarr
RADARR_URL=http://radarr:7878
RADARR_API_KEY=your_api_key_here

# Email (Gmail example)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=Plex Notifications <your_email@gmail.com>

# Application
APP_SECRET_KEY=generate_a_long_random_string
```

#### Getting API Keys

**Jellyseerr:**
1. Go to Settings → General
2. Copy your API Key

**Sonarr:**
1. Go to Settings → General → Security
2. Copy your API Key

**Radarr:**
1. Go to Settings → General → Security
2. Copy your API Key

**Gmail App Password:**
1. Enable 2-Factor Authentication on your Google account
2. Go to https://myaccount.google.com/apppasswords
3. Generate an app password for "Mail"
4. Use this password in SMTP_PASSWORD

### 3. Start the Application

```bash
# Build and start services
docker-compose up -d

# Check logs
docker-compose logs -f api

# Check health
curl http://localhost:8000/health
```

### 4. Configure Webhooks in Sonarr/Radarr

#### Sonarr Webhook Setup

1. Go to **Settings → Connect**
2. Click the **+** button
3. Select **Webhook**
4. Configure:
   - **Name:** Plex Notification Portal
   - **Notification Triggers:** ✅ On Download
   - **URL:** `http://your-server-ip:8000/webhooks/sonarr`
   - **Method:** POST
5. Click **Test** then **Save**

#### Radarr Webhook Setup

1. Go to **Settings → Connect**
2. Click the **+** button
3. Select **Webhook**
4. Configure:
   - **Name:** Plex Notification Portal
   - **Notification Triggers:** ✅ On Download
   - **URL:** `http://your-server-ip:8000/webhooks/radarr`
   - **Method:** POST
5. Click **Test** then **Save**

## API Endpoints

### Webhooks (Automatic)
- `POST /webhooks/sonarr` - Receives Sonarr download events
- `POST /webhooks/radarr` - Receives Radarr download events

### Admin (Manual Operations)
- `POST /admin/sync/users` - Manually sync users from Jellyseerr
- `POST /admin/sync/requests` - Manually sync requests from Jellyseerr (also imports existing episodes)
- `POST /admin/requests/{request_id}/import-episodes` - Import existing episodes for a specific TV show request
- `POST /admin/import-all-existing-episodes` - Import existing episodes for ALL TV show requests
- `POST /admin/notifications/process` - Process pending notifications
- `GET /admin/stats` - Get system statistics
- `GET /admin/users` - List all users
- `GET /admin/requests` - List all media requests
- `GET /admin/notifications` - List notifications

### Health
- `GET /health` - Health check and service status

### Documentation
- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - Alternative API documentation (ReDoc)

## Usage Examples

### Access the Admin Dashboard

Open your browser and go to:
```
http://localhost:8000/dashboard
```

The dashboard provides:
- Real-time statistics (users, requests, episodes, notifications)
- User management and search
- Request tracking with import episode buttons
- Notification queue monitoring
- One-click sync and import actions
- Search and filter capabilities

### Manual Sync

```bash
# Sync users from Jellyseerr
curl -X POST http://localhost:8000/admin/sync/users

# Sync requests from Jellyseerr (automatically imports existing episodes)
curl -X POST http://localhost:8000/admin/sync/requests

# Import existing episodes for ALL TV shows (useful for initial setup)
curl -X POST http://localhost:8000/admin/import-all-existing-episodes

# Import existing episodes for a specific request (replace 123 with request ID)
curl -X POST http://localhost:8000/admin/requests/123/import-episodes

# Process pending notifications
curl -X POST http://localhost:8000/admin/notifications/process
```

### View Statistics

```bash
curl http://localhost:8000/admin/stats
```

### Test Webhooks

```bash
# Test Sonarr webhook
curl -X POST http://localhost:8000/webhooks/sonarr \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "Test",
    "series": {"id": 1, "title": "Test Series", "tmdbId": 12345}
  }'

# Test Radarr webhook
curl -X POST http://localhost:8000/webhooks/radarr \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "Test",
    "movie": {"id": 1, "title": "Test Movie", "tmdbId": 12345}
  }'
```

## Docker Compose Services

- **postgres** - PostgreSQL database (port 5432, internal only)
- **api** - FastAPI application (port 8000)

## Database Schema

### Tables
- **users** - User information from Jellyseerr
- **media_requests** - Content requests from Jellyseerr
- **episode_tracking** - Tracks individual episodes and notification status
- **notifications** - Queue of email notifications

## Monitoring

### View Logs

```bash
# All logs
docker-compose logs -f

# API only
docker-compose logs -f api

# Database only
docker-compose logs -f postgres
```

### Check Service Status

```bash
# Container status
docker-compose ps

# Health check
curl http://localhost:8000/health
```

## Troubleshooting

### Users not syncing
1. Check Jellyseerr URL and API key in `.env`
2. Manually trigger sync: `curl -X POST http://localhost:8000/admin/sync/users`
3. Check logs: `docker-compose logs -f api`

### Webhooks not working
1. Verify webhook URL in Sonarr/Radarr settings
2. Test webhook from Sonarr/Radarr UI
3. Check firewall rules (port 8000 must be accessible)
4. Review logs for webhook events

### Emails not sending
1. Verify SMTP settings in `.env`
2. For Gmail, ensure you're using an App Password (not your regular password)
3. Check SMTP port (587 for TLS, 465 for SSL)
4. Manually trigger: `curl -X POST http://localhost:8000/admin/notifications/process`

### Database issues
1. Check database password in `.env`
2. Ensure postgres container is healthy: `docker-compose ps`
3. Reset database: `docker-compose down -v` (⚠️ deletes all data)

## Backup and Restore

### Backup Database

```bash
docker exec notification-portal-db pg_dump -U notifyuser notifications > backup.sql
```

### Restore Database

```bash
docker exec -i notification-portal-db psql -U notifyuser notifications < backup.sql
```

## Updating

```bash
# Pull latest changes
git pull

# Rebuild containers
docker-compose down
docker-compose up -d --build

# Check status
docker-compose logs -f api
```

## Advanced Configuration

### Custom Email Templates

Email templates are in `app/services/email_service.py`. You can customize:
- HTML/CSS styling
- Email content
- Subject lines

### Running Behind a Reverse Proxy

If using nginx or Traefik:

```yaml
# docker-compose.yml
services:
  api:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.notifications.rule=Host(`notifications.yourdomain.com`)"
```

### Database Connection Pooling

For high-traffic deployments, adjust in `app/database.py`:

```python
engine = create_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=10
)
```

## Development

### Local Development Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with local values

# Run database
docker-compose up -d postgres

# Run API locally
uvicorn app.main:app --reload
```

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest tests/
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

MIT License - feel free to use and modify as needed!

## Support

For issues, questions, or feature requests, please open an issue on GitHub.

---

**Enjoy your automated Plex notifications!** 🎉
