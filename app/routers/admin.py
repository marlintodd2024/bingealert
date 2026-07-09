from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging
import os
import json
from datetime import datetime

from app.database import (
    AdminActivityLog,
    get_db,
    User,
    MediaRequest,
    EpisodeTracking,
    Notification,
    SharedRequest,
    SystemConfig,
    MaintenanceWindow,
)
from app.services.jellyseerr_sync import JellyseerrSyncService
from app.services.email_service import EmailService
from app.security import (
    clean_email_address,
    normalize_http_url,
    sanitize_for_log,
    validate_ip_or_cidr_csv,
)
from app.services.admin_activity import record_admin_activity

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sync/users")
async def sync_users():
    """Manually trigger user sync from Jellyseerr"""
    try:
        sync_service = JellyseerrSyncService()
        await sync_service.sync_users()
        return {"success": True, "message": "User sync completed"}
    except Exception as e:
        logger.error(f"User sync failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sync/requests")
async def sync_requests():
    """Manually trigger request sync from Jellyseerr"""
    try:
        sync_service = JellyseerrSyncService()
        await sync_service.sync_requests()
        return {"success": True, "message": "Request sync completed"}
    except Exception as e:
        logger.error(f"Request sync failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/notifications/process")
async def process_notifications(db: Session = Depends(get_db)):
    """Manually trigger processing of pending notifications"""
    try:
        email_service = EmailService()
        await email_service.process_pending_notifications(db)
        record_admin_activity("process_notifications", "Processed pending notifications", db=db)
        db.commit()
        return {"success": True, "message": "Notifications processed"}
    except Exception as e:
        logger.error(f"Notification processing failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/stats")
async def get_stats(db: Session = Depends(get_db)):
    """Get system statistics"""
    try:
        from app.database import ReportedIssue

        total_users = db.query(func.count(User.id)).scalar()
        active_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar()

        stats = {
            "users": total_users,
            "active_users": active_users,
            "inactive_users": total_users - active_users,
            "requests": {
                "total": db.query(func.count(MediaRequest.id)).scalar(),
                "movies": db.query(func.count(MediaRequest.id)).filter(MediaRequest.media_type == "movie").scalar(),
                "tv_shows": db.query(func.count(MediaRequest.id)).filter(MediaRequest.media_type == "tv").scalar(),
                "tracking": db.query(func.count(MediaRequest.id)).filter(MediaRequest.status != "available").scalar(),
            },
            "episodes_tracked": db.query(func.count(EpisodeTracking.id)).scalar(),
            "notifications": {
                "total": db.query(func.count(Notification.id)).scalar(),
                "sent": db.query(func.count(Notification.id)).filter(Notification.sent == True).scalar(),
                "pending": db.query(func.count(Notification.id)).filter(Notification.sent == False).scalar(),
            },
            # Lets the dashboard populate every tab-count badge on initial load
            # instead of waiting for each tab's first click to fetch its data.
            "issues": db.query(func.count(ReportedIssue.id)).scalar(),
        }
        return stats
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/system-health")
async def get_system_health():
    """Return latest service and worker health snapshot."""
    try:
        from app.background.system_health import get_system_health_snapshot

        return get_system_health_snapshot()
    except Exception as e:
        logger.error(f"Failed to get system health: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/system-health/check")
async def check_system_health_now():
    """Run service health checks immediately."""
    try:
        from app.background.system_health import run_service_health_checks

        result = await run_service_health_checks(send_alerts=True)
        record_admin_activity("system_health_check", "Manual system health check started")
        return result
    except Exception as e:
        logger.error(f"Failed to run system health checks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/system-health/history")
async def get_system_health_history(hours: int = 24, limit: int = 200):
    """Return recent service health check events."""
    try:
        from app.background.system_health import get_service_health_history

        return get_service_health_history(hours=hours, limit=limit)
    except Exception as e:
        logger.error(f"Failed to get system health history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/activity")
async def get_admin_activity(limit: int = 100, db: Session = Depends(get_db)):
    """Return recent admin activity/audit rows."""
    try:
        safe_limit = max(1, min(int(limit or 100), 500))
        rows = (
            db.query(AdminActivityLog)
            .order_by(AdminActivityLog.created_at.desc())
            .limit(safe_limit)
            .all()
        )
        def parse_details(value):
            if not value:
                return {}
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except (TypeError, ValueError):
                return {"raw": str(value)}

        activity = [
            {
                "id": row.id,
                "action": row.action,
                "status": row.status,
                "message": row.message,
                "details": parse_details(row.details),
                "actor": row.actor,
                "ip_address": row.ip_address,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
        return {
            "activity": activity,
            "activities": activity,
            "count": len(rows),
        }
    except Exception as e:
        logger.error(f"Failed to get admin activity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/config/validate")
async def validate_config():
    """Run a setup/configuration validation check."""
    from app.config import settings as _s

    checks = []

    def add(category: str, name: str, status: str, message: str):
        checks.append({"category": category, "name": name, "status": status, "message": message})

    try:
        add("core", "Config file", "ok" if os.path.isfile(_s.config_file_path) else "warn", _s.config_file_path)
        add("core", "App secret", "ok" if _s.app_secret_key and len(_s.app_secret_key) >= 32 else "error", "Strong key configured" if _s.app_secret_key else "Missing app secret")
        add("auth", "Admin auth", "ok" if (not _s.auth_required or _s.admin_password_hash) else "error", "Enabled" if _s.auth_required else "Disabled")
        add("email", "SMTP host", "ok" if _s.smtp_host and _s.smtp_from else "error", _s.smtp_host or "SMTP host missing")
        add("email", "Admin email", "ok" if clean_email_address(_s.admin_email or _s.smtp_from) else "warn", clean_email_address(_s.admin_email or _s.smtp_from) or "No valid admin alert email")
        add("security", "Webhook secret", "ok" if _s.webhook_secret else "warn", "Configured" if _s.webhook_secret else "Not configured")
        add("security", "Webhook IP allowlist", "ok" if _s.webhook_allowed_ips else "warn", _s.webhook_allowed_ips or "All IPs allowed")
        add("system", "SQLite database", "ok" if os.path.isfile(os.path.join(_s.data_dir, _s.sqlite_filename)) else "warn", os.path.join(_s.data_dir, _s.sqlite_filename))
        add("system", "Docker socket", "ok" if os.path.exists("/var/run/docker.sock") else "warn", "/var/run/docker.sock available" if os.path.exists("/var/run/docker.sock") else "Docker restart unavailable")

        if _s.public_base_url:
            try:
                normalize_http_url(_s.public_base_url)
                add("email", "Public base URL", "ok", _s.public_base_url)
            except ValueError:
                add("email", "Public base URL", "error", "Invalid public base URL")
        else:
            add("email", "Public base URL", "warn", "Not set; email calendar links are omitted")

        if _s.alert_webhook_type == "pushover" or _s.pushover_app_token or _s.pushover_user_key:
            pushover_configured = bool(_s.pushover_app_token and _s.pushover_user_key)
            if _s.alert_webhook_enabled and _s.alert_webhook_type == "pushover":
                status = "ok" if pushover_configured else "error"
            else:
                status = "ok" if pushover_configured else "warn"
            add(
                "integrations",
                "Pushover",
                status,
                "Configured" if pushover_configured else "Missing app token or user/group key",
            )

        from app.background.system_health import run_service_health_checks

        health = await run_service_health_checks(send_alerts=False)
        for service in health.get("services", []):
            if not service.get("configured"):
                status = "warn"
            elif service.get("status") == "ok":
                status = "ok"
            else:
                status = "error"
            if status == "ok":
                message = "Reachability check passed"
            elif status == "warn":
                message = "Service is not configured"
            else:
                message = "Reachability check failed; see System Health for details"
            add(
                "integrations",
                service.get("service_name") or service.get("service_key"),
                status,
                message,
            )

        summary = {
            "ok": sum(1 for c in checks if c["status"] == "ok"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "error": sum(1 for c in checks if c["status"] == "error"),
        }
        record_admin_activity("config_validate", "Configuration validation run", details=summary)
        return {"checks": checks, "summary": summary, "checked_at": datetime.utcnow().isoformat()}
    except Exception as e:
        logger.error(f"Config validation failed: {e}", exc_info=True)
        record_admin_activity(
            "config_validate",
            "Configuration validation failed",
            status="error",
            details={"error": "Configuration validation failed"},
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/users")
async def list_users(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all users"""
    users = db.query(User).order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "users": [
            {
                "id": u.id,
                "jellyseerr_id": u.jellyseerr_id,
                "email": u.email,
                "username": u.username,
                "is_active": u.is_active if hasattr(u, 'is_active') else True,
                "deactivated_at": u.deactivated_at.isoformat() + 'Z' if hasattr(u, 'deactivated_at') and u.deactivated_at else None,
                "created_at": u.created_at.isoformat() + 'Z' if u.created_at else None
            }
            for u in users
        ]
    }


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(user_id: int, db: Session = Depends(get_db)):
    """Toggle a user's active status (soft delete / reactivate)"""
    from datetime import datetime
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.is_active:
        user.is_active = False
        user.deactivated_at = datetime.utcnow()
        action = "deactivated"
        logger.info(f"Manually deactivated user: {user.username} ({user.email})")
    else:
        user.is_active = True
        user.deactivated_at = None
        action = "reactivated"
        logger.info(f"Manually reactivated user: {user.username} ({user.email})")
    
    db.commit()
    
    return {
        "success": True,
        "message": f"User {user.username} {action}",
        "is_active": user.is_active
    }


@router.get("/requests")
async def list_requests(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all media requests"""
    requests = db.query(MediaRequest).order_by(MediaRequest.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "requests": [
            {
                "id": r.id,
                "user_email": r.user.email,
                "media_type": r.media_type,
                "title": r.title,
                "status": r.status,
                "created_at": r.created_at.isoformat() + 'Z' if r.created_at else None
            }
            for r in requests
        ]
    }


@router.get("/notifications")
async def list_notifications(
    skip: int = 0,
    limit: int = 50,
    sent: bool = None,
    db: Session = Depends(get_db)
):
    """List notifications"""
    query = db.query(Notification)
    
    if sent is not None:
        query = query.filter(Notification.sent == sent)
    
    notifications = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
    
    return {
        "notifications": [
            {
                "id": n.id,
                "user_email": n.user.email,
                "type": n.notification_type,
                "subject": n.subject,
                "sent": n.sent,
                "sent_at": n.sent_at.isoformat() + 'Z' if n.sent_at else None,
                "send_after": n.send_after.isoformat() + 'Z' if n.send_after else None,
                "created_at": n.created_at.isoformat() + 'Z' if n.created_at else None
            }
            for n in notifications
        ]
    }


@router.get("/upcoming-episodes")
async def get_upcoming_episodes(days: int = 30, db: Session = Depends(get_db)):
    """Get upcoming episodes from Sonarr calendar that match user requests.

    Multi-instance correct: each Sonarr's series IDs are scoped to that
    instance, so we tag every calendar episode with its source and look
    up its series details against that instance's series_map. (The
    pre-v2.0.2 code only built series_map from the last instance, which
    silently dropped calendar episodes from any other instance whose
    series IDs didn't happen to collide with the last one's.)
    """
    try:
        from app.services.sonarr_service import SonarrService, get_all_sonarr_instances
        from app.database import EpisodeTracking
        from datetime import datetime, timedelta

        start_date = datetime.utcnow().strftime('%Y-%m-%d')
        end_date = (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')

        instances = get_all_sonarr_instances()
        # series_maps[i] = {series_id: series_dict} for instances[i]
        series_maps: list[dict] = []
        calendar_episodes: list[dict] = []

        for idx, sonarr in enumerate(instances):
            # Pull /series for this instance so we can resolve seriesId later.
            try:
                series_list = await sonarr._get("/series")
                series_maps.append({s.get("id"): s for s in series_list if s.get("id")})
                logger.info(f"Loaded {len(series_maps[-1])} series from {sonarr.instance_name}")
            except Exception as e:
                logger.warning(f"Failed to load series from {sonarr.instance_name}: {e}")
                series_maps.append({})

            logger.info(f"Fetching {sonarr.instance_name} calendar from {start_date} to {end_date}")
            try:
                episodes = await sonarr.get_calendar(start_date, end_date)
            except Exception as e:
                logger.warning(f"Failed calendar fetch from {sonarr.instance_name}: {e}")
                episodes = []
            if episodes:
                for ep in episodes:
                    ep["_instance_idx"] = idx
                calendar_episodes.extend(episodes)
                logger.info(f"Found {len(episodes)} episodes in {sonarr.instance_name} calendar")

        if not calendar_episodes:
            logger.warning("No episodes returned from any Sonarr instance")
            return {"upcoming": [], "count": 0}

        logger.info(f"Found {len(calendar_episodes)} total episodes across all Sonarr instances")

        # Get all TV show requests with their users
        tv_requests = db.query(MediaRequest).filter(
            MediaRequest.media_type == "tv"
        ).all()

        logger.info(f"Found {len(tv_requests)} TV show requests in database")
        
        # Create a mapping of series TMDB IDs to users who requested them
        tmdb_to_requests = {}
        title_to_requests = {}  # Fallback matching by title
        for request in tv_requests:
            if request.tmdb_id:
                if request.tmdb_id not in tmdb_to_requests:
                    tmdb_to_requests[request.tmdb_id] = []
                tmdb_to_requests[request.tmdb_id].append(request)
            
            # Also track by title (normalized)
            normalized_title = request.title.lower().strip()
            if normalized_title not in title_to_requests:
                title_to_requests[normalized_title] = []
            title_to_requests[normalized_title].append(request)
        
        logger.info(f"Tracking {len(tmdb_to_requests)} unique series by TMDB ID, {len(title_to_requests)} by title")
        logger.info(f"Request titles: {list(title_to_requests.keys())[:5]}")  # Show first 5
        logger.info(f"Request TMDB IDs: {list(tmdb_to_requests.keys())[:5]}")  # Show first 5
        
        upcoming = []
        matched_count = 0
        
        for episode in calendar_episodes:
            # Get series details from the per-instance series map.
            series_id = episode.get("seriesId")
            instance_idx = episode.get("_instance_idx", 0)
            instance_map = series_maps[instance_idx] if instance_idx < len(series_maps) else {}
            if not series_id or series_id not in instance_map:
                logger.debug(
                    f"Episode {episode.get('title')} (seriesId={series_id}) "
                    f"not in instance {instance_idx} series_map"
                )
                continue

            series = instance_map[series_id]
            series_tmdb = series.get("tmdbId")
            series_title = series.get("title", "").lower().strip()
            
            # Try to match by TMDB ID first, then by title
            matching_requests = []
            if series_tmdb and series_tmdb in tmdb_to_requests:
                matching_requests = tmdb_to_requests[series_tmdb]
                logger.debug(f"Matched '{series.get('title')}' by TMDB ID {series_tmdb}")
            elif series_title in title_to_requests:
                matching_requests = title_to_requests[series_title]
                logger.debug(f"Matched '{series.get('title')}' by title '{series_title}'")
            
            # Check if any user has requested this series
            if matching_requests:
                matched_count += 1
                # Check if this episode has already been notified
                for request in matching_requests:
                    existing_tracking = db.query(EpisodeTracking).filter(
                        EpisodeTracking.request_id == request.id,
                        EpisodeTracking.season_number == episode.get("seasonNumber"),
                        EpisodeTracking.episode_number == episode.get("episodeNumber")
                    ).first()
                    
                    # Get all users for this request (original + shared)
                    users_for_request = [request.user]
                    
                    # Add shared users
                    from app.database import SharedRequest
                    shared = db.query(SharedRequest).filter(
                        SharedRequest.request_id == request.id
                    ).all()
                    for s in shared:
                        users_for_request.append(s.user)
                    
                    # Create an entry for each user
                    for user in users_for_request:
                        upcoming.append({
                            "request_id": request.id,
                            "series_id": series_id,
                            "series_title": series.get("title"),
                            "season_number": episode.get("seasonNumber"),
                            "episode_number": episode.get("episodeNumber"),
                            "episode_title": episode.get("title"),
                            "air_date": episode.get("airDateUtc"),
                            "has_file": episode.get("hasFile", False),
                            "monitored": episode.get("monitored", True),
                            "user_email": user.email,
                            "user_name": user.username,
                            "already_notified": existing_tracking.notified if existing_tracking else False
                        })
        
        logger.info(f"Matched {matched_count} episodes to user requests, {len(upcoming)} pending notification")
        
        # Sort by air date
        upcoming.sort(key=lambda x: x["air_date"] if x["air_date"] else "")
        
        return {
            "upcoming": upcoming,
            "count": len(upcoming),
            "debug": {
                "calendar_episodes": len(calendar_episodes),
                "tv_requests": len(tv_requests),
                "tracked_series": len(tmdb_to_requests),
                "matched_episodes": matched_count
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to get upcoming episodes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/requests/{request_id}/import-episodes")
async def import_existing_episodes(request_id: int, db: Session = Depends(get_db)):
    """Manually import existing episodes from Sonarr for a specific TV show request"""
    try:
        # Get the request
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        if request.media_type != "tv":
            raise HTTPException(status_code=400, detail="Request is not a TV show")
        
        # Import existing episodes
        from app.services.sonarr_service import SonarrService, get_all_sonarr_instances
        from app.services.jellyseerr_sync import JellyseerrSyncService
        
        sync_service = JellyseerrSyncService()
        
        # Try importing from all Sonarr instances
        for sonarr in get_all_sonarr_instances():
            await sync_service._import_existing_episodes(
                db, 
                request, 
                request.tmdb_id, 
                sonarr
            )
        
        db.commit()
        
        # Get count of imported episodes
        episode_count = db.query(EpisodeTracking).filter(
            EpisodeTracking.request_id == request_id
        ).count()
        
        return {
            "success": True,
            "message": f"Imported existing episodes for '{request.title}'",
            "total_episodes_tracked": episode_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to import episodes for request {request_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/import-all-existing-episodes")
async def import_all_existing_episodes(db: Session = Depends(get_db)):
    """Import existing episodes from Sonarr for ALL TV show requests"""
    try:
        from app.services.sonarr_service import SonarrService, get_all_sonarr_instances
        from app.services.jellyseerr_sync import JellyseerrSyncService
        
        sonarr_instances = get_all_sonarr_instances()
        sync_service = JellyseerrSyncService()
        
        # Get all TV show requests
        tv_requests = db.query(MediaRequest).filter(MediaRequest.media_type == "tv").all()
        
        imported_count = 0
        for request in tv_requests:
            try:
                for sonarr in sonarr_instances:
                    await sync_service._import_existing_episodes(
                        db,
                        request,
                        request.tmdb_id,
                        sonarr
                    )
                imported_count += 1
            except Exception as e:
                logger.error(f"Failed to import episodes for request {request.id}: {e}")
                continue
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Imported existing episodes for {imported_count} TV show requests",
            "processed_requests": imported_count
        }
        
    except Exception as e:
        logger.error(f"Failed to import all existing episodes: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-email")
async def send_test_email(
    email: str,
    notification_type: str = "episode",
    db: Session = Depends(get_db)
):
    """Send a test email notification"""
    try:
        from app.services.email_service import EmailService
        from app.services.tmdb_service import TMDBService
        from app.config import settings as app_settings
        
        email_service = EmailService()
        tmdb_service = TMDBService(app_settings.jellyseerr_url, app_settings.jellyseerr_api_key)
        
        # Generate test email based on type
        if notification_type == "episode":
            # Breaking Bad TMDB ID: 1396
            poster_url = await tmdb_service.get_tv_poster(1396)
            
            html_body = email_service.render_episode_notification(
                series_title="Breaking Bad",
                episodes=[
                    {
                        'season': 1,
                        'episode': 1,
                        'title': "Pilot",
                        'air_date': "2008-01-20"
                    },
                    {
                        'season': 1,
                        'episode': 2,
                        'title': "Cat's in the Bag...",
                        'air_date': "2008-01-27"
                    }
                ],
                poster_url=poster_url
            )
            subject = "Test: New Episodes Available - Breaking Bad"
        elif notification_type == "movie":
            # The Shawshank Redemption TMDB ID: 278
            poster_url = await tmdb_service.get_movie_poster(278)
            
            html_body = email_service.render_movie_notification(
                movie_title="The Shawshank Redemption",
                year=1994,
                poster_url=poster_url
            )
            subject = "Test: Movie Available - The Shawshank Redemption"
        else:
            raise HTTPException(status_code=400, detail="Invalid notification type. Use 'episode' or 'movie'")
        
        # Send the test email
        success = await email_service.send_email(
            to_email=email,
            subject=subject,
            html_body=html_body
        )
        
        if success:
            return {
                "success": True,
                "message": f"Test email sent successfully to {email}"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to send test email")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send test email: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/notify-episode")
async def notify_episode_now(
    request_id: int,
    series_id: int,
    season_number: int,
    episode_number: int,
    db: Session = Depends(get_db)
):
    """Manually trigger notification for a specific episode"""
    try:
        from app.services.email_service import EmailService
        from app.services.sonarr_service import SonarrService, get_all_sonarr_instances
        from app.database import EpisodeTracking
        
        # Get the request
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Get series details from Sonarr (try all instances)
        series = None
        matched_sonarr = None
        for sonarr in get_all_sonarr_instances():
            series = await sonarr.get_series(series_id)
            if series:
                matched_sonarr = sonarr
                break
        
        if not series or not matched_sonarr:
            raise HTTPException(status_code=404, detail="Series not found in any Sonarr instance")
        
        # Get episode details
        all_episodes = await matched_sonarr.get_episodes_by_series(series_id)
        episode = None
        for ep in all_episodes or []:
            if ep.get("seasonNumber") == season_number and ep.get("episodeNumber") == episode_number:
                episode = ep
                break
        
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")
        
        # Create or update episode tracking
        tracking = db.query(EpisodeTracking).filter(
            EpisodeTracking.request_id == request_id,
            EpisodeTracking.series_id == series_id,
            EpisodeTracking.season_number == season_number,
            EpisodeTracking.episode_number == episode_number
        ).first()
        
        if not tracking:
            from datetime import datetime
            tracking = EpisodeTracking(
                request_id=request_id,
                series_id=series_id,
                season_number=season_number,
                episode_number=episode_number,
                episode_title=episode.get("title"),
                air_date=datetime.fromisoformat(episode.get("airDateUtc").replace('Z', '+00:00')) if episode.get("airDateUtc") else None,
                notified=True,
                available_in_plex=True
            )
            db.add(tracking)
        else:
            # Mark as notified
            tracking.notified = True
        
        # Create notification
        email_service = EmailService()
        
        # Get poster URL
        from app.services.tmdb_service import TMDBService
        from app.config import settings as app_settings
        tmdb_service = TMDBService(app_settings.jellyseerr_url, app_settings.jellyseerr_api_key)
        poster_url = await tmdb_service.get_tv_poster(request.tmdb_id)
        
        html_body = email_service.render_episode_notification(
            series_title=series.get("title"),
            episodes=[{
                'season': season_number,
                'episode': episode_number,
                'title': episode.get("title"),
                'air_date': episode.get("airDate")
            }],
            poster_url=poster_url
        )
        
        notification = Notification(
            user_id=request.user_id,
            request_id=request_id,
            notification_type="episode",
            subject=f"New Episode: {series.get('title')} S{season_number:02d}E{episode_number:02d}",
            body=html_body
        )
        db.add(notification)
        
        # Mark as notified
        tracking.notified = True
        
        db.commit()
        
        # Send immediately
        success = await email_service.send_email(
            to_email=request.user.email,
            subject=notification.subject,
            html_body=notification.body
        )
        
        if success:
            notification.sent = True
            from datetime import datetime
            from app.services.notification_history import record_delivery_for_notification
            notification.sent_at = datetime.utcnow()
            record_delivery_for_notification(db, notification, sent_at=notification.sent_at)
            db.commit()
        
        return {
            "success": True,
            "message": f"Notification sent to {request.user.email}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send episode notification: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/resend-notification/{notification_id}")
async def resend_notification(notification_id: int, regenerate: bool = True, db: Session = Depends(get_db)):
    """Resend an existing notification (optionally regenerate with fresh poster)"""
    try:
        from app.services.email_service import EmailService
        from app.services.tmdb_service import TMDBService
        from app.config import settings as app_settings
        
        notification = db.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        email_service = EmailService()
        tmdb_service = TMDBService(app_settings.jellyseerr_url, app_settings.jellyseerr_api_key)
        
        # Optionally regenerate the email body with a fresh poster
        body = notification.body
        if regenerate and notification.request:
            logger.info(f"Regenerating notification {notification_id} with fresh poster")
            
            if notification.notification_type == "episode":
                # Extract episode info from subject (e.g., "New Episode: Breaking Bad S01E05")
                import re
                match = re.search(r'S(\d+)E(\d+)', notification.subject)
                if match and notification.request.tmdb_id:
                    season = int(match.group(1))
                    episode = int(match.group(2))
                    
                    poster_url = await tmdb_service.get_tv_poster(notification.request.tmdb_id)
                    
                    # Get episode title from tracking if available
                    from app.database import EpisodeTracking
                    tracking = db.query(EpisodeTracking).filter(
                        EpisodeTracking.request_id == notification.request_id,
                        EpisodeTracking.season_number == season,
                        EpisodeTracking.episode_number == episode
                    ).first()
                    
                    body = email_service.render_episode_notification(
                        series_title=notification.request.title,
                        episodes=[{
                            'season': season,
                            'episode': episode,
                            'title': tracking.episode_title if tracking else None,
                            'air_date': tracking.air_date.strftime('%Y-%m-%d') if tracking and tracking.air_date else None
                        }],
                        poster_url=poster_url
                    )
            elif notification.notification_type == "movie" and notification.request.tmdb_id:
                poster_url = await tmdb_service.get_movie_poster(notification.request.tmdb_id)
                body = email_service.render_movie_notification(
                    movie_title=notification.request.title,
                    poster_url=poster_url
                )
        
        success = await email_service.send_email(
            to_email=notification.user.email,
            subject=notification.subject,
            html_body=body
        )
        
        if success:
            from datetime import datetime
            from app.services.notification_history import record_delivery_for_notification
            notification.sent = True
            notification.sent_at = datetime.utcnow()
            notification.error_message = None
            if regenerate:
                notification.body = body  # Update stored body with new poster
            record_delivery_for_notification(db, notification, sent_at=notification.sent_at)
            db.commit()
            
            return {
                "success": True,
                "message": f"Notification resent to {notification.user.email}" + (" (regenerated with poster)" if regenerate else "")
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to resend notification")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resend notification: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/backup/create")
async def create_backup(include_config: bool = True):
    """Create a backup of database and configuration"""
    try:
        from app.services.backup_service import BackupService
        
        backup_service = BackupService()
        backup_file = backup_service.create_backup(include_config=include_config)
        
        if backup_file:
            record_admin_activity(
                "backup_create",
                "Backup created manually",
                details={"filename": os.path.basename(backup_file), "include_config": include_config},
            )
            return {
                "success": True,
                "message": "Backup created successfully",
                "filename": os.path.basename(backup_file),
                "filepath": backup_file
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create backup")
    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/backup/list")
async def list_backups():
    """List all available backups"""
    try:
        from app.services.backup_service import BackupService
        
        backup_service = BackupService()
        backups = backup_service.list_backups()
        
        return {
            "backups": backups,
            "count": len(backups)
        }
    except Exception as e:
        logger.error(f"Failed to list backups: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/backup/download/{filename}")
async def download_backup(filename: str):
    """Download a backup file"""
    try:
        from app.services.backup_service import BackupService
        from fastapi.responses import FileResponse
        
        backup_service = BackupService()
        
        # Validate filename against actual directory listing (no user input in path construction)
        available_files = []
        backup_dir = os.path.realpath(backup_service.backup_dir)
        for entry in os.listdir(backup_dir):
            full_path = os.path.join(backup_dir, entry)
            if os.path.isfile(full_path) and entry.endswith('.zip'):
                available_files.append((entry, full_path))
        
        # Match requested filename against known safe files
        matched_path = None
        matched_name = None
        for name, path in available_files:
            if name == filename:
                matched_path = path
                matched_name = name
                break
        
        if not matched_path:
            raise HTTPException(status_code=404, detail="Backup file not found")
        
        return FileResponse(
            path=matched_path,
            filename=matched_name,
            media_type="application/zip"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download backup: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/backup/restore")
async def restore_backup(file: UploadFile):
    """Restore from an uploaded backup file"""
    try:
        from app.services.backup_service import BackupService
        import tempfile
        import zipfile

        # SECURITY FIX [MED-4]: Validate upload
        if not file.filename or not file.filename.endswith('.zip'):
            raise HTTPException(status_code=400, detail="Only .zip files are accepted")

        # Read and validate size (max 50MB)
        content = await file.read()
        max_size = 50 * 1024 * 1024
        if len(content) > max_size:
            raise HTTPException(status_code=400, detail="File too large (max 50MB)")

        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        # SECURITY FIX [MED-4]: Validate ZIP contents before restore
        try:
            with zipfile.ZipFile(temp_path, 'r') as zf:
                names = zf.namelist()
                if 'metadata.json' not in names:
                    os.remove(temp_path)
                    raise HTTPException(status_code=400, detail="Invalid backup: missing metadata.json")
                if 'bingealert.db' not in names:
                    os.remove(temp_path)
                    raise HTTPException(status_code=400, detail="Invalid backup: missing bingealert.db")
                for name in names:
                    if name.startswith('/') or '..' in name:
                        os.remove(temp_path)
                        logger.warning(f"Zip-slip attempt detected: {name}")
                        raise HTTPException(status_code=400, detail="Invalid backup: suspicious file paths")
                allowed_extensions = {'.json', '.db', '.txt'}
                for name in names:
                    ext = os.path.splitext(name)[1].lower()
                    if ext and ext not in allowed_extensions:
                        os.remove(temp_path)
                        raise HTTPException(status_code=400, detail=f"Invalid backup: unexpected file type")
        except zipfile.BadZipFile:
            os.remove(temp_path)
            raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file")

        backup_service = BackupService()
        success = backup_service.restore_backup(temp_path)
        
        # Cleanup temp file
        os.remove(temp_path)
        
        if success:
            record_admin_activity(
                "backup_restore",
                "Backup restored",
                details={"filename": file.filename},
            )
            return {
                "success": True,
                "message": "Backup restored successfully. Please restart the application."
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to restore backup")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Restore failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/backup/delete/{filename}")
async def delete_backup(filename: str):
    """Delete a backup file"""
    try:
        from app.services.backup_service import BackupService
        
        backup_service = BackupService()
        success = backup_service.delete_backup(filename)
        
        if success:
            record_admin_activity(
                "backup_delete",
                f"Deleted backup {filename}",
                details={"filename": filename},
            )
            return {
                "success": True,
                "message": f"Backup {filename} deleted successfully"
            }
        else:
            raise HTTPException(status_code=404, detail="Backup file not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete backup: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/requests/{request_id}/shared-users")
async def get_shared_users(request_id: int, db: Session = Depends(get_db)):
    """Get all users sharing a request"""
    try:
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Get original requester
        original_user = {
            "user_id": request.user_id,
            "username": request.user.username,
            "email": request.user.email,
            "is_original": True,
            "added_at": request.created_at.isoformat()
        }
        
        # Get shared users
        shared_users = []
        for shared in request.shared_with:
            shared_users.append({
                "user_id": shared.user_id,
                "username": shared.user.username,
                "email": shared.user.email,
                "is_original": False,
                "added_at": shared.added_at.isoformat(),
                "added_by": shared.added_by_user.username if shared.added_by_user else None
            })
        
        return {
            "request_id": request_id,
            "title": request.title,
            "users": [original_user] + shared_users
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get shared users: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/requests/{request_id}/share")
async def share_request_with_user(request_id: int, user_id: int, db: Session = Depends(get_db)):
    """Add a user to a request (share it with them)"""
    try:
        # Check if request exists
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Check if user exists
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if already the original requester
        if request.user_id == user_id:
            raise HTTPException(status_code=400, detail="User is already the original requester")
        
        # Check if already shared
        existing = db.query(SharedRequest).filter(
            SharedRequest.request_id == request_id,
            SharedRequest.user_id == user_id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="Request already shared with this user")
        
        # Create shared request
        shared = SharedRequest(
            request_id=request_id,
            user_id=user_id,
            added_by=None  # Could track admin user if you add auth
        )
        db.add(shared)
        db.commit()
        
        logger.info(f"Shared request {request_id} ({request.title}) with user {user.username}")
        
        return {
            "success": True,
            "message": f"Request shared with {user.username}",
            "request_id": request_id,
            "user_id": user_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to share request: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/requests/{request_id}/share/{user_id}")
async def unshare_request_with_user(request_id: int, user_id: int, db: Session = Depends(get_db)):
    """Remove a user from a request"""
    try:
        # Check if request exists
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Can't remove original requester
        if request.user_id == user_id:
            raise HTTPException(status_code=400, detail="Cannot remove the original requester")
        
        # Find shared request
        shared = db.query(SharedRequest).filter(
            SharedRequest.request_id == request_id,
            SharedRequest.user_id == user_id
        ).first()
        
        if not shared:
            raise HTTPException(status_code=404, detail="User is not shared on this request")
        
        db.delete(shared)
        db.commit()
        
        logger.info(f"Removed user {user_id} from request {request_id} ({request.title})")
        
        return {
            "success": True,
            "message": "User removed from request"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to unshare request: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


def _mask_secret(value) -> str:
    """Bullet-mask any non-empty value. Used for keys/passwords in GET /admin/config."""
    return "••••••••" if value else ""


def _is_masked_value(value: str) -> bool:
    """Detect whether a posted secret is the bullet-masked version we sent on GET.

    The admin UI fills password/key fields with the masked value on load. When the
    user saves without re-typing the secret, the masked string comes back -- we
    must NOT save it (would blank out the real secret).
    """
    if not value:
        return False
    if "•" in value:
        return True
    # mojibake encodings of U+2022 sometimes leak through
    if "\xe2\x80\xa2" in value.encode("latin-1", errors="ignore").decode("latin-1", errors="ignore"):
        return True
    import re as _re
    if len(_re.findall(r"[^\x00-\x7F]", value)) >= 3:
        return True
    if value.strip() in ("********",):
        return True
    return False


@router.get("/config")
async def get_config():
    """Return the current settings (with secrets masked).

    In v2, settings come from /data/config.json (overlaid on env+defaults), not
    raw os.environ. We render the response in the v1-nested shape so the
    existing admin.html JS keeps working without modification.
    """
    from app.config import normalize_smtp_security, settings as _s

    try:
        try:
            smtp_security = normalize_smtp_security(_s.smtp_security)
        except ValueError:
            smtp_security = "starttls"

        config = {
            "timing": {
                "initial_delay_minutes": _s.notification_initial_delay_minutes,
                "extension_delay_minutes": _s.notification_extension_delay_minutes,
                "max_wait_minutes": _s.notification_max_wait_minutes,
                "check_frequency_seconds": _s.notification_check_frequency_seconds,
            },
            "smtp": {
                "host": _s.smtp_host or "",
                "port": str(_s.smtp_port),
                "security": smtp_security,
                "from": _s.smtp_from or "",
                "user": _s.smtp_user or "",
                "password": _mask_secret(_s.smtp_password),
            },
            "jellyseerr": {
                "url": _s.jellyseerr_url or "",
                "api_key": _mask_secret(_s.jellyseerr_api_key),
            },
            "sonarr": {
                "url": _s.sonarr_url or "",
                "api_key": _mask_secret(_s.sonarr_api_key),
            },
            "sonarr_anime": {
                "url": _s.sonarr_anime_url or "",
                "api_key": _mask_secret(_s.sonarr_anime_api_key),
            },
            "radarr": {
                "url": _s.radarr_url or "",
                "api_key": _mask_secret(_s.radarr_api_key),
            },
            "plex": {
                "url": _s.plex_url or "",
                "token": _mask_secret(_s.plex_token),
            },
            "quality_monitor": {
                "enabled": _s.quality_monitor_enabled,
                "interval_hours": _s.quality_monitor_interval_hours,
                "waiting_delay_seconds": _s.quality_waiting_delay_seconds,
            },
            "operations": {
                "service_health_enabled": _s.service_health_enabled,
                "service_health_interval_minutes": _s.service_health_interval_minutes,
                "service_health_failure_threshold": _s.service_health_failure_threshold,
                "service_health_alert_cooldown_minutes": _s.service_health_alert_cooldown_minutes,
                "service_health_email_alerts_enabled": _s.service_health_email_alerts_enabled,
                "service_health_history_days": _s.service_health_history_days,
                "alert_webhook_enabled": _s.alert_webhook_enabled,
                "alert_webhook_url": _mask_secret(_s.alert_webhook_url),
                "alert_webhook_type": _s.alert_webhook_type,
                "pushover_app_token": _mask_secret(_s.pushover_app_token),
                "pushover_user_key": _mask_secret(_s.pushover_user_key),
                "pushover_sound": _s.pushover_sound or "",
                "notification_retention_enabled": _s.notification_retention_enabled,
                "notification_retention_days": _s.notification_retention_days,
                "notification_retention_interval_hours": _s.notification_retention_interval_hours,
                "backup_schedule_enabled": _s.backup_schedule_enabled,
                "backup_schedule_interval_hours": _s.backup_schedule_interval_hours,
                "backup_schedule_retention_count": _s.backup_schedule_retention_count,
            },
            "issue_autofix": {
                "mode": _s.issue_autofix_mode,
            },
            "admin_email": _s.admin_email or "",
            "public_base_url": _s.public_base_url or "",
            "seerr_anime": {
                "server_id": _s.seerr_anime_server_id if _s.seerr_anime_server_id is not None else "",
                "profile_id": _s.seerr_anime_profile_id if _s.seerr_anime_profile_id is not None else "",
                "root_folder": _s.seerr_anime_root_folder or "",
            },
            "security": {
                "webhook_allowed_ips": _s.webhook_allowed_ips,
                "webhook_secret": _mask_secret(_s.webhook_secret),
                "trusted_proxy_cidrs": _s.trusted_proxy_cidrs,
                "environment": _s.environment,
                "secret_key_status": "strong" if _s.app_secret_key and len(_s.app_secret_key) >= 32 else "weak",
            },
        }
        
        # Load auth settings from database
        try:
            from app.auth import get_auth_settings
            from app.database import get_db
            db = next(get_db())
            try:
                auth_settings = get_auth_settings(db)
                config["auth"] = {
                    "enabled": auth_settings.get("auth_enabled", "false").lower() == "true",
                    "has_password": bool(auth_settings.get("auth_password_hash", "")),
                    "local_network_cidr": auth_settings.get("local_network_cidr", ""),
                    "session_timeout_hours": int(auth_settings.get("session_timeout_hours", "24")),
                    "turnstile_enabled": auth_settings.get("turnstile_enabled", "false").lower() == "true",
                    "turnstile_site_key": auth_settings.get("turnstile_site_key", ""),
                    "turnstile_secret_key": _mask_secret(auth_settings.get("turnstile_secret_key", "")) if auth_settings.get("turnstile_secret_key") else ""
                }
                
                # Reconciliation settings
                from app.background.reconciliation import get_reconciliation_settings
                recon = get_reconciliation_settings()
                config["reconciliation"] = recon
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to load auth settings: {e}")
            config["auth"] = {
                "enabled": False,
                "has_password": False,
                "local_network_cidr": "",
                "session_timeout_hours": 24,
                "turnstile_enabled": False,
                "turnstile_site_key": "",
                "turnstile_secret_key": ""
            }
            config["reconciliation"] = {
                "interval_hours": 2,
                "notification_lookback_days": _s.notification_retention_days,
                "issue_fixing_cutoff_hours": 1,
                "issue_reported_cutoff_hours": 24,
                "issue_abandon_days": 7,
            }
        
        return config
    except Exception as e:
        logger.error(f"Failed to get config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/config")
async def update_config(config: dict, db: Session = Depends(get_db)):
    """Persist settings updates to /data/config.json.

    Accepts the v1-shaped nested dict for admin.html JS compatibility, then
    flattens to v2 settings field names. Masked secrets (bullet-character or
    similar) are skipped so a partial save doesn\'t blank existing API keys.

    Some changes (auth password, local CIDR, app_secret_key, environment)
    only take effect after a container restart -- the in-memory settings
    singleton is rebuilt at process boot, not per-request.

    Reconciliation tunables remain in the system_config DB table for now;
    the worker reads them lazily each cycle so they apply without restart.
    """
    from app.auth import hash_password
    from app.config import normalize_smtp_security, settings as _s

    updates: dict = {}
    label_updates: list = []
    validation_errors: list[str] = []

    def take(json_path, settings_key, label=None, transform=None,
             secret=False, allow_empty=False):
        node = config
        for k in json_path:
            if not isinstance(node, dict) or k not in node:
                return
            node = node[k]
        if not allow_empty and (node is None or node == ""):
            return
        if secret and isinstance(node, str) and _is_masked_value(node):
            return
        try:
            updates[settings_key] = transform(node) if transform else node
        except (TypeError, ValueError):
            validation_errors.append(label or settings_key)
            return
        label_updates.append(label or settings_key.upper())

    def bounded_int(min_value: int, max_value: int):
        def _transform(value):
            number = int(value)
            if number < min_value or number > max_value:
                raise ValueError
            return number
        return _transform

    # SMTP / email
    take(["smtp", "host"], "smtp_host")
    take(["smtp", "port"], "smtp_port", transform=int)
    take(["smtp", "security"], "smtp_security", transform=normalize_smtp_security)
    take(["smtp", "from"], "smtp_from")
    take(["smtp", "user"], "smtp_user", allow_empty=True)
    take(["smtp", "password"], "smtp_password", secret=True)
    take(["admin_email"], "admin_email", transform=clean_email_address, allow_empty=True)

    # External-facing URL used for absolute links in outbound emails (calendar
    # subscribe footer; future password-reset). Validated here because the
    # value lands inside an <a href="..."> in the recipient's mailbox -- a
    # javascript: scheme would render as a clickable XSS vector. Trailing
    # slash trimmed; empty allowed (footer injection skips when blank).
    take(["public_base_url"], "public_base_url",
         transform=lambda v: normalize_http_url(v, allow_empty=True), allow_empty=True)

    # Notification batching / processor timing
    take(["timing", "initial_delay_minutes"], "notification_initial_delay_minutes",
         transform=bounded_int(1, 30))
    take(["timing", "extension_delay_minutes"], "notification_extension_delay_minutes",
         transform=bounded_int(1, 10))
    take(["timing", "max_wait_minutes"], "notification_max_wait_minutes",
         transform=bounded_int(5, 60))
    take(["timing", "check_frequency_seconds"], "notification_check_frequency_seconds",
         transform=bounded_int(30, 300))

    # Seerr / Sonarr / Radarr / Plex
    take(["jellyseerr", "url"], "jellyseerr_url", transform=normalize_http_url)
    take(["jellyseerr", "api_key"], "jellyseerr_api_key", secret=True)
    take(["sonarr", "url"], "sonarr_url", transform=normalize_http_url)
    take(["sonarr", "api_key"], "sonarr_api_key", secret=True)
    take(["sonarr_anime", "url"], "sonarr_anime_url",
         transform=lambda v: normalize_http_url(v, allow_empty=True), allow_empty=True)
    take(["sonarr_anime", "api_key"], "sonarr_anime_api_key", secret=True)
    take(["radarr", "url"], "radarr_url", transform=normalize_http_url)
    take(["radarr", "api_key"], "radarr_api_key", secret=True)
    take(["plex", "url"], "plex_url",
         transform=lambda v: normalize_http_url(v, allow_empty=True), allow_empty=True)
    take(["plex", "token"], "plex_token", secret=True)

    # Quality monitor + issue autofix
    take(["quality_monitor", "enabled"], "quality_monitor_enabled", transform=bool)
    take(["quality_monitor", "interval_hours"], "quality_monitor_interval_hours", transform=int)
    take(["quality_monitor", "waiting_delay_seconds"], "quality_waiting_delay_seconds", transform=int)
    take(["operations", "service_health_enabled"], "service_health_enabled", transform=bool)
    take(["operations", "service_health_interval_minutes"], "service_health_interval_minutes", transform=int)
    take(["operations", "service_health_failure_threshold"], "service_health_failure_threshold", transform=int)
    take(["operations", "service_health_alert_cooldown_minutes"], "service_health_alert_cooldown_minutes", transform=int)
    take(["operations", "service_health_email_alerts_enabled"], "service_health_email_alerts_enabled", transform=bool)
    take(["operations", "service_health_history_days"], "service_health_history_days", transform=int)
    take(["operations", "alert_webhook_enabled"], "alert_webhook_enabled", transform=bool)
    take(["operations", "alert_webhook_url"], "alert_webhook_url",
         transform=lambda v: normalize_http_url(v, allow_empty=True), secret=True, allow_empty=True)
    webhook_type = str(config.get("operations", {}).get("alert_webhook_type", "")).strip().lower()
    if webhook_type:
        if webhook_type not in {"generic", "discord", "slack", "pushover"}:
            validation_errors.append("ALERT_WEBHOOK_TYPE")
        else:
            updates["alert_webhook_type"] = webhook_type
            label_updates.append("ALERT_WEBHOOK_TYPE")
    take(["operations", "pushover_app_token"], "pushover_app_token", secret=True)
    take(["operations", "pushover_user_key"], "pushover_user_key", secret=True)
    take(["operations", "pushover_sound"], "pushover_sound", allow_empty=True)
    take(["operations", "notification_retention_enabled"], "notification_retention_enabled", transform=bool)
    take(["operations", "notification_retention_days"], "notification_retention_days", transform=int)
    take(["operations", "notification_retention_interval_hours"], "notification_retention_interval_hours", transform=int)
    take(["operations", "backup_schedule_enabled"], "backup_schedule_enabled", transform=bool)
    take(["operations", "backup_schedule_interval_hours"], "backup_schedule_interval_hours", transform=int)
    take(["operations", "backup_schedule_retention_count"], "backup_schedule_retention_count", transform=int)
    if config.get("issue_autofix", {}).get("mode") in ("manual", "auto", "auto_notify"):
        updates["issue_autofix_mode"] = config["issue_autofix"]["mode"]
        label_updates.append("ISSUE_AUTOFIX_MODE")

    # Seerr anime overrides
    take(["seerr_anime", "server_id"], "seerr_anime_server_id",
         transform=lambda v: int(v) if str(v).strip() else None, allow_empty=True)
    take(["seerr_anime", "profile_id"], "seerr_anime_profile_id",
         transform=lambda v: int(v) if str(v).strip() else None, allow_empty=True)
    take(["seerr_anime", "root_folder"], "seerr_anime_root_folder", allow_empty=True)

    # Security
    take(["security", "webhook_allowed_ips"], "webhook_allowed_ips",
         transform=validate_ip_or_cidr_csv, allow_empty=True)
    take(["security", "webhook_secret"], "webhook_secret", secret=True, allow_empty=True)
    take(["security", "trusted_proxy_cidrs"], "trusted_proxy_cidrs",
         transform=validate_ip_or_cidr_csv, allow_empty=True)
    if config.get("security", {}).get("environment") in ("production", "development"):
        updates["environment"] = config["security"]["environment"]
        label_updates.append("ENVIRONMENT")
    take(["security", "app_secret_key"], "app_secret_key", secret=True)

    # Auth -- bcrypt the password; map session timeout hours to seconds
    auth = config.get("auth", {})
    if auth:
        if "enabled" in auth:
            updates["auth_required"] = bool(auth["enabled"])
            label_updates.append("AUTH_REQUIRED")
        new_password = auth.get("password", "")
        if new_password and not _is_masked_value(new_password):
            updates["admin_password_hash"] = hash_password(new_password)
            label_updates.append("AUTH_PASSWORD")
        if "local_network_cidr" in auth:
            updates["local_network_cidrs"] = validate_ip_or_cidr_csv(auth["local_network_cidr"])
            label_updates.append("LOCAL_NETWORK_CIDRS")
        if "session_timeout_hours" in auth:
            try:
                updates["session_max_age_seconds"] = int(auth["session_timeout_hours"]) * 3600
                label_updates.append("SESSION_MAX_AGE_SECONDS")
            except (TypeError, ValueError):
                validation_errors.append("SESSION_TIMEOUT_HOURS")
        if "turnstile_site_key" in auth:
            updates["turnstile_site_key"] = auth["turnstile_site_key"] or None
            label_updates.append("TURNSTILE_SITE_KEY")
        ts_secret = auth.get("turnstile_secret_key", "")
        if ts_secret and not _is_masked_value(ts_secret):
            updates["turnstile_secret_key"] = ts_secret
            label_updates.append("TURNSTILE_SECRET_KEY")

    # Reconciliation -- still in system_config (lazy-read each cycle, no restart)
    if "reconciliation" in config:
        try:
            recon = config["reconciliation"]
            recon_fields = {
                "reconciliation_interval_hours": "interval_hours",
                "reconciliation_notification_lookback_days": "notification_lookback_days",
                "reconciliation_issue_fixing_cutoff_hours": "issue_fixing_cutoff_hours",
                "reconciliation_issue_reported_cutoff_hours": "issue_reported_cutoff_hours",
                "reconciliation_issue_abandon_days": "issue_abandon_days",
            }
            for db_key, json_key in recon_fields.items():
                if json_key in recon:
                    val = str(int(recon[json_key]))
                    existing = db.query(SystemConfig).filter(SystemConfig.key == db_key).first()
                    if existing:
                        existing.value = val
                        existing.updated_at = datetime.utcnow()
                    else:
                        db.add(SystemConfig(key=db_key, value=val))
                    label_updates.append(db_key.upper())
            db.commit()
        except Exception as e:
            logger.error(f"Failed to save reconciliation settings: {e}")
            db.rollback()

    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid setting value(s): {', '.join(validation_errors)}",
        )

    if updates:
        from app.config import reload_from_disk
        try:
            _s.write_to_disk(updates)
        except OSError as e:
            logger.error(f"failed writing config.json: {e}")
            raise HTTPException(status_code=500, detail=f"Could not write config.json: {e}")
        # Refresh the in-memory settings singleton so subsequent reads (this
        # request's GET, AuthMiddleware, background workers) see the new
        # values without a container restart.
        reload_from_disk()

    logger.info(f"config updated: {', '.join(label_updates)}")
    record_admin_activity(
        "config_update",
        f"Updated {len(label_updates)} setting(s)",
        details={"fields": label_updates},
        db=db,
    )
    db.commit()
    return {
        "success": True,
        "message": (
            f"Updated {len(label_updates)} settings. Most changes are live now; "
            "restart the container if you changed the database URL or want background "
            "workers to re-create their HTTP clients with new credentials."
        ),
        "updated_fields": label_updates,
    }


@router.post("/restart")
async def restart_container():
    """Restart the Docker container (requires Docker socket access)"""
    import os
    import subprocess
    
    try:
        # Get container ID from environment or hostname
        container_id = os.getenv('HOSTNAME')
        
        if not container_id:
            raise HTTPException(status_code=500, detail="Cannot determine container ID")
        
        # Restart the container using Docker API
        # Note: This requires the Docker socket to be mounted
        result = subprocess.run(
            ['docker', 'restart', container_id],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.info(f"Container {container_id} restart initiated")
            record_admin_activity("container_restart", "Container restart initiated")
            return {"success": True, "message": "Container restart initiated"}
        else:
            raise HTTPException(status_code=500, detail=f"Restart failed: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        # Timeout is actually good - means restart started
        logger.info("Container restart command sent (timeout expected)")
        record_admin_activity("container_restart", "Container restart initiated")
        return {"success": True, "message": "Container restart initiated"}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Docker CLI not available in container")
    except Exception as e:
        logger.error(f"Failed to restart container: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/reconcile")
async def trigger_reconciliation():
    """Manually trigger reconciliation check"""
    try:
        from app.background.reconciliation import run_reconciliation
        import asyncio
        
        # Run reconciliation in background
        asyncio.create_task(run_reconciliation())
        record_admin_activity("reconciliation_manual", "Manual reconciliation started")
        
        return {
            "success": True,
            "message": "Reconciliation started - check logs for results"
        }
    except Exception as e:
        logger.error(f"Failed to start reconciliation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _docker_self():
    """Open the Docker SDK against /var/run/docker.sock and return our own container.

    Uses HOSTNAME (docker sets this to the short container ID) -- works inside
    any normally-launched container. Raises HTTPException if the socket is not
    mounted or HOSTNAME does not resolve.
    """
    import docker
    from docker.errors import DockerException, NotFound

    try:
        client = docker.from_env()
    except DockerException as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "Docker socket unavailable. Mount /var/run/docker.sock into the "
                f"container to enable log access. ({e})"
            ),
        )
    container_id = os.environ.get("HOSTNAME", "")
    if not container_id:
        raise HTTPException(
            status_code=500, detail="HOSTNAME not set; cannot identify own container"
        )
    try:
        return client.containers.get(container_id)
    except NotFound:
        raise HTTPException(
            status_code=503,
            detail=f"Container {container_id} not found via Docker socket",
        )


@router.get("/logs")
async def get_logs(lines: int = 100):
    """Return the last `lines` lines of this container\'s stdout+stderr."""
    try:
        container = _docker_self()
        raw = container.logs(tail=lines, stdout=True, stderr=True, timestamps=False)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        return {
            "success": True,
            "logs": text,
            "lines": text.count("\n"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to read logs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logs/stream")
async def stream_logs():
    """Stream this container\'s logs as Server-Sent Events.

    docker.Client.logs(stream=True, follow=True) returns a blocking byte
    iterator. We pump it in a thread and feed an asyncio queue so the event
    loop stays responsive.
    """
    from fastapi.responses import StreamingResponse

    container = _docker_self()  # raises HTTPException on infra problems

    async def log_generator():
        import asyncio
        import queue
        import threading

        q: queue.Queue = queue.Queue(maxsize=512)
        sentinel = object()

        def pump():
            try:
                for chunk in container.logs(
                    stream=True, follow=True, tail=50, stdout=True, stderr=True
                ):
                    q.put(chunk)
            except Exception as e:
                q.put(f"[log stream ended: {e}]\n".encode())
            finally:
                q.put(sentinel)

        threading.Thread(target=pump, daemon=True).start()
        loop = asyncio.get_event_loop()
        while True:
            chunk = await loop.run_in_executor(None, q.get)
            if chunk is sentinel:
                break
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
            for line in chunk.splitlines():
                yield f"data: {line}\n\n"

    return StreamingResponse(log_generator(), media_type="text/event-stream")


@router.post("/notifications/mark-old-as-sent")
async def mark_old_notifications_as_sent(hours_old: int = 24, db: Session = Depends(get_db)):
    """Mark old notifications as sent without emailing them"""
    try:
        from datetime import datetime, timedelta
        
        cutoff = datetime.utcnow() - timedelta(hours=hours_old)
        
        # Find old pending notifications
        old_notifications = db.query(Notification).filter(
            Notification.sent == False,
            Notification.created_at < cutoff
        ).all()
        
        count = len(old_notifications)
        
        # Mark them as sent
        for notif in old_notifications:
            notif.sent = True
            notif.sent_at = datetime.utcnow()
        record_admin_activity(
            "notification_mark_old_sent",
            f"Marked {count} old notification(s) as sent",
            details={"hours_old": hours_old, "count": count},
            db=db,
        )
        db.commit()
        
        logger.info(f"Marked {count} old notifications as sent (older than {hours_old} hours)")
        
        return {
            "success": True,
            "message": f"Marked {count} old notifications as sent",
            "count": count,
            "cutoff_hours": hours_old
        }
        
    except Exception as e:
        logger.error(f"Failed to mark old notifications: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/notifications/clear-all-pending")
async def clear_all_pending_notifications(db: Session = Depends(get_db)):
    """Mark ALL pending notifications as sent without emailing them"""
    try:
        from datetime import datetime
        
        # Find ALL pending notifications
        pending_notifications = db.query(Notification).filter(
            Notification.sent == False
        ).all()
        
        count = len(pending_notifications)
        
        # Mark them as sent
        for notif in pending_notifications:
            notif.sent = True
            notif.sent_at = datetime.utcnow()
        record_admin_activity(
            "notification_clear_pending",
            f"Marked {count} pending notification(s) as sent",
            details={"count": count},
            db=db,
        )
        db.commit()
        
        logger.info(f"Marked {count} pending notifications as sent (admin override)")
        
        return {
            "success": True,
            "message": f"Marked {count} pending notifications as sent",
            "count": count
        }
        
    except Exception as e:
        logger.error(f"Failed to clear pending notifications: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/notifications/purge-sent")
async def purge_sent_notifications(days_old: int = 90, db: Session = Depends(get_db)):
    """Delete sent notifications older than the requested retention window."""
    try:
        from app.background.ops_maintenance import purge_sent_notifications as purge_sent

        if days_old < 1:
            raise HTTPException(status_code=400, detail="days_old must be at least 1")
        if days_old > 3650:
            raise HTTPException(status_code=400, detail="days_old must be 3650 or less")

        deleted = purge_sent(db, days_old)
        record_admin_activity(
            "notification_purge",
            f"Purged {deleted} sent notification(s)",
            details={"days_old": days_old, "count": deleted},
            db=db,
        )
        db.commit()

        logger.info("Purged %s sent notification(s) older than %s days", deleted, days_old)
        return {
            "success": True,
            "message": f"Purged {deleted} sent notification(s)",
            "count": deleted,
            "days_old": days_old,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to purge sent notifications: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/send-weekly-summary")
async def send_weekly_summary_now():
    """Manually trigger weekly summary email"""
    try:
        from app.background.weekly_summary import send_weekly_summary
        import asyncio
        
        # Run summary in background
        asyncio.create_task(send_weekly_summary())
        record_admin_activity("weekly_summary_manual", "Manual weekly summary started")
        
        return {
            "success": True,
            "message": "Weekly summary email will be sent shortly - check your inbox!"
        }
    except Exception as e:
        logger.error(f"Failed to send weekly summary: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/check-stuck-downloads")
async def check_stuck_downloads_now():
    """Manually trigger stuck download check"""
    try:
        from app.background.stuck_monitor import check_and_alert_stuck_downloads
        import asyncio
        
        # Run check in background
        asyncio.create_task(check_and_alert_stuck_downloads())
        record_admin_activity("stuck_download_check_manual", "Manual stuck download check started")
        
        return {
            "success": True,
            "message": "Checking for stuck downloads - you'll get an email if any are found"
        }
    except Exception as e:
        logger.error(f"Failed to check stuck downloads: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/check-quality-release")
async def manual_quality_release_check():
    """Manually trigger quality/release monitoring check"""
    try:
        from app.background.quality_monitor import run_quality_release_monitor
        
        logger.info("Manual quality/release check triggered")
        
        # Run the check
        await run_quality_release_monitor()
        record_admin_activity("quality_release_check_manual", "Manual quality/release check completed")
        
        return {
            "success": True,
            "message": "Quality/release check completed! Notifications sent for unreleased content and quality mismatches."
        }
    except Exception as e:
        logger.error(f"Failed to run quality/release check: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ===== Issues Management =====

@router.get("/issues")
async def get_issues(db: Session = Depends(get_db)):
    """Get all reported issues"""
    try:
        from app.database import ReportedIssue
        
        issues = db.query(ReportedIssue).order_by(ReportedIssue.created_at.desc()).all()
        
        result = []
        for issue in issues:
            result.append({
                "id": issue.id,
                "seerr_issue_id": issue.seerr_issue_id,
                "title": issue.title,
                "media_type": issue.media_type,
                "tmdb_id": issue.tmdb_id,
                "issue_type": issue.issue_type,
                "issue_message": issue.issue_message,
                "season_number": issue.season_number,
                "episode_number": issue.episode_number,
                "status": issue.status,
                "action_taken": issue.action_taken,
                "error_message": issue.error_message,
                "reported_by": issue.user.username if issue.user else "Unknown",
                "reported_by_email": issue.user.email if issue.user else None,
                "created_at": issue.created_at.isoformat() if issue.created_at else None,
                "resolved_at": issue.resolved_at.isoformat() if issue.resolved_at else None,
            })
        
        return result
    except Exception as e:
        logger.error(f"Failed to get issues: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/issues/{issue_id}/fix")
async def fix_issue(issue_id: int, db: Session = Depends(get_db)):
    """Manually trigger blacklist + re-search for a reported issue"""
    try:
        from app.database import ReportedIssue
        
        issue = db.query(ReportedIssue).filter(ReportedIssue.id == issue_id).first()
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        if issue.status == "resolved":
            return {"success": False, "message": "Issue is already resolved"}
        
        issue.status = "fixing"
        db.commit()
        
        # Trigger blacklist + re-search
        if issue.media_type == "movie":
            from app.services.radarr_service import RadarrService
            radarr = RadarrService()
            result = await radarr.blacklist_and_research_movie(issue.tmdb_id)
        elif issue.media_type == "tv":
            from app.services.sonarr_service import SonarrService, get_all_sonarr_instances
            result = {"success": False, "message": "Series not found in any Sonarr instance"}
            for sonarr_svc in get_all_sonarr_instances():
                r = await sonarr_svc.blacklist_and_research_series(
                    issue.tmdb_id,
                    season_number=issue.season_number,
                    episode_number=issue.episode_number,
                )
                if r["success"]:
                    result = r
                    break
        else:
            result = {"success": False, "message": "Unknown media type"}
        
        fix_succeeded = bool(result["success"])
        
        if fix_succeeded:
            issue.action_taken = "blacklist_research"
            logger.info(f"Manual fix initiated for issue #{issue.id}: {result['message']}")
            client_message = "Fix initiated — file blacklisted and new search triggered"
        else:
            issue.status = "failed"
            issue.error_message = result["message"]
            logger.error(f"Manual fix failed for issue #{issue.id}: {result['message']}")
            client_message = "Fix failed — check logs for details"
        
        db.commit()
        
        return {
            "success": fix_succeeded,
            "message": client_message
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fix issue {issue_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/issues/{issue_id}/resolve")
async def resolve_issue(issue_id: int, db: Session = Depends(get_db)):
    """Manually mark an issue as resolved (without re-downloading)"""
    try:
        from app.database import ReportedIssue
        from datetime import datetime
        
        issue = db.query(ReportedIssue).filter(ReportedIssue.id == issue_id).first()
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        issue.status = "resolved"
        issue.action_taken = "manual"
        issue.resolved_at = datetime.utcnow()
        db.commit()
        
        # Close the issue in Seerr too
        seerr_message = ""
        if issue.seerr_issue_id:
            try:
                from app.services.seerr_service import SeerrService
                seerr = SeerrService()
                result = await seerr.resolve_issue(issue.seerr_issue_id)
                if result["success"]:
                    seerr_message = " (also closed in Seerr)"
                else:
                    seerr_message = " (Seerr close failed)"
                    logger.warning(f"Seerr close failed for issue {issue_id}: {result['message']}")
            except Exception as e:
                seerr_message = " (Seerr close failed)"
                logger.warning(f"Seerr close failed for issue {issue_id}: {e}")
        
        return {"success": True, "message": f"Issue #{issue_id} marked as resolved{seerr_message}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve issue {issue_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/issues/{issue_id}")
async def delete_issue(issue_id: int, db: Session = Depends(get_db)):
    """Delete a reported issue"""
    try:
        from app.database import ReportedIssue
        
        issue = db.query(ReportedIssue).filter(ReportedIssue.id == issue_id).first()
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        db.delete(issue)
        db.commit()
        
        return {"success": True, "message": f"Issue #{issue_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete issue {issue_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/requests/{request_id}/notify-shared-user/{user_id}")
async def notify_shared_user_about_existing(request_id: int, user_id: int, db: Session = Depends(get_db)):
    """Send notifications to a newly added shared user for already-downloaded episodes"""
    try:
        from app.services.email_service import EmailService
        from app.database import EpisodeTracking, SharedRequest
        
        # Verify the share exists
        shared = db.query(SharedRequest).filter(
            SharedRequest.request_id == request_id,
            SharedRequest.user_id == user_id
        ).first()
        
        if not shared:
            raise HTTPException(status_code=404, detail="User is not shared on this request")
        
        # Get the request
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Get user
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Find all downloaded episodes for this request that haven't been notified to this user
        email_service = EmailService()
        episodes_sent = 0
        
        if request.media_type == 'tv':
            # Get all tracked episodes that are downloaded
            tracked_episodes = db.query(EpisodeTracking).filter(
                EpisodeTracking.request_id == request_id,
                EpisodeTracking.available == True
            ).all()
            
            if tracked_episodes:
                # Group by season for batch sending
                from collections import defaultdict
                episodes_by_season = defaultdict(list)
                
                for ep in tracked_episodes:
                    episodes_by_season[ep.season_number].append({
                        'season': ep.season_number,
                        'episode': ep.episode_number,
                        'title': ep.episode_title or 'TBA'
                    })
                
                # Send notification for each season's episodes
                for season, eps in episodes_by_season.items():
                    try:
                        await email_service.send_episode_notification(
                            user_email=user.email,
                            user_name=user.username,
                            series_title=request.title,
                            episodes=eps
                        )
                        episodes_sent += len(eps)
                    except Exception as e:
                        logger.error(f"Failed to send notification: {e}")
        
        elif request.media_type == 'movie' and request.status == 'available':
            # Send movie notification
            try:
                await email_service.send_movie_notification(
                    user_email=user.email,
                    user_name=user.username,
                    movie_title=request.title,
                    movie_year=request.year
                )
                episodes_sent = 1
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")
        
        if episodes_sent > 0:
            return {
                "success": True,
                "message": f"Sent {episodes_sent} notification(s) to {user.username}",
                "episodes_sent": episodes_sent
            }
        else:
            return {
                "success": True,
                "message": "No downloaded content to notify about",
                "episodes_sent": 0
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to notify shared user: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# NOTE: a duplicate @router.post("/requests/{request_id}/share") used to live
# here, taking a JSON body and constructing SharedRequest(shared_at=...) -- a
# column name that doesn't exist (the model has `added_at`). The duplicate
# made FastAPI's resolution non-deterministic and any time the second handler
# won the race, the SharedRequest construction crashed with a 500. Removed
# entirely; the canonical handler at line ~938 reads user_id from the query
# string, which matches what admin.html actually sends.

@router.post("/request-on-behalf")
async def request_on_behalf(
    data: dict,
    db: Session = Depends(get_db)
):
    """Create a request in Jellyseerr on behalf of a user"""
    try:
        jellyseerr_id = data.get('jellyseerr_user_id')  # Frontend sends jellyseerr_user_id
        tmdb_id = data.get('tmdb_id')
        media_type = data.get('media_type')  # 'movie' or 'tv'
        
        if not all([jellyseerr_id, tmdb_id, media_type]):
            raise HTTPException(status_code=400, detail="jellyseerr_user_id, tmdb_id, and media_type are required")
        
        # Check if user exists (using jellyseerr_id field in database)
        user = db.query(User).filter(User.jellyseerr_id == jellyseerr_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Create request in Jellyseerr
        from app.config import settings
        import httpx
        
        jellyseerr_url = normalize_http_url(settings.jellyseerr_url)
        api_key = settings.jellyseerr_api_key
        
        # First, get the media details
        media_endpoint = f"{jellyseerr_url}/api/v1/{'movie' if media_type == 'movie' else 'tv'}/{tmdb_id}"
        
        async with httpx.AsyncClient() as client:
            # Get media details
            media_response = await client.get(
                media_endpoint,
                headers={"X-Api-Key": api_key}
            )
            media_response.raise_for_status()
            media_data = media_response.json()
            
            # Auto-detect anime from TMDB data
            is_anime = False
            if media_type == 'tv':
                genres = [g.get('name', '').lower() for g in media_data.get('genres', [])]
                origin_countries = [c.lower() for c in (media_data.get('origin_country', []) or [])]
                # Also check keywords if available
                keywords = [k.get('name', '').lower() for k in media_data.get('keywords', [])]
                
                # Anime detection: Animation genre + Japanese origin, or 'anime' keyword
                has_animation = 'animation' in genres
                is_japanese = 'jp' in origin_countries
                has_anime_keyword = 'anime' in keywords
                
                is_anime = (has_animation and is_japanese) or has_anime_keyword
                
                if is_anime:
                    title = media_data.get('name') or media_data.get('title') or 'Unknown'
                    logger.info(f"Auto-detected anime: {title} (genres={genres}, origin={origin_countries})")
            
            # Create request
            request_payload = {
                "mediaType": media_type,
                "mediaId": tmdb_id,
                "userId": jellyseerr_id  # Use the jellyseerr_id
            }
            
            if media_type == 'tv':
                request_payload["seasons"] = "all"
            
            # Apply anime overrides if detected and configured
            if is_anime and settings.seerr_anime_server_id:
                request_payload["serverId"] = settings.seerr_anime_server_id
                logger.info(f"Routing to anime Sonarr server ID: {settings.seerr_anime_server_id}")
                
                if settings.seerr_anime_profile_id:
                    request_payload["profileId"] = settings.seerr_anime_profile_id
                    logger.info(f"Using anime quality profile ID: {settings.seerr_anime_profile_id}")
                
                if settings.seerr_anime_root_folder:
                    request_payload["rootFolder"] = settings.seerr_anime_root_folder
                    logger.info(f"Using anime root folder: {settings.seerr_anime_root_folder}")
            
            request_response = await client.post(
                f"{jellyseerr_url}/api/v1/request",
                headers={"X-Api-Key": api_key},
                json=request_payload
            )
            request_response.raise_for_status()
            request_data = request_response.json()
        
        title = media_data.get('title') or media_data.get('name') or 'Unknown'
        anime_note = " (routed to anime Sonarr)" if is_anime and settings.seerr_anime_server_id else ""
        logger.info(f"Created request for {title} on behalf of {user.email}{anime_note}")
        
        return {
            "success": True,
            "message": f"Request created successfully{'  🎌 Routed to anime Sonarr' if is_anime and settings.seerr_anime_server_id else ''}",
            "jellyseerr_request_id": request_data.get('id'),
            "is_anime": is_anime
        }
        
    except httpx.HTTPError as e:
        logger.error(f"Jellyseerr API error: {e}")
        raise HTTPException(status_code=500, detail=f"Jellyseerr error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create request on behalf: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/seerr-sonarr-servers")
async def get_seerr_sonarr_servers():
    """Fetch configured Sonarr servers from Jellyseerr/Seerr to discover server IDs and profiles"""
    try:
        import httpx
        from app.config import settings
        
        jellyseerr_url = normalize_http_url(settings.jellyseerr_url)
        api_key = settings.jellyseerr_api_key
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{jellyseerr_url}/api/v1/settings/sonarr",
                headers={"X-Api-Key": api_key}
            )
            response.raise_for_status()
            servers = response.json()
        
        # Return simplified server info for the UI
        result = []
        for server in servers:
            result.append({
                "id": server.get("id"),
                "name": server.get("name"),
                "hostname": server.get("hostname"),
                "port": server.get("port"),
                "is4k": server.get("is4k", False),
                "isDefault": server.get("isDefault", False),
                "activeProfileId": server.get("activeProfileId"),
                "activeProfileName": server.get("activeProfileName"),
                "activeDirectory": server.get("activeDirectory"),
            })
        
        return {"success": True, "servers": result}
        
    except Exception as e:
        logger.error(f"Failed to fetch Seerr Sonarr servers: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch servers: {str(e)}")


@router.post("/test-smtp")
async def test_email_connection(data: dict):
    """Test SMTP email connection"""
    try:
        from app.config import normalize_smtp_security
        import smtplib
        
        host = data.get('host')
        port = int(data.get('port') or 587)
        security = normalize_smtp_security(data.get('security', 'starttls'))
        user = data.get('user')
        password = data.get('password')
        
        # Test connection
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if security == "starttls":
                server.starttls()
        try:
            if user or password:
                server.login(user, password)
        finally:
            server.quit()
        
        return {"success": True, "message": "SMTP connection successful!"}
        
    except Exception as e:
        logger.error(f"SMTP test failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-pushover")
async def test_pushover_connection(data: dict):
    """Send a Pushover test notification using posted or saved credentials."""
    try:
        from app.config import settings as _s
        from app.services.pushover_service import PushoverService

        app_token = str(data.get("app_token") or "").strip()
        user_key = str(data.get("user_key") or "").strip()
        sound = str(data.get("sound") or "").strip()

        if not app_token or _is_masked_value(app_token):
            app_token = _s.pushover_app_token or ""
        if not user_key or _is_masked_value(user_key):
            user_key = _s.pushover_user_key or ""

        if not app_token or not user_key:
            raise HTTPException(
                status_code=400,
                detail="Pushover app token and user/group key are required",
            )

        success = await PushoverService().send(
            title="BingeAlert test push",
            message="Pushover alerts are connected.",
            priority=0,
            app_token=app_token,
            user_key=user_key,
            sound=sound or None,
            require_enabled=False,
        )
        if not success:
            raise HTTPException(status_code=500, detail="Pushover test failed")

        record_admin_activity("test_pushover", "Sent Pushover test notification")
        return {"success": True, "message": "Pushover test notification sent"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pushover test failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-jellyseerr")
async def test_jellyseerr_connection(data: dict):
    """Test Jellyseerr API connection"""
    try:
        import httpx
        
        url = normalize_http_url(data.get('url', ''))
        api_key = data.get('api_key')
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{url}/api/v1/status",
                headers={"X-Api-Key": api_key}
            )
            response.raise_for_status()
            
        return {"success": True, "message": "Jellyseerr connection successful!"}
        
    except Exception as e:
        logger.error(f"Jellyseerr test failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-sonarr")
async def test_sonarr_connection(data: dict):
    """Test Sonarr API connection"""
    try:
        import httpx
        
        url = normalize_http_url(data.get('url', ''))
        api_key = data.get('api_key')
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{url}/api/v3/system/status",
                headers={"X-Api-Key": api_key}
            )
            response.raise_for_status()
            
        return {"success": True, "message": "Sonarr connection successful!"}
        
    except Exception as e:
        logger.error(f"Sonarr test failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-radarr")
async def test_radarr_connection(data: dict):
    """Test Radarr API connection"""
    try:
        import httpx
        
        url = normalize_http_url(data.get('url', ''))
        api_key = data.get('api_key')
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{url}/api/v3/system/status",
                headers={"X-Api-Key": api_key}
            )
            response.raise_for_status()
            
        return {"success": True, "message": "Radarr connection successful!"}
        
    except Exception as e:
        logger.error(f"Radarr test failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# NOTE (v2): the three /setup-* endpoints below originally read/wrote a
# `setup_complete` flag in the system_config table. In v2 the existence of
# /data/config.json (and is_minimally_configured) IS the source of truth.
# These bodies now delegate to settings so the v1 admin.html can keep calling
# them without the result drifting from reality.

@router.post("/setup-complete")
async def mark_setup_complete():
    """No-op in v2 -- /data/config.json existence is the setup flag."""
    from app.config import settings as _s
    return {"success": True, "message": "Setup state derives from /data/config.json", "configured": _s.is_minimally_configured()}


@router.get("/setup-status")
async def get_setup_status():
    from app.config import settings as _s
    configured = _s.is_minimally_configured()
    return {"setup_complete": configured, "needs_setup": not configured}


@router.post("/skip-setup")
async def skip_setup():
    """No-op in v2 -- there's nothing to skip; config.json is required."""
    from app.config import settings as _s
    return {"success": True, "message": "Setup state derives from /data/config.json", "configured": _s.is_minimally_configured()}


# ──────────────────────────────────────
# Maintenance Window Endpoints
# ──────────────────────────────────────

@router.get("/maintenance")
async def list_maintenance_windows(db: Session = Depends(get_db)):
    """List all maintenance windows"""
    try:
        windows = db.query(MaintenanceWindow).order_by(MaintenanceWindow.start_time.desc()).all()
        return [{
            "id": w.id,
            "title": w.title,
            "description": w.description,
            "start_time": w.start_time.isoformat() if w.start_time else None,
            "end_time": w.end_time.isoformat() if w.end_time else None,
            "status": w.status,
            "announcement_sent": w.announcement_sent,
            "reminder_sent": w.reminder_sent,
            "completion_sent": w.completion_sent,
            "cancelled": w.cancelled,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        } for w in windows]
    except Exception as e:
        logger.error(f"Failed to list maintenance windows: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/maintenance")
async def create_maintenance_window(data: dict, db: Session = Depends(get_db)):
    """Create a new maintenance window and send announcement email to all users"""
    try:
        title = data.get("title", "").strip()
        description = data.get("description", "").strip()
        start_time_str = data.get("start_time")
        end_time_str = data.get("end_time")
        send_announcement = data.get("send_announcement", True)
        
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        if not start_time_str or not end_time_str:
            raise HTTPException(status_code=400, detail="Start time and end time are required")
        
        # Parse datetime strings
        try:
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00")).replace(tzinfo=None)
            end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, AttributeError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid datetime format: {e}")
        
        if end_time <= start_time:
            raise HTTPException(status_code=400, detail="End time must be after start time")
        
        # Create window
        window = MaintenanceWindow(
            title=title,
            description=description if description else None,
            start_time=start_time,
            end_time=end_time,
            status="scheduled"
        )
        db.add(window)
        db.commit()
        db.refresh(window)
        
        logger.info(
            "Created maintenance window '%s' (%s - %s)",
            sanitize_for_log(title),
            start_time,
            end_time,
        )
        
        # Send announcement email
        email_result = None
        if send_announcement:
            email_service = EmailService()
            email_result = await email_service.send_maintenance_email_to_all_users(db, "announcement", window)
            window.announcement_sent = True
            db.commit()
        
        return {
            "success": True,
            "message": f"Maintenance window created{' and announcement sent' if send_announcement else ''}",
            "id": window.id,
            "email_result": email_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create maintenance window: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create maintenance window: {str(e)}")


@router.put("/maintenance/{window_id}")
async def update_maintenance_window(window_id: int, data: dict, db: Session = Depends(get_db)):
    """Update a maintenance window (reschedule). Optionally sends update email."""
    try:
        window = db.query(MaintenanceWindow).filter(MaintenanceWindow.id == window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Maintenance window not found")
        
        if window.status in ("completed", "cancelled"):
            raise HTTPException(status_code=400, detail="Cannot update a completed or cancelled window")
        
        if "title" in data and data["title"].strip():
            window.title = data["title"].strip()
        if "description" in data:
            window.description = data["description"].strip() if data["description"] else None
        
        if "start_time" in data:
            try:
                window.start_time = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="Invalid start_time format")
        
        if "end_time" in data:
            try:
                window.end_time = datetime.fromisoformat(data["end_time"].replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="Invalid end_time format")
        
        if window.end_time <= window.start_time:
            raise HTTPException(status_code=400, detail="End time must be after start time")
        
        window.updated_at = datetime.utcnow()
        
        # Reset reminder if rescheduled to the future
        if window.start_time > datetime.utcnow():
            window.reminder_sent = False
            window.status = "scheduled"
        
        db.commit()
        
        # Optionally send update announcement
        email_result = None
        if data.get("send_update_email", False):
            email_service = EmailService()
            email_result = await email_service.send_maintenance_email_to_all_users(db, "announcement", window)
            window.announcement_sent = True
            db.commit()
        
        logger.info(
            "Updated maintenance window '%s' (id=%s)",
            sanitize_for_log(window.title),
            window_id,
        )
        return {
            "success": True,
            "message": "Maintenance window updated",
            "email_result": email_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update maintenance window: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/maintenance/{window_id}/complete")
async def complete_maintenance_window(window_id: int, db: Session = Depends(get_db)):
    """Manually mark maintenance as complete (early completion) and send completion email"""
    try:
        window = db.query(MaintenanceWindow).filter(MaintenanceWindow.id == window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Maintenance window not found")
        
        if window.status == "completed":
            raise HTTPException(status_code=400, detail="Window is already completed")
        if window.cancelled:
            raise HTTPException(status_code=400, detail="Window was cancelled")
        
        # Send completion email
        email_service = EmailService()
        email_result = await email_service.send_maintenance_email_to_all_users(db, "complete", window)
        
        window.status = "completed"
        window.completion_sent = True
        window.updated_at = datetime.utcnow()
        db.commit()
        
        logger.info(
            "Manually completed maintenance window '%s' (id=%s)",
            sanitize_for_log(window.title),
            window_id,
        )
        return {
            "success": True,
            "message": "Maintenance marked as complete — emails sent",
            "email_result": email_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to complete maintenance window: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/maintenance/{window_id}/cancel")
async def cancel_maintenance_window(window_id: int, data: dict = None, db: Session = Depends(get_db)):
    """Cancel a maintenance window and optionally send cancellation email"""
    try:
        window = db.query(MaintenanceWindow).filter(MaintenanceWindow.id == window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Maintenance window not found")
        
        if window.status == "completed":
            raise HTTPException(status_code=400, detail="Cannot cancel a completed window")
        
        send_email = True
        if data and "send_email" in data:
            send_email = data["send_email"]
        
        # Send cancellation email if announcement was sent
        email_result = None
        if send_email and window.announcement_sent:
            email_service = EmailService()
            email_result = await email_service.send_maintenance_email_to_all_users(db, "cancelled", window)
        
        window.cancelled = True
        window.status = "cancelled"
        window.updated_at = datetime.utcnow()
        db.commit()
        
        logger.info(
            "Cancelled maintenance window '%s' (id=%s)",
            sanitize_for_log(window.title),
            window_id,
        )
        return {
            "success": True,
            "message": f"Maintenance window cancelled{' — cancellation emails sent' if email_result else ''}",
            "email_result": email_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel maintenance window: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/maintenance/{window_id}")
async def delete_maintenance_window(window_id: int, db: Session = Depends(get_db)):
    """Delete a maintenance window (no email sent)"""
    try:
        window = db.query(MaintenanceWindow).filter(MaintenanceWindow.id == window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Maintenance window not found")
        
        title = window.title
        db.delete(window)
        db.commit()
        
        logger.info(
            "Deleted maintenance window '%s' (id=%s)",
            sanitize_for_log(title),
            window_id,
        )
        return {"success": True, "message": f"Maintenance window '{title}' deleted"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete maintenance window: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/maintenance/{window_id}/send-reminder")
async def send_maintenance_reminder(window_id: int, db: Session = Depends(get_db)):
    """Manually send a reminder email for a maintenance window"""
    try:
        window = db.query(MaintenanceWindow).filter(MaintenanceWindow.id == window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Maintenance window not found")
        
        if window.cancelled or window.status == "completed":
            raise HTTPException(status_code=400, detail="Cannot send reminder for cancelled/completed window")
        
        email_service = EmailService()
        email_result = await email_service.send_maintenance_email_to_all_users(db, "reminder", window)
        
        window.reminder_sent = True
        db.commit()
        
        logger.info("Manually sent reminder for maintenance window '%s'", sanitize_for_log(window.title))
        return {
            "success": True,
            "message": "Reminder emails sent",
            "email_result": email_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send maintenance reminder: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
