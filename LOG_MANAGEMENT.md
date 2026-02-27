# Log Management

## Current Configuration

The portal uses Docker's built-in log rotation with these settings:

### Portal Container:
- **Max log file size**: 10MB per file
- **Number of files**: 5 files kept (50MB total)
- **Compression**: Enabled (saves space)
- **Total storage**: ~50MB of logs

### Database Container:
- **Max log file size**: 10MB per file
- **Number of files**: 3 files kept (30MB total)

## How It Works

Docker automatically:
1. Rotates logs when they reach 10MB
2. Keeps the 5 most recent files
3. Compresses old log files
4. Deletes oldest when limit reached

## Viewing Logs

### Via Portal:
- Click **📋 Logs** tab in admin dashboard
- Shows last 100-500 lines
- Real-time viewing with Auto-Refresh

### Via Docker:
```bash
# Last 100 lines
docker logs plex-notification-portal --tail 100

# Follow live
docker logs -f plex-notification-portal

# Specific time range
docker logs plex-notification-portal --since 2h
docker logs plex-notification-portal --since "2024-02-19T10:00:00"
```

## Adjust Log Retention

Edit `docker-compose.yml` to change settings:

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "20m"      # Larger files
    max-file: "10"       # More files kept
    compress: "true"
```

Then restart: `docker-compose up -d`

## Log Levels

Application logs at these levels:
- **INFO**: Normal operations (notifications sent, webhooks received)
- **WARNING**: Issues that don't break functionality (TBA titles, retries)
- **ERROR**: Failures (webhook errors, email failures)

## Background Workers

Each worker logs separately:
- **Notification Processor**: Every 60 seconds
- **Reconciliation Worker**: Every 2 hours
- **Weekly Summary**: Sundays at 9 AM UTC
- **Stuck Download Monitor**: Every 30 minutes

## Disk Space

With default settings:
- Portal logs: ~50MB max
- Database logs: ~30MB max
- **Total**: ~80MB for all logs

Logs compress well (usually 5-10x compression), so actual disk usage is much less.

## Manual Cleanup

If needed, clear all logs:
```bash
# Truncate logs (keeps container running)
truncate -s 0 $(docker inspect --format='{{.LogPath}}' plex-notification-portal)
```

## Best Practices

✅ **Do**: Keep 3-7 days of logs (default setup)
✅ **Do**: Use compression (saves 80-90% space)
✅ **Do**: Monitor log viewer in portal
❌ **Don't**: Set max-file to 1 (lose history)
❌ **Don't**: Disable rotation (fills disk)
