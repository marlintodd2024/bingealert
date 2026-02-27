# Contributing

Thanks for your interest! Here's how to contribute.

## Reporting Bugs

Open an issue with:
- What you expected vs what happened
- Steps to reproduce
- Docker logs (`docker logs plex-notification-portal`)
- Browser console errors (if UI-related)

## Development Setup

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/plex-notification-portal.git
cd plex-notification-portal

# Python virtual environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start just the database
docker-compose up -d postgres

# Run in dev mode
cp .env.example .env
# Edit .env with your settings
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Pull Requests

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Test locally with Docker (`docker-compose up -d --build`)
5. Submit a PR with a clear description of what changed and why

## Architecture Notes

- **Backend**: FastAPI with SQLAlchemy ORM, PostgreSQL
- **Frontend**: Single-file vanilla HTML/CSS/JS (no build step)
- **Config**: `.env` for service connections, `system_config` DB table for runtime settings
- **Migrations**: Alembic (run automatically on container startup)
- **Auth**: Middleware-based, stored in DB, not `.env`
