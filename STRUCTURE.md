# Project Structure

```
plex-notification-portal/
│
├── app/                           # Main application code
│   ├── __init__.py
│   ├── main.py                    # FastAPI application entry point
│   ├── config.py                  # Configuration management
│   ├── database.py                # Database models and session
│   ├── schemas.py                 # Pydantic schemas for validation
│   │
│   ├── routers/                   # API route handlers
│   │   ├── __init__.py
│   │   ├── webhooks.py            # Sonarr/Radarr webhook endpoints
│   │   ├── admin.py               # Admin/management endpoints
│   │   └── health.py              # Health check endpoint
│   │
│   └── services/                  # Business logic services
│       ├── __init__.py
│       ├── jellyseerr_sync.py     # Jellyseerr API integration
│       ├── sonarr_service.py      # Sonarr API integration
│       ├── radarr_service.py      # Radarr API integration
│       └── email_service.py       # Email notification service
│
├── alembic/                       # Database migrations
│   ├── versions/
│   │   └── 001_initial_migration.py
│   ├── env.py
│   └── script.py.mako
│
├── tests/                         # Test files (to be added)
│
├── scripts/                       # Utility scripts
│
├── docker-compose.yml             # Docker Compose configuration
├── Dockerfile                     # Docker build instructions
├── requirements.txt               # Python dependencies
├── alembic.ini                    # Alembic configuration
│
├── .env.example                   # Environment template
├── .gitignore                     # Git ignore rules
│
├── setup.sh                       # Automated setup script
├── README.md                      # Comprehensive documentation
├── QUICKSTART.md                  # Quick start guide
├── STRUCTURE.md                   # This file
│
└── docker-compose.override.yml.example  # Docker override examples
```

## Key Components

### Application Layer (`app/`)

**main.py**
- FastAPI application initialization
- Router registration
- Lifecycle management (startup/shutdown)
- Global exception handling

**config.py**
- Environment variable management
- Configuration validation using Pydantic

**database.py**
- SQLAlchemy models (User, MediaRequest, EpisodeTracking, Notification)
- Database session management
- Database connection setup

**schemas.py**
- Request/response validation schemas
- Webhook payload schemas for Sonarr/Radarr
- Internal data transfer objects

### Routers (`app/routers/`)

**webhooks.py**
- POST /webhooks/sonarr - Handles Sonarr download events
- POST /webhooks/radarr - Handles Radarr download events
- Processes episodes/movies and creates notifications

**admin.py**
- POST /admin/sync/users - Manual user sync
- POST /admin/sync/requests - Manual request sync
- POST /admin/notifications/process - Process pending emails
- GET /admin/stats - System statistics
- GET /admin/users - List users
- GET /admin/requests - List requests
- GET /admin/notifications - List notifications

**health.py**
- GET /health - Health check and service status

### Services (`app/services/`)

**jellyseerr_sync.py**
- Fetches users from Jellyseerr API
- Fetches media requests from Jellyseerr API
- Syncs data to local database
- Maps Jellyseerr request statuses

**sonarr_service.py**
- Communicates with Sonarr API
- Fetches series information
- Gets episode details
- Looks up series by TMDB ID

**radarr_service.py**
- Communicates with Radarr API
- Fetches movie information
- Looks up movies by TMDB ID

**email_service.py**
- Sends emails via SMTP
- HTML email template rendering
- Processes notification queue
- Handles email delivery errors

### Database Migrations (`alembic/`)

**001_initial_migration.py**
- Creates initial database schema
- Sets up users, media_requests, episode_tracking, and notifications tables
- Defines relationships and constraints

## Data Flow

### 1. User & Request Sync
```
Jellyseerr API → jellyseerr_sync.py → Database
```

### 2. Content Download (TV Show)
```
Sonarr → Webhook → webhooks.py → Database
                              ↓
                        email_service.py → User Email
```

### 3. Content Download (Movie)
```
Radarr → Webhook → webhooks.py → Database
                              ↓
                        email_service.py → User Email
```

## Database Schema

### users
- Stores user information from Jellyseerr
- Links to media requests and notifications

### media_requests
- Tracks content requests from Jellyseerr
- Links users to their requested content
- Stores TMDB IDs for matching with Sonarr/Radarr

### episode_tracking
- Tracks individual TV episodes
- Prevents duplicate notifications
- Stores notification status per episode

### notifications
- Queue of email notifications
- Tracks sent/pending status
- Stores email content and delivery errors

## Docker Services

### postgres
- PostgreSQL 15 database
- Persistent data storage
- Automated health checks

### api
- Python FastAPI application
- Automatic database migrations on startup
- Exposes port 8000 for webhooks and API

## Environment Variables

See `.env.example` for all configuration options:
- Database credentials
- Jellyseerr connection
- Sonarr connection
- Radarr connection
- SMTP settings
- Application secrets

## API Documentation

Once running, visit:
- http://localhost:8000/docs - Swagger UI
- http://localhost:8000/redoc - ReDoc UI
