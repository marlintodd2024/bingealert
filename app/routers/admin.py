from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging
import os
from datetime import datetime

from app.database import get_db, User, MediaRequest, EpisodeTracking, Notification, SharedRequest, SystemConfig
from app.services.jellyseerr_sync import JellyseerrSyncService
from app.services.email_service import EmailService

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
        return {"success": True, "message": "Notifications processed"}
    except Exception as e:
        logger.error(f"Notification processing failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/stats")
async def get_stats(db: Session = Depends(get_db)):
    """Get system statistics"""
    try:
        stats = {
            "users": db.query(func.count(User.id)).scalar(),
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
            }
        }
        return stats
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
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
                "created_at": u.created_at.isoformat() + 'Z' if u.created_at else None
            }
            for u in users
        ]
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
    """Get upcoming episodes from Sonarr calendar that match user requests"""
    try:
        from app.services.sonarr_service import SonarrService
        from app.database import EpisodeTracking
        from datetime import datetime, timedelta
        
        sonarr = SonarrService()
        
        # Get calendar for next N days
        start_date = datetime.utcnow().strftime('%Y-%m-%d')
        end_date = (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')
        
        logger.info(f"Fetching Sonarr calendar from {start_date} to {end_date}")
        calendar_episodes = await sonarr.get_calendar(start_date, end_date)
        
        if not calendar_episodes:
            logger.warning("No episodes returned from Sonarr calendar")
            return {"upcoming": [], "count": 0}
        
        logger.info(f"Found {len(calendar_episodes)} episodes in Sonarr calendar")
        
        # Get all TV show requests with their users
        tv_requests = db.query(MediaRequest).filter(
            MediaRequest.media_type == "tv"
        ).all()
        
        logger.info(f"Found {len(tv_requests)} TV show requests in database")
        
        # Get all series from Sonarr to map seriesId to series details
        all_series = await sonarr._get("/series")
        series_map = {}  # seriesId -> series details
        for series in all_series:
            series_id = series.get("id")
            if series_id:
                series_map[series_id] = series
        
        logger.info(f"Loaded {len(series_map)} series from Sonarr")
        
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
            # Get series details from the series map
            series_id = episode.get("seriesId")
            if not series_id or series_id not in series_map:
                logger.debug(f"Episode {episode.get('title')} has no series in map")
                continue
            
            series = series_map[series_id]
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
        from app.services.sonarr_service import SonarrService
        from app.services.jellyseerr_sync import JellyseerrSyncService
        
        sonarr = SonarrService()
        sync_service = JellyseerrSyncService()
        
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
        from app.services.sonarr_service import SonarrService
        from app.services.jellyseerr_sync import JellyseerrSyncService
        
        sonarr = SonarrService()
        sync_service = JellyseerrSyncService()
        
        # Get all TV show requests
        tv_requests = db.query(MediaRequest).filter(MediaRequest.media_type == "tv").all()
        
        imported_count = 0
        for request in tv_requests:
            try:
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
        from app.services.sonarr_service import SonarrService
        from app.database import EpisodeTracking
        
        # Get the request
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Get series details from Sonarr
        sonarr = SonarrService()
        series = await sonarr.get_series(series_id)
        if not series:
            raise HTTPException(status_code=404, detail="Series not found in Sonarr")
        
        # Get episode details
        all_episodes = await sonarr.get_episodes_by_series(series_id)
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
            notification.sent_at = datetime.utcnow()
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
            notification.sent = True
            notification.sent_at = datetime.utcnow()
            notification.error_message = None
            if regenerate:
                notification.body = body  # Update stored body with new poster
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
async def create_backup(include_config: bool = False):
    """Create a backup of database and configuration"""
    try:
        from app.services.backup_service import BackupService
        
        backup_service = BackupService()
        backup_file = backup_service.create_backup(include_config=include_config)
        
        if backup_file:
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
                if 'database.sql' not in names:
                    os.remove(temp_path)
                    raise HTTPException(status_code=400, detail="Invalid backup: missing database.sql")
                for name in names:
                    if name.startswith('/') or '..' in name:
                        os.remove(temp_path)
                        logger.warning(f"Zip-slip attempt detected: {name}")
                        raise HTTPException(status_code=400, detail="Invalid backup: suspicious file paths")
                allowed_extensions = {'.json', '.sql', '.txt'}
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


@router.get("/config")
async def get_config():
    """Get current configuration (sanitized - no passwords/API keys shown in full)"""
    import os
    
    def mask_secret(value: str) -> str:
        """SECURITY FIX [CRIT-2]: Never reveal any part of secrets"""
        if not value or value.strip() == "":
            return ""
        return "••••••••"
    
    try:
        config = {
            "timing": {
                "initial_delay_minutes": int(os.getenv("NOTIFICATION_INITIAL_DELAY_MIN", "7")),
                "extension_delay_minutes": int(os.getenv("NOTIFICATION_EXTENSION_DELAY_MIN", "3")),
                "max_wait_minutes": int(os.getenv("NOTIFICATION_MAX_WAIT_MIN", "15")),
                "check_frequency_seconds": int(os.getenv("NOTIFICATION_CHECK_FREQUENCY_SEC", "60"))
            },
            "smtp": {
                "host": os.getenv("SMTP_HOST", ""),
                "port": os.getenv("SMTP_PORT", "587"),
                "from": os.getenv("SMTP_FROM", ""),
                "user": os.getenv("SMTP_USER", ""),
                "password": mask_secret(os.getenv("SMTP_PASSWORD", ""))
            },
            "jellyseerr": {
                "url": os.getenv("JELLYSEERR_URL", ""),
                "api_key": mask_secret(os.getenv("JELLYSEERR_API_KEY", ""))
            },
            "sonarr": {
                "url": os.getenv("SONARR_URL", ""),
                "api_key": mask_secret(os.getenv("SONARR_API_KEY", ""))
            },
            "radarr": {
                "url": os.getenv("RADARR_URL", ""),
                "api_key": mask_secret(os.getenv("RADARR_API_KEY", ""))
            },
            "plex": {
                "url": os.getenv("PLEX_URL", ""),
                "token": mask_secret(os.getenv("PLEX_TOKEN", ""))
            },
            "quality_monitor": {
                "enabled": os.getenv("QUALITY_MONITOR_ENABLED", "true").lower() == "true",
                "interval_hours": int(os.getenv("QUALITY_MONITOR_INTERVAL_HOURS", "24")),
                "waiting_delay_seconds": int(os.getenv("QUALITY_WAITING_DELAY_SECONDS", "300"))
            },
            "issue_autofix": {
                "mode": os.getenv("ISSUE_AUTOFIX_MODE", "manual")
            },
            "admin_email": os.getenv("ADMIN_EMAIL", ""),
            "security": {
                "webhook_allowed_ips": os.getenv("WEBHOOK_ALLOWED_IPS", ""),
                "environment": os.getenv("ENVIRONMENT", "production"),
                "secret_key_status": "strong" if os.getenv("APP_SECRET_KEY", "") not in ("", "default-secret", "change-me", "CHANGE_ME_random_string_here", "CHANGE_ME_TO_A_RANDOM_STRING") and len(os.getenv("APP_SECRET_KEY", "")) >= 32 else "weak"
            }
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
                    "turnstile_secret_key": mask_secret(auth_settings.get("turnstile_secret_key", "")) if auth_settings.get("turnstile_secret_key") else ""
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
                "issue_fixing_cutoff_hours": 1,
                "issue_reported_cutoff_hours": 24,
                "issue_abandon_days": 7,
            }
        
        return config
    except Exception as e:
        logger.error(f"Failed to get config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/config")
async def update_config(config: dict):
    """Update configuration in .env file"""
    import os
    from pathlib import Path
    
    try:
        # Try multiple possible .env locations
        possible_paths = [
            Path("/app/.env"),
            Path("/data/.env"),
            Path(".env"),
            Path(os.getcwd()) / ".env"
        ]
        
        env_path = None
        for path in possible_paths:
            if path.exists():
                env_path = path
                logger.info(f"Found .env at: {env_path}")
                break
        
        if not env_path:
            logger.error(f".env not found in any of: {[str(p) for p in possible_paths]}")
            raise HTTPException(
                status_code=500, 
                detail=".env file not found. Configuration cannot be saved."
            )
        
        # Read existing .env
        env_lines = []
        with open(env_path, 'r') as f:
            env_lines = f.readlines()
        
        # Build new env dict
        env_dict = {}
        for line in env_lines:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_dict[key] = value
        
        # Update with new values (only if not masked)
        updates = []
        
        def is_masked_value(value: str) -> bool:
            """Check if a value is a masked/redacted secret that should NOT be saved.
            Catches all variants of bullet masking regardless of encoding."""
            if not value:
                return False
            # Check for actual bullet character (U+2022)
            if '\u2022' in value:
                return True
            # Check for common mojibake patterns of U+2022
            # UTF-8 bytes of • are 0xE2 0x80 0xA2
            # When read as Latin-1: â€¢  When read as Windows-1252: â€¢
            if '\xe2\x80\xa2' in value.encode('latin-1', errors='ignore').decode('latin-1', errors='ignore'):
                return True
            # Catch any non-ASCII in what should be ASCII-only API keys/passwords
            # If the value has non-ASCII chars mixed with ASCII, it's likely masked
            import re
            non_ascii_count = len(re.findall(r'[^\x00-\x7F]', value))
            if non_ascii_count >= 3:
                return True
            # Check for placeholder patterns
            if value.strip() in ('********',):
                return True
            return False
        
        # Notification Timing
        if 'timing' in config:
            if config['timing'].get('initial_delay_minutes'):
                env_dict['NOTIFICATION_INITIAL_DELAY_MIN'] = str(config['timing']['initial_delay_minutes'])
                updates.append('NOTIFICATION_INITIAL_DELAY_MIN')
            if config['timing'].get('extension_delay_minutes'):
                env_dict['NOTIFICATION_EXTENSION_DELAY_MIN'] = str(config['timing']['extension_delay_minutes'])
                updates.append('NOTIFICATION_EXTENSION_DELAY_MIN')
            if config['timing'].get('max_wait_minutes'):
                env_dict['NOTIFICATION_MAX_WAIT_MIN'] = str(config['timing']['max_wait_minutes'])
                updates.append('NOTIFICATION_MAX_WAIT_MIN')
            if config['timing'].get('check_frequency_seconds'):
                env_dict['NOTIFICATION_CHECK_FREQUENCY_SEC'] = str(config['timing']['check_frequency_seconds'])
                updates.append('NOTIFICATION_CHECK_FREQUENCY_SEC')
        
        # SMTP
        if 'smtp' in config:
            if config['smtp'].get('host'):
                env_dict['SMTP_HOST'] = config['smtp']['host']
                updates.append('SMTP_HOST')
            if config['smtp'].get('port'):
                env_dict['SMTP_PORT'] = str(config['smtp']['port'])
                updates.append('SMTP_PORT')
            if config['smtp'].get('from'):
                env_dict['SMTP_FROM'] = config['smtp']['from']
                updates.append('SMTP_FROM')
            if config['smtp'].get('user'):
                env_dict['SMTP_USER'] = config['smtp']['user']
                updates.append('SMTP_USER')
            if config['smtp'].get('password') and not is_masked_value(config['smtp']['password']):
                env_dict['SMTP_PASSWORD'] = config['smtp']['password']
                updates.append('SMTP_PASSWORD')
        
        # Admin email
        if config.get('admin_email'):
            env_dict['ADMIN_EMAIL'] = config['admin_email']
            updates.append('ADMIN_EMAIL')
        
        # Jellyseerr
        if 'jellyseerr' in config:
            if config['jellyseerr'].get('url'):
                env_dict['JELLYSEERR_URL'] = config['jellyseerr']['url']
                updates.append('JELLYSEERR_URL')
            if config['jellyseerr'].get('api_key') and not is_masked_value(config['jellyseerr']['api_key']):
                env_dict['JELLYSEERR_API_KEY'] = config['jellyseerr']['api_key']
                updates.append('JELLYSEERR_API_KEY')
        
        # Sonarr
        if 'sonarr' in config:
            if config['sonarr'].get('url'):
                env_dict['SONARR_URL'] = config['sonarr']['url']
                updates.append('SONARR_URL')
            if config['sonarr'].get('api_key') and not is_masked_value(config['sonarr']['api_key']):
                env_dict['SONARR_API_KEY'] = config['sonarr']['api_key']
                updates.append('SONARR_API_KEY')
        
        # Radarr
        if 'radarr' in config:
            if config['radarr'].get('url'):
                env_dict['RADARR_URL'] = config['radarr']['url']
                updates.append('RADARR_URL')
            if config['radarr'].get('api_key') and not is_masked_value(config['radarr']['api_key']):
                env_dict['RADARR_API_KEY'] = config['radarr']['api_key']
                updates.append('RADARR_API_KEY')
        
        # Plex
        if 'plex' in config:
            if config['plex'].get('url'):
                env_dict['PLEX_URL'] = config['plex']['url']
                updates.append('PLEX_URL')
            if config['plex'].get('token') and not is_masked_value(config['plex']['token']):
                env_dict['PLEX_TOKEN'] = config['plex']['token']
                updates.append('PLEX_TOKEN')
        
        # Quality Monitor
        if 'quality_monitor' in config:
            env_dict['QUALITY_MONITOR_ENABLED'] = str(config['quality_monitor'].get('enabled', True)).lower()
            updates.append('QUALITY_MONITOR_ENABLED')
            if config['quality_monitor'].get('interval_hours'):
                env_dict['QUALITY_MONITOR_INTERVAL_HOURS'] = str(config['quality_monitor']['interval_hours'])
                updates.append('QUALITY_MONITOR_INTERVAL_HOURS')
            if config['quality_monitor'].get('waiting_delay_seconds'):
                env_dict['QUALITY_WAITING_DELAY_SECONDS'] = str(config['quality_monitor']['waiting_delay_seconds'])
                updates.append('QUALITY_WAITING_DELAY_SECONDS')
        
        # Issue Auto-fix
        if 'issue_autofix' in config:
            mode = config['issue_autofix'].get('mode', 'manual')
            if mode in ('manual', 'auto', 'auto_notify'):
                env_dict['ISSUE_AUTOFIX_MODE'] = mode
                updates.append('ISSUE_AUTOFIX_MODE')
        
        # Security settings (stored in .env)
        if 'security' in config:
            sec = config['security']
            if 'webhook_allowed_ips' in sec:
                env_dict['WEBHOOK_ALLOWED_IPS'] = sec['webhook_allowed_ips']
                updates.append('WEBHOOK_ALLOWED_IPS')
            if sec.get('environment') in ('production', 'development'):
                env_dict['ENVIRONMENT'] = sec['environment']
                updates.append('ENVIRONMENT')
            if sec.get('app_secret_key') and not is_masked_value(sec['app_secret_key']):
                env_dict['APP_SECRET_KEY'] = sec['app_secret_key']
                updates.append('APP_SECRET_KEY')
        
        # Auth settings (stored in database, not .env)
        if 'auth' in config:
            try:
                from app.auth import set_auth_setting, hash_password, get_auth_settings
                from app.database import get_db
                db = next(get_db())
                try:
                    auth = config['auth']
                    
                    if 'enabled' in auth:
                        set_auth_setting(db, 'auth_enabled', str(auth['enabled']).lower())
                        updates.append('AUTH_ENABLED')
                    
                    # Only set password if a new one is provided (not empty, not masked)
                    new_password = auth.get('password', '')
                    if new_password and not is_masked_value(new_password):
                        set_auth_setting(db, 'auth_password_hash', hash_password(new_password))
                        updates.append('AUTH_PASSWORD')
                    
                    if 'local_network_cidr' in auth:
                        set_auth_setting(db, 'local_network_cidr', auth['local_network_cidr'])
                        updates.append('LOCAL_NETWORK_CIDR')
                    
                    if 'session_timeout_hours' in auth:
                        set_auth_setting(db, 'session_timeout_hours', str(auth['session_timeout_hours']))
                        updates.append('SESSION_TIMEOUT_HOURS')
                    
                    if 'turnstile_enabled' in auth:
                        set_auth_setting(db, 'turnstile_enabled', str(auth['turnstile_enabled']).lower())
                        updates.append('TURNSTILE_ENABLED')
                    
                    if auth.get('turnstile_site_key') is not None:
                        set_auth_setting(db, 'turnstile_site_key', auth['turnstile_site_key'])
                        updates.append('TURNSTILE_SITE_KEY')
                    
                    turnstile_secret = auth.get('turnstile_secret_key', '')
                    if turnstile_secret and not is_masked_value(turnstile_secret):
                        set_auth_setting(db, 'turnstile_secret_key', turnstile_secret)
                        updates.append('TURNSTILE_SECRET_KEY')
                finally:
                    db.close()
            except Exception as e:
                logger.error(f"Failed to save auth settings: {e}")
        
        # Reconciliation settings (stored in database)
        if 'reconciliation' in config:
            try:
                from app.database import get_db, SystemConfig
                db = next(get_db())
                try:
                    recon = config['reconciliation']
                    recon_fields = {
                        'reconciliation_interval_hours': ('interval_hours', 2),
                        'reconciliation_issue_fixing_cutoff_hours': ('issue_fixing_cutoff_hours', 1),
                        'reconciliation_issue_reported_cutoff_hours': ('issue_reported_cutoff_hours', 24),
                        'reconciliation_issue_abandon_days': ('issue_abandon_days', 7),
                    }
                    for db_key, (json_key, default) in recon_fields.items():
                        if json_key in recon:
                            val = str(int(recon[json_key]))
                            existing = db.query(SystemConfig).filter(SystemConfig.key == db_key).first()
                            if existing:
                                existing.value = val
                                existing.updated_at = datetime.utcnow()
                            else:
                                db.add(SystemConfig(key=db_key, value=val))
                            updates.append(db_key.upper())
                    db.commit()
                finally:
                    db.close()
            except Exception as e:
                logger.error(f"Failed to save reconciliation settings: {e}")
        
        # Write back to .env - preserve comments and structure
        new_lines = []
        keys_written = set()
        for line in env_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key = stripped.split('=', 1)[0]
                if key in env_dict:
                    new_lines.append(f"{key}={env_dict[key]}\n")
                    keys_written.add(key)
                else:
                    new_lines.append(line if line.endswith('\n') else line + '\n')
            else:
                new_lines.append(line if line.endswith('\n') else line + '\n')
        
        # Add any new keys not in original file
        for key, value in env_dict.items():
            if key not in keys_written:
                new_lines.append(f"{key}={value}\n")
        
        with open(env_path, 'w') as f:
            f.writelines(new_lines)
        
        logger.info(f"Configuration updated: {', '.join(updates)}")
        
        return {
            "success": True,
            "message": f"Updated {len(updates)} settings. Restart required for changes to take effect.",
            "updated_fields": updates
        }
        
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


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
            return {"success": True, "message": "Container restart initiated"}
        else:
            raise HTTPException(status_code=500, detail=f"Restart failed: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        # Timeout is actually good - means restart started
        logger.info("Container restart command sent (timeout expected)")
        return {"success": True, "message": "Container restart initiated"}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Docker CLI not available in container")
    except Exception as e:
        logger.error(f"Failed to restart container: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/reconcile")
async def trigger_reconciliation():
    """Manually trigger reconciliation check"""
    try:
        from app.background.reconciliation import run_reconciliation
        import asyncio
        
        # Run reconciliation in background
        asyncio.create_task(run_reconciliation())
        
        return {
            "success": True,
            "message": "Reconciliation started - check logs for results"
        }
    except Exception as e:
        logger.error(f"Failed to start reconciliation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logs")
async def get_logs(lines: int = 100):
    """Get recent application logs"""
    try:
        import subprocess
        
        # Get logs from Docker container
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), os.environ.get("HOSTNAME", "self")],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Combine stdout and stderr
        logs = result.stdout + result.stderr
        
        return {
            "success": True,
            "logs": logs,
            "lines": len(logs.split('\n'))
        }
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timeout reading logs")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Docker CLI not available")
    except Exception as e:
        logger.error(f"Failed to read logs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logs/stream")
async def stream_logs():
    """Stream logs in real-time (SSE)"""
    try:
        import subprocess
        from fastapi.responses import StreamingResponse
        
        async def log_generator():
            process = subprocess.Popen(
                ["docker", "logs", "-f", "--tail", "50", os.environ.get("HOSTNAME", "self")],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            try:
                for line in process.stdout:
                    yield f"data: {line}\n\n"
            finally:
                process.terminate()
                process.wait()
        
        return StreamingResponse(
            log_generator(),
            media_type="text/event-stream"
        )
        
    except Exception as e:
        logger.error(f"Failed to stream logs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


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


@router.post("/send-weekly-summary")
async def send_weekly_summary_now():
    """Manually trigger weekly summary email"""
    try:
        from app.background.weekly_summary import send_weekly_summary
        import asyncio
        
        # Run summary in background
        asyncio.create_task(send_weekly_summary())
        
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
            from app.services.sonarr_service import SonarrService
            sonarr_svc = SonarrService()
            result = await sonarr_svc.blacklist_and_research_series(issue.tmdb_id)
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


@router.post("/restart")
async def restart_container():
    """Restart the Docker container"""
    try:
        import subprocess
        import os
        
        # Get container ID/name from hostname or environment
        container_id = os.environ.get("HOSTNAME", "self")
        
        logger.info(f"Attempting to restart container: {container_id}")
        
        # Check if docker socket is available
        if not os.path.exists("/var/run/docker.sock"):
            logger.error("Docker socket not mounted")
            raise HTTPException(
                status_code=500, 
                detail="Docker socket not available. Mount /var/run/docker.sock to enable restart. See DOCKER_RESTART_SETUP.md"
            )
        
        # Use docker restart command
        result = subprocess.run(
            ["docker", "restart", container_id],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.info("Container restart command sent successfully")
            return {"success": True, "message": "Container restarting..."}
        else:
            error = result.stderr or "Unknown error"
            logger.error(f"Failed to restart container: {error}")
            raise HTTPException(status_code=500, detail=f"Restart failed: {error}")
            
    except subprocess.TimeoutExpired:
        # Timeout is expected as container restarts
        logger.info("Restart command timed out (expected - container is restarting)")
        return {"success": True, "message": "Container restarting..."}
    except FileNotFoundError:
        logger.error("Docker CLI not available in container")
        raise HTTPException(
            status_code=500, 
            detail="Docker CLI not found. Restart container manually: docker restart <container-name>"
        )
    except Exception as e:
        logger.error(f"Failed to restart container: {e}")
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


@router.post("/requests/{request_id}/share")
async def add_user_to_request(
    request_id: int,
    data: dict,
    db: Session = Depends(get_db)
):
    """Add a user to an existing request's notifications"""
    try:
        user_id = data.get('user_id')
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        
        # Check if request exists
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Check if user exists
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if already shared
        existing = db.query(SharedRequest).filter(
            SharedRequest.request_id == request_id,
            SharedRequest.user_id == user_id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="User already added to this request")
        
        # Create shared request
        from datetime import datetime
        shared = SharedRequest(
            request_id=request_id,
            user_id=user_id,
            shared_at=datetime.utcnow()
        )
        db.add(shared)
        db.commit()
        
        logger.info(f"Added user {user.email} to request {request_id} ({request.title})")
        
        return {
            "success": True,
            "message": f"User {user.email} added to notifications for {request.title}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add user to request: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


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
        
        jellyseerr_url = settings.jellyseerr_url.rstrip('/')
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
            
            # Create request
            request_payload = {
                "mediaType": media_type,
                "mediaId": tmdb_id,
                "userId": jellyseerr_id  # Use the jellyseerr_id
            }
            
            if media_type == 'tv':
                request_payload["seasons"] = "all"
            
            request_response = await client.post(
                f"{jellyseerr_url}/api/v1/request",
                headers={"X-Api-Key": api_key},
                json=request_payload
            )
            request_response.raise_for_status()
            request_data = request_response.json()
        
        logger.info(f"Created request for {media_data.get('title') or media_data.get('name')} on behalf of {user.email}")
        
        return {
            "success": True,
            "message": f"Request created successfully",
            "jellyseerr_request_id": request_data.get('id')
        }
        
    except httpx.HTTPError as e:
        logger.error(f"Jellyseerr API error: {e}")
        raise HTTPException(status_code=500, detail=f"Jellyseerr error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create request on behalf: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-email")
async def test_email_connection(data: dict):
    """Test SMTP email connection"""
    try:
        from app.services.email_service import EmailService
        import smtplib
        from email.mime.text import MIMEText
        
        host = data.get('host')
        port = data.get('port', 587)
        user = data.get('user')
        password = data.get('password')
        from_addr = data.get('from')
        
        # Test connection
        server = smtplib.SMTP(host, port)
        server.starttls()
        server.login(user, password)
        server.quit()
        
        return {"success": True, "message": "SMTP connection successful!"}
        
    except Exception as e:
        logger.error(f"SMTP test failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-jellyseerr")
async def test_jellyseerr_connection(data: dict):
    """Test Jellyseerr API connection"""
    try:
        import httpx
        
        url = data.get('url', '').rstrip('/')
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
        
        url = data.get('url', '').rstrip('/')
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
        
        url = data.get('url', '').rstrip('/')
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


@router.post("/setup-complete")
async def mark_setup_complete(db: Session = Depends(get_db)):
    """Mark initial setup as complete"""
    try:
        # Check if already exists
        config = db.query(SystemConfig).filter(SystemConfig.key == "setup_complete").first()
        
        if config:
            config.value = "true"
            config.updated_at = datetime.utcnow()
        else:
            config = SystemConfig(key="setup_complete", value="true")
            db.add(config)
        
        db.commit()
        
        logger.info("Setup marked as complete in database")
        return {"success": True, "message": "Setup marked as complete"}
        
    except Exception as e:
        logger.error(f"Failed to mark setup complete: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/setup-status")
async def get_setup_status(db: Session = Depends(get_db)):
    """Check if initial setup has been completed"""
    try:
        config = db.query(SystemConfig).filter(SystemConfig.key == "setup_complete").first()
        setup_complete = config and config.value == "true"
        
        return {
            "setup_complete": setup_complete,
            "needs_setup": not setup_complete
        }
        
    except Exception as e:
        logger.error(f"Failed to check setup status: {e}")
        # If there's an error, assume setup is not complete to be safe
        return {"setup_complete": False, "needs_setup": True}


@router.post("/skip-setup")
async def skip_setup(db: Session = Depends(get_db)):
    """Skip setup wizard for already configured instances"""
    try:
        # Check if already exists
        config = db.query(SystemConfig).filter(SystemConfig.key == "setup_complete").first()
        
        if config:
            config.value = "true"
            config.updated_at = datetime.utcnow()
        else:
            config = SystemConfig(key="setup_complete", value="true")
            db.add(config)
        
        db.commit()
        
        logger.info("Setup skipped - marked as complete in database")
        return {"success": True, "message": "Setup skipped - marked as complete"}
        
    except Exception as e:
        logger.error(f"Failed to skip setup: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")

