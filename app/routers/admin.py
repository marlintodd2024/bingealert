from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging

from app.database import get_db, User, MediaRequest, EpisodeTracking, Notification
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
                    
                    # Include ALL upcoming episodes, mark their notification status
                    upcoming.append({
                        "series_title": series.get("title"),
                        "season_number": episode.get("seasonNumber"),
                        "episode_number": episode.get("episodeNumber"),
                        "episode_title": episode.get("title"),
                        "air_date": episode.get("airDateUtc"),
                        "has_file": episode.get("hasFile", False),
                        "monitored": episode.get("monitored", True),
                        "user_email": request.user.email,
                        "user_name": request.user.username,
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
        
        email_service = EmailService()
        
        # Generate test email based on type
        if notification_type == "episode":
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
                ]
            )
            subject = "Test: New Episodes Available - Breaking Bad"
        elif notification_type == "movie":
            html_body = email_service.render_movie_notification(
                movie_title="The Shawshank Redemption",
                year=1994
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
