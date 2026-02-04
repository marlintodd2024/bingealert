# Jellyseerr Webhook Setup

This guide explains how to configure Jellyseerr to send webhooks to the notification portal.

## 🎯 What This Does

**Event-Driven Architecture:**
- Jellyseerr sends webhook when request is created/approved
- Portal stores the request immediately (no 15-min delay!)
- Sonarr/Radarr webhooks trigger per-episode notifications
- Daily backup sync ensures nothing is missed

## 📋 Setup Steps

### 1. Get Your Webhook URL

Your webhook endpoint is:
```
http://YOUR_SERVER_IP:8000/webhooks/jellyseerr
```

If using a domain/reverse proxy:
```
https://your-domain.com/webhooks/jellyseerr
```

### 2. Configure Jellyseerr Webhook

1. Open Jellyseerr web interface
2. Go to **Settings** → **Notifications** → **Webhook**
3. Enable the Webhook agent
4. Configure:

**Webhook URL:**
```
http://YOUR_SERVER_IP:8000/webhooks/jellyseerr
```

**JSON Payload (leave default):**
The portal automatically handles Jellyseerr's format.

**Notification Types - Enable these:**
- ✅ **Media Requested** (MEDIA_PENDING)
- ✅ **Media Approved** (MEDIA_APPROVED)
- ✅ **Media Auto-Approved** (MEDIA_AUTO_APPROVED)

**Notification Types - Disable these:**
- ❌ **Media Available** (let Sonarr/Radarr handle this)
- ❌ **Media Failed** (optional - up to you)
- ❌ **Test Notification** (optional - for testing only)

### 3. Test It

1. In Jellyseerr, click **"Send Test Notification"**
2. Check portal logs:
```bash
docker compose logs -f api | grep -i jellyseerr
```

You should see:
```
Received Jellyseerr webhook: TEST_NOTIFICATION
Ignored event type: TEST_NOTIFICATION
```

3. Make a real request in Jellyseerr
4. Check the logs again - you should see:
```
Received Jellyseerr webhook: MEDIA_APPROVED
Created new request for [TITLE] (tv) by [username]
```

5. Check the portal dashboard → Requests tab - your request should appear immediately!

## 🔄 How It Works Now

### Old Flow (Polling):
```
User requests in Jellyseerr
↓
[wait up to 15 minutes]
↓
Portal polls Jellyseerr API
↓
Request synced
↓
Episode downloads → Notification
```

### New Flow (Event-Driven):
```
User requests in Jellyseerr
↓
Jellyseerr webhook → Portal (instant!)
↓
Request stored immediately
↓
Episode downloads → Notification
```

## 🛡️ Backup Sync

The portal still runs a **daily backup sync** at midnight to catch anything that might have been missed if webhooks fail. This ensures resilience.

## 🐛 Troubleshooting

### Webhook not working?

**Check portal logs:**
```bash
docker compose logs -f api | grep -i "jellyseerr"
```

**Check Jellyseerr logs:**
- Settings → Logs
- Look for webhook delivery errors

**Common issues:**
- ❌ Wrong URL (check IP/port/domain)
- ❌ Firewall blocking port 8000
- ❌ Portal not running (`docker compose ps`)
- ❌ Wrong notification types enabled

### Request not showing in portal?

1. Check if webhook was received in logs
2. Check Users tab - is the requesting user synced?
3. Try manual sync: Click "Sync Requests" button
4. Check for errors in portal logs

### Still using old polling?

The portal now only syncs once daily as backup. If you prefer the old 15-min polling, edit `app/main.py` and change:
```python
await asyncio.sleep(86400)  # 24 hours
```
to:
```python
await asyncio.sleep(900)  # 15 minutes
```

## ✅ Verification

After setup, verify everything works:

1. ✅ Request something in Jellyseerr
2. ✅ Check portal dashboard - request appears immediately
3. ✅ Wait for Sonarr/Radarr to download
4. ✅ Check email - notification sent per episode
5. ✅ Check Notifications tab - shows sent notification

You're all set! 🎉
