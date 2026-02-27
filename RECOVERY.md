# Data Recovery Guide

## If Database Volume Was Lost

The data isn't in the container - it's in a Docker volume. Let's find it:

### Step 1: Check for existing volumes
```bash
docker volume ls | grep postgres
docker volume ls | grep notification
docker volume ls | grep plex
```

Look for volumes like:
- `plex-notifier_postgres_data`
- `plex-notification-portal_postgres_data`
- `postgres_data`

### Step 2: Find the old volume
```bash
# List all postgres volumes with details
docker volume ls --filter driver=local | grep -E "postgres|notification"

# Inspect a volume to see when it was created
docker volume inspect <volume-name>
```

### Step 3: If you find the old volume, update docker-compose.yml

Change this:
```yaml
volumes:
  postgres_data:
```

To this:
```yaml
volumes:
  postgres_data:
    external: true
    name: <your-old-volume-name>  # e.g., plex-notifier_postgres_data
```

Then restart:
```bash
docker-compose up -d
```

### Step 4: If volume is truly gone, restore from backup

If you have a backup from the Backup tab:
```bash
# 1. Start fresh database
docker-compose up -d postgres

# 2. Wait for postgres to be ready
sleep 5

# 3. Restore from backup file
docker exec -i plex-notifications-db psql -U notifyuser -d notifications < backup.sql
```

## Preventing Future Loss

### Always use `docker-compose down` WITHOUT `-v` flag:
```bash
# GOOD - stops containers, keeps data
docker-compose down

# BAD - removes volumes, loses data!
docker-compose down -v
```

### Use Backup Feature

1. Go to **💾 Backup** tab
2. Click **"📥 Download Backup"**
3. Save the `.sql` file
4. Do this weekly or before updates

## Quick Recovery Steps

### If database is running but empty:

1. **Check if database is up:**
   ```bash
   docker-compose ps
   ```

2. **Sync data from Jellyseerr:**
   - Click **"🔄 Sync Users"**
   - Click **"🔄 Sync Requests"**
   - Click **"📥 Import All Episodes"**

3. **Reconciliation will catch up:**
   - Wait for automatic reconciliation (runs every 2 hours)
   - Or click **"🔍 Check Missed"** to run now

This will rebuild your tracking data from Jellyseerr and Sonarr.

## Current Volume Name

Your docker-compose.yml creates: `plex-notification-portal_postgres_data`

Check if it exists:
```bash
docker volume inspect plex-notification-portal_postgres_data
```
