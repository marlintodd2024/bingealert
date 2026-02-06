from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging
import os

from app.database import get_db, User, MediaRequest, EpisodeTracking, Notification, SharedRequest
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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync/requests")
async def sync_requests():
    """Manually trigger request sync from Jellyseerr"""
    try:
        sync_service = JellyseerrSyncService()
        await sync_service.sync_requests()
        return {"success": True, "message": "Request sync completed"}
    except Exception as e:
        logger.error(f"Request sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notifications/process")
async def process_notifications(db: Session = Depends(get_db)):
    """Manually trigger processing of pending notifications"""
    try:
        email_service = EmailService()
        await email_service.process_pending_notifications(db)
        return {"success": True, "message": "Notifications processed"}
    except Exception as e:
        logger.error(f"Notification processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users")
async def list_users(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all users"""
    users = db.query(User).offset(skip).limit(limit).all()
    return {
        "users": [
            {
                "id": u.id,
                "jellyseerr_id": u.jellyseerr_id,
                "email": u.email,
                "username": u.username,
                "created_at": u.created_at
            }
            for u in users
        ]
    }


@router.get("/requests")
async def list_requests(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all media requests"""
    requests = db.query(MediaRequest).offset(skip).limit(limit).all()
    return {
        "requests": [
            {
                "id": r.id,
                "user_email": r.user.email,
                "media_type": r.media_type,
                "title": r.title,
                "status": r.status,
                "created_at": r.created_at
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
    
    notifications = query.offset(skip).limit(limit).all()
    
    return {
        "notifications": [
            {
                "id": n.id,
                "user_email": n.user.email,
                "type": n.notification_type,
                "subject": n.subject,
                "sent": n.sent,
                "sent_at": n.sent_at,
                "created_at": n.created_at
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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backup/create")
async def create_backup(include_config: bool = True):
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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/backup/download/{filename}")
async def download_backup(filename: str):
    """Download a backup file"""
    try:
        from app.services.backup_service import BackupService
        from fastapi.responses import FileResponse
        
        backup_service = BackupService()
        filepath = os.path.join(backup_service.backup_dir, filename)
        
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Backup file not found")
        
        return FileResponse(
            path=filepath,
            filename=filename,
            media_type="application/zip"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backup/restore")
async def restore_backup(file: UploadFile):
    """Restore from an uploaded backup file"""
    try:
        from app.services.backup_service import BackupService
        import tempfile
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_path = temp_file.name
        
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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_config():
    """Get current configuration (sanitized - no passwords/API keys shown in full)"""
    import os
    
    def mask_secret(value: str) -> str:
        """Mask sensitive values, showing only first/last 4 chars"""
        if not value or len(value) < 8:
            return "••••••••"
        return f"{value[:4]}••••{value[-4:]}"
    
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
            }
        }
        return config
    except Exception as e:
        logger.error(f"Failed to get config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def update_config(config: dict):
    """Update configuration in .env file"""
    import os
    from pathlib import Path
    
    try:
        env_path = Path("/app/.env")
        
        # Read existing .env
        env_lines = []
        if env_path.exists():
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
            if config['smtp'].get('password') and not config['smtp']['password'].startswith('••'):
                env_dict['SMTP_PASSWORD'] = config['smtp']['password']
                updates.append('SMTP_PASSWORD')
        
        # Jellyseerr
        if 'jellyseerr' in config:
            if config['jellyseerr'].get('url'):
                env_dict['JELLYSEERR_URL'] = config['jellyseerr']['url']
                updates.append('JELLYSEERR_URL')
            if config['jellyseerr'].get('api_key') and not config['jellyseerr']['api_key'].startswith('••'):
                env_dict['JELLYSEERR_API_KEY'] = config['jellyseerr']['api_key']
                updates.append('JELLYSEERR_API_KEY')
        
        # Sonarr
        if 'sonarr' in config:
            if config['sonarr'].get('url'):
                env_dict['SONARR_URL'] = config['sonarr']['url']
                updates.append('SONARR_URL')
            if config['sonarr'].get('api_key') and not config['sonarr']['api_key'].startswith('••'):
                env_dict['SONARR_API_KEY'] = config['sonarr']['api_key']
                updates.append('SONARR_API_KEY')
        
        # Radarr
        if 'radarr' in config:
            if config['radarr'].get('url'):
                env_dict['RADARR_URL'] = config['radarr']['url']
                updates.append('RADARR_URL')
            if config['radarr'].get('api_key') and not config['radarr']['api_key'].startswith('••'):
                env_dict['RADARR_API_KEY'] = config['radarr']['api_key']
                updates.append('RADARR_API_KEY')
        
        # Plex
        if 'plex' in config:
            if config['plex'].get('url'):
                env_dict['PLEX_URL'] = config['plex']['url']
                updates.append('PLEX_URL')
            if config['plex'].get('token') and not config['plex']['token'].startswith('••'):
                env_dict['PLEX_TOKEN'] = config['plex']['token']
                updates.append('PLEX_TOKEN')
        
        # Write back to .env
        with open(env_path, 'w') as f:
            for key, value in env_dict.items():
                f.write(f"{key}={value}\n")
        
        logger.info(f"Configuration updated: {', '.join(updates)}")
        
        return {
            "success": True,
            "message": f"Updated {len(updates)} settings. Restart required for changes to take effect.",
            "updated_fields": updates
        }
        
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
