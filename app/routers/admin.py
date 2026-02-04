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
