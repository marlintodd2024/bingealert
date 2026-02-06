from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session
import logging
from datetime import datetime
from typing import List

from app.database import get_db, MediaRequest, EpisodeTracking, Notification, User
from app.schemas import SonarrWebhook, RadarrWebhook, WebhookResponse
from app.services.email_service import EmailService
from app.services.sonarr_service import SonarrService

logger = logging.getLogger(__name__)
router = APIRouter()
email_service = EmailService()


@router.post("/sonarr", response_model=WebhookResponse)
async def sonarr_webhook(
    webhook: SonarrWebhook,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Handle webhooks from Sonarr
    Supported events: Download, Test
    """
    logger.info(f"Received Sonarr webhook: {webhook.eventType}")
    
    if webhook.eventType == "Test":
        return WebhookResponse(success=True, message="Sonarr webhook test successful")
    
    if webhook.eventType != "Download":
        return WebhookResponse(success=False, message=f"Unsupported event type: {webhook.eventType}")
    
    try:
        # Get series TMDB ID
        tmdb_id = webhook.series.tmdbId
        if not tmdb_id:
            logger.warning(f"Series {webhook.series.title} has no TMDB ID")
            return WebhookResponse(success=False, message="Series has no TMDB ID")
        
        # Find all requests for this series
        requests = db.query(MediaRequest).filter(
            MediaRequest.media_type == "tv",
            MediaRequest.tmdb_id == tmdb_id
        ).all()
        
        if not requests:
            logger.info(f"No requests found for series TMDB ID {tmdb_id}")
            return WebhookResponse(success=True, message="No matching requests found")
        
        logger.info(f"Found {len(requests)} request(s) for series: {webhook.series.title}")
        
        # Process episodes - batch by user
        # Structure: {user_id: {request_id: [episodes]}}
        user_episode_batches = {}
        
        for episode in webhook.episodes or []:
            for request in requests:
                # Get all users for this request (original + shared)
                users_to_notify = [request.user]
                
                # Add shared users
                from app.database import SharedRequest
                shared_requests = db.query(SharedRequest).filter(
                    SharedRequest.request_id == request.id
                ).all()
                for shared in shared_requests:
                    users_to_notify.append(shared.user)
                
                for user in users_to_notify:
                    # Track this episode
                    episode_tracking = db.query(EpisodeTracking).filter(
                        EpisodeTracking.request_id == request.id,
                        EpisodeTracking.series_id == webhook.series.id,
                        EpisodeTracking.season_number == episode.seasonNumber,
                        EpisodeTracking.episode_number == episode.episodeNumber
                    ).first()
                    
                    if not episode_tracking:
                        # Create new episode tracking (only once per request, not per user)
                        episode_tracking = EpisodeTracking(
                            request_id=request.id,
                            series_id=webhook.series.id,
                            season_number=episode.seasonNumber,
                            episode_number=episode.episodeNumber,
                            episode_title=episode.title,
                            air_date=datetime.fromisoformat(episode.airDateUtc.replace('Z', '+00:00')) if episode.airDateUtc else None,
                            notified=False,
                            available_in_plex=True
                        )
                        db.add(episode_tracking)
                    else:
                        # Update existing tracking
                        episode_tracking.available_in_plex = True
                        episode_tracking.episode_title = episode.title
                    
                    # Check if already notified for this specific user
                    existing_notification = db.query(Notification).filter(
                        Notification.user_id == user.id,
                        Notification.request_id == request.id,
                        Notification.notification_type == "episode",
                        Notification.subject.contains(f"S{episode.seasonNumber:02d}E{episode.episodeNumber:02d}")
                    ).first()
                    
                    # Only add to batch if not already notified
                    if not existing_notification:
                        # Initialize user batch if needed
                        if user.id not in user_episode_batches:
                            user_episode_batches[user.id] = {
                                'user': user,
                                'request_id': request.id,
                                'tmdb_id': request.tmdb_id,
                                'episodes': []
                            }
                        
                        # Add episode to user's batch
                        user_episode_batches[user.id]['episodes'].append({
                            'season': episode.seasonNumber,
                            'episode': episode.episodeNumber,
                            'title': episode.title,
                            'air_date': episode.airDate,
                            'tracking': episode_tracking
                        })
        
        # Now create one notification per user with all their episodes batched
        notifications_created = 0
        for user_id, batch in user_episode_batches.items():
            if not batch['episodes']:
                continue
            
            # Get poster URL
            from app.services.tmdb_service import TMDBService
            from app.config import settings
            tmdb_service = TMDBService(settings.jellyseerr_url, settings.jellyseerr_api_key)
            poster_url = await tmdb_service.get_tv_poster(batch['tmdb_id'])
            
            # Render email with all episodes
            html_body = email_service.render_episode_notification(
                series_title=webhook.series.title,
                episodes=batch['episodes'],
                poster_url=poster_url
            )
            
            # Create subject based on episode count
            if len(batch['episodes']) == 1:
                ep = batch['episodes'][0]
                subject = f"New Episode: {webhook.series.title} S{ep['season']:02d}E{ep['episode']:02d}"
            else:
                subject = f"New Episodes: {webhook.series.title} ({len(batch['episodes'])} episodes)"
            
            # Set send_after to 7 minutes from now (420 seconds)
            # This gives time for: 2 min batch window + 5 min Plex indexing
            from datetime import timedelta
            send_after = datetime.utcnow() + timedelta(seconds=420)
            
            notification = Notification(
                user_id=batch['user'].id,
                request_id=batch['request_id'],
                notification_type="episode",
                subject=subject,
                body=html_body,
                send_after=send_after,
                series_id=webhook.series.id  # Store series ID for smart batching
            )
            db.add(notification)
            notifications_created += 1
            
            # Mark all episodes as notified
            for ep in batch['episodes']:
                ep['tracking'].notified = True
            
            logger.info(f"Created batched notification for {batch['user'].email}: {len(batch['episodes'])} episode(s), will send after {send_after}")
        
        db.commit()
        
        # Send pending notifications in background
        background_tasks.add_task(email_service.process_pending_notifications, db)
        
        return WebhookResponse(
            success=True,
            message=f"Processed {len(webhook.episodes or [])} episodes",
            processed_items=notifications_created
        )
        
    except Exception as e:
        logger.error(f"Error processing Sonarr webhook: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/radarr", response_model=WebhookResponse)
async def radarr_webhook(
    webhook: RadarrWebhook,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Handle webhooks from Radarr
    Supported events: Download, Test
    """
    logger.info(f"Received Radarr webhook: {webhook.eventType}")
    
    if webhook.eventType == "Test":
        return WebhookResponse(success=True, message="Radarr webhook test successful")
    
    if webhook.eventType != "Download":
        return WebhookResponse(success=False, message=f"Unsupported event type: {webhook.eventType}")
    
    try:
        tmdb_id = webhook.movie.tmdbId
        
        # Find all requests for this movie
        requests = db.query(MediaRequest).filter(
            MediaRequest.media_type == "movie",
            MediaRequest.tmdb_id == tmdb_id
        ).all()
        
        if not requests:
            logger.info(f"No requests found for movie TMDB ID {tmdb_id}")
            return WebhookResponse(success=True, message="No matching requests found")
        
        notifications_created = 0
        for request in requests:
            # Get all users for this request (original + shared)
            users_to_notify = [request.user]
            
            # Add shared users
            from app.database import SharedRequest
            shared_requests = db.query(SharedRequest).filter(
                SharedRequest.request_id == request.id
            ).all()
            for shared in shared_requests:
                users_to_notify.append(shared.user)
            
            logger.info(f"Notifying {len(users_to_notify)} user(s) for movie: {webhook.movie.title}")
            
            for user in users_to_notify:
                # Check if already notified
                existing_notification = db.query(Notification).filter(
                    Notification.user_id == user.id,
                    Notification.request_id == request.id,
                    Notification.notification_type == "movie"
                ).first()
                
                if not existing_notification:
                    # Get poster URL
                    from app.services.tmdb_service import TMDBService
                    from app.config import settings
                    tmdb_service = TMDBService(settings.jellyseerr_url, settings.jellyseerr_api_key)
                    poster_url = await tmdb_service.get_movie_poster(tmdb_id)
                    
                    # Render email
                    html_body = email_service.render_movie_notification(
                        movie_title=webhook.movie.title,
                        poster_url=poster_url
                    )
                    
                    # Set send_after to 5 minutes from now (300 seconds) to allow Plex to index
                    from datetime import timedelta
                    send_after = datetime.utcnow() + timedelta(seconds=300)
                    
                    notification = Notification(
                        user_id=user.id,
                        request_id=request.id,
                        notification_type="movie",
                        subject=f"Movie Available: {webhook.movie.title}",
                        body=html_body,
                        send_after=send_after
                    )
                    db.add(notification)
                    notifications_created += 1
                    logger.info(f"Created movie notification for {user.email}, will send after {send_after}")
            
            # Update request status (once per request, not per user)
            request.status = "available"
        
        db.commit()
        
        # Send pending notifications in background
        background_tasks.add_task(email_service.process_pending_notifications, db)
        
        return WebhookResponse(
            success=True,
            message=f"Processed movie: {webhook.movie.title}",
            processed_items=notifications_created
        )
        
    except Exception as e:
        logger.error(f"Error processing Radarr webhook: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/jellyseerr", response_model=WebhookResponse)
async def jellyseerr_webhook(
    webhook: dict,  # Using dict because Jellyseerr webhook format varies
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Handle webhooks from Jellyseerr for request events
    Supports: MEDIA_PENDING, MEDIA_APPROVED, MEDIA_AUTO_APPROVED, MEDIA_AVAILABLE
    """
    try:
        logger.info(f"Received Jellyseerr webhook: {webhook.get('notification_type')}")
        logger.debug(f"Jellyseerr webhook payload: {webhook}")
        
        notification_type = webhook.get('notification_type', '')
        
        # We only care about new/approved requests, not availability
        # (Sonarr/Radarr will handle availability notifications)
        if notification_type not in ['MEDIA_PENDING', 'MEDIA_APPROVED', 'MEDIA_AUTO_APPROVED']:
            logger.info(f"Ignoring notification type: {notification_type}")
            return WebhookResponse(
                success=True,
                message=f"Ignored event type: {notification_type}"
            )
        
        # Extract data from webhook
        subject = webhook.get('subject', '')
        media = webhook.get('media', {})
        request_data = webhook.get('request', {})
        extra = webhook.get('extra', [])
        
        # Get media details
        media_type = media.get('media_type', '')
        tmdb_id = media.get('tmdbId')
        
        if not tmdb_id:
            logger.error("No TMDB ID in webhook")
            return WebhookResponse(success=False, message="No TMDB ID provided")
        
        # Get user info from request or extra data
        user_email = request_data.get('requestedBy_email')
        user_username = request_data.get('requestedBy_username')
        
        # Try to extract from extra array if not in request
        if not user_email and extra:
            for item in extra:
                if item.get('name') == 'Requested By' and item.get('value'):
                    user_username = item.get('value')
                elif item.get('name') == 'Email' and item.get('value'):
                    user_email = item.get('value')
        
        # Find or create user
        user = None
        if user_email:
            user = db.query(User).filter(User.email == user_email).first()
        
        if not user and user_username:
            user = db.query(User).filter(User.username == user_username).first()
        
        if not user:
            # Try to sync users from Jellyseerr to find this user
            logger.info("User not found, attempting sync...")
            from app.services.jellyseerr_sync import JellyseerrSyncService
            sync_service = JellyseerrSyncService()
            await sync_service.sync_users()
            
            # Try again
            if user_email:
                user = db.query(User).filter(User.email == user_email).first()
            if not user and user_username:
                user = db.query(User).filter(User.username == user_username).first()
        
        if not user:
            logger.error(f"Could not find or create user: {user_email or user_username}")
            return WebhookResponse(
                success=False,
                message="User not found"
            )
        
        # Extract title from subject (format: "New Request for TITLE")
        title = subject.replace('New Request for ', '').replace('New request from ', '').strip()
        
        # Get more details from extra
        for item in extra:
            if item.get('name') == 'Requested Media' and item.get('value'):
                title = item.get('value')
                break
        
        # Check if request already exists
        jellyseerr_request_id = request_data.get('request_id')
        existing_request = None
        
        if jellyseerr_request_id:
            existing_request = db.query(MediaRequest).filter(
                MediaRequest.jellyseerr_request_id == jellyseerr_request_id
            ).first()
        
        if not existing_request:
            # Also check by user + TMDB ID
            existing_request = db.query(MediaRequest).filter(
                MediaRequest.user_id == user.id,
                MediaRequest.tmdb_id == tmdb_id,
                MediaRequest.media_type == media_type
            ).first()
        
        if existing_request:
            # Update existing request status
            if notification_type in ['MEDIA_APPROVED', 'MEDIA_AUTO_APPROVED']:
                existing_request.status = 'approved'
            logger.info(f"Updated existing request {existing_request.id}")
            request_obj = existing_request
        else:
            # Create new request
            status = 'approved' if notification_type in ['MEDIA_APPROVED', 'MEDIA_AUTO_APPROVED'] else 'pending'
            
            request_obj = MediaRequest(
                user_id=user.id,
                jellyseerr_request_id=jellyseerr_request_id or 0,  # Fallback if missing
                media_type=media_type,
                tmdb_id=tmdb_id,
                title=title,
                status=status
            )
            db.add(request_obj)
            logger.info(f"Created new request for {title} ({media_type}) by {user.username}")
        
        db.commit()
        
        return WebhookResponse(
            success=True,
            message=f"Processed request: {title}",
            processed_items=1
        )
        
    except Exception as e:
        logger.error(f"Error processing Jellyseerr webhook: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
