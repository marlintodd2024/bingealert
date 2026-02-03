# Quick Start Guide

Get up and running in 5 minutes!

## 1. Prerequisites

- Docker & Docker Compose installed
- Jellyseerr, Sonarr, and Radarr running
- SMTP email credentials (Gmail, Outlook, etc.)

## 2. Get API Keys

### Jellyseerr API Key
1. Open Jellyseerr → Settings → General
2. Copy "API Key"

### Sonarr API Key
1. Open Sonarr → Settings → General → Security
2. Copy "API Key"

### Radarr API Key
1. Open Radarr → Settings → General → Security
2. Copy "API Key"

### Gmail App Password (if using Gmail)
1. Enable 2FA on your Google account
2. Visit: https://myaccount.google.com/apppasswords
3. Generate password for "Mail"

## 3. Install & Configure

```bash
# Clone repository
git clone <your-repo-url>
cd plex-notification-portal

# Copy environment file
cp .env.example .env

# Edit .env with your settings
nano .env
```

**Minimum required settings in `.env`:**
```env
DB_PASSWORD=choose_secure_password
JELLYSEERR_URL=http://your-jellyseerr:5055
JELLYSEERR_API_KEY=your_key_here
SONARR_URL=http://your-sonarr:8989
SONARR_API_KEY=your_key_here
RADARR_URL=http://your-radarr:7878
RADARR_API_KEY=your_key_here
SMTP_HOST=smtp.gmail.com
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=Plex Notifications <your_email@gmail.com>
```

## 4. Start Services

```bash
# Run setup script (recommended)
./setup.sh

# Or manually:
docker-compose up -d
```

## 5. Configure Webhooks

### In Sonarr:
1. Settings → Connect → Add → Webhook
2. URL: `http://your-server-ip:8000/webhooks/sonarr`
3. Check "On Download"
4. Test & Save

### In Radarr:
1. Settings → Connect → Add → Webhook
2. URL: `http://your-server-ip:8000/webhooks/radarr`
3. Check "On Download"
4. Test & Save

## 6. Verify Everything Works

```bash
# Check health
curl http://localhost:8000/health

# Sync users and requests
curl -X POST http://localhost:8000/admin/sync/users
curl -X POST http://localhost:8000/admin/sync/requests

# View stats
curl http://localhost:8000/admin/stats
```

## 7. Test It Out

1. Request content in Jellyseerr
2. Download completes in Sonarr/Radarr
3. Check your email! 📧

## Troubleshooting

**Can't connect to services?**
- Use Docker network names or host IPs in URLs
- Example: `http://jellyseerr:5055` or `http://192.168.1.100:5055`

**Not receiving emails?**
- Check logs: `docker-compose logs -f api`
- Verify SMTP settings
- For Gmail, make sure you're using an App Password

**Webhooks not triggering?**
- Ensure port 8000 is accessible from Sonarr/Radarr
- Test webhooks from Sonarr/Radarr UI
- Check firewall rules

## Next Steps

- View API docs: http://localhost:8000/docs
- Read full README.md for advanced features
- Customize email templates in `app/services/email_service.py`

That's it! You're all set! 🎉
