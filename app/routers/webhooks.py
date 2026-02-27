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
    Supported events: Grab, Download, Test
    """
    logger.info(f"Received Sonarr webhook: {webhook.eventType}")
    
    if webhook.eventType == "Test":
        return WebhookResponse(success=True, message="Sonarr webhook test successful")
    
    # Handle Grab event (download started)
    if webhook.eventType == "Grab":
        try:
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
                return WebhookResponse(success=True, message="No matching requests found")
            
            # Cancel any pending quality_waiting notifications since download is starting
            cancelled_count = 0
            for request in requests:
                cancelled = db.query(Notification).filter(
                    Notification.request_id == request.id,
                    Notification.notification_type == "quality_waiting",
                    Notification.sent == False
                ).delete()
                cancelled_count += cancelled
            
            db.commit()
            
            if cancelled_count > 0:
                logger.info(f"Grab event: Cancelled {cancelled_count} pending quality_waiting notification(s) for {webhook.series.title} - download started")
            
            return WebhookResponse(
                success=True,
                message=f"Download started for {webhook.series.title}, cancelled quality waiting notifications",
                processed_items=cancelled_count
            )
        except Exception as e:
            logger.error(f"Error processing Sonarr Grab webhook: {e}")
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))
    
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
                
                # Track episode ONCE per request (not per user)
                episode_tracking = db.query(EpisodeTracking).filter(
                    EpisodeTracking.request_id == request.id,
                    EpisodeTracking.series_id == webhook.series.id,
                    EpisodeTracking.season_number == episode.seasonNumber,
                    EpisodeTracking.episode_number == episode.episodeNumber
                ).first()
                
                if not episode_tracking:
                    # Create new episode tracking
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
                
                # Now notify all users
                for user in users_to_notify:
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
        # First, check if the downloaded episodes meet quality cutoff
        quality_cutoff_met = True
        if webhook.episodeFile:
            quality_cutoff_met = not webhook.episodeFile.get('qualityCutoffNotMet', False)
            logger.info(f"Episodes downloaded - Quality cutoff met: {quality_cutoff_met}")
        
        # Only cancel quality_waiting notifications if quality is correct
        if quality_cutoff_met:
            cancelled_count = 0
            for request in requests:
                cancelled = db.query(Notification).filter(
                    Notification.request_id == request.id,
                    Notification.notification_type == "quality_waiting",
                    Notification.sent == False
                ).delete()
                cancelled_count += cancelled
            
            if cancelled_count > 0:
                logger.info(f"Cancelled {cancelled_count} pending quality_waiting notification(s) - correct quality downloaded")
        else:
            logger.info(f"Quality cutoff not met - skipping 'Episodes Available' notifications, keeping quality_waiting active")
            # Don't process episode notifications - quality isn't right yet
            return WebhookResponse(
                success=True,
                message=f"Episodes downloaded but quality cutoff not met - waiting for correct quality",
                processed_items=0
            )
        
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
            
            # NOTE: Don't mark episode_tracking.notified = True here!
            # EpisodeTracking is shared across all users for the same episode.
            # We rely on the Notification table to track who's been notified.
            
            logger.info(f"Created batched notification for {batch['user'].email}: {len(batch['episodes'])} episode(s), will send after {send_after}")
        
        try:
            db.commit()
        except Exception as e:
            # Handle duplicate episode tracking (race condition from multiple webhooks)
            if "duplicate key value violates unique constraint" in str(e):
                logger.warning(f"Duplicate episode detected (likely from multiple webhooks), rolling back: {e}")
                db.rollback()
                # Continue without error - the episode was already tracked
            else:
                # Re-raise other errors
                db.rollback()
                raise
        
        # Check if this download resolves any reported issues
        background_tasks.add_task(_check_issue_resolution, webhook.series.tmdbId, "tv")
        
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
    Supported events: Grab, Download, Test
    """
    logger.info(f"Received Radarr webhook: {webhook.eventType}")
    
    if webhook.eventType == "Test":
        return WebhookResponse(success=True, message="Radarr webhook test successful")
    
    # Handle Grab event (download started)
    if webhook.eventType == "Grab":
        try:
            tmdb_id = webhook.movie.tmdbId
            
            # Find all requests for this movie
            requests = db.query(MediaRequest).filter(
                MediaRequest.media_type == "movie",
                MediaRequest.tmdb_id == tmdb_id
            ).all()
            
            if not requests:
                return WebhookResponse(success=True, message="No matching requests found")
            
            # Cancel any pending quality_waiting notifications since download is starting
            cancelled_count = 0
            for request in requests:
                cancelled = db.query(Notification).filter(
                    Notification.request_id == request.id,
                    Notification.notification_type == "quality_waiting",
                    Notification.sent == False
                ).delete()
                cancelled_count += cancelled
            
            db.commit()
            
            if cancelled_count > 0:
                logger.info(f"Grab event: Cancelled {cancelled_count} pending quality_waiting notification(s) for {webhook.movie.title} - download started")
            
            return WebhookResponse(
                success=True,
                message=f"Download started for {webhook.movie.title}, cancelled quality waiting notifications",
                processed_items=cancelled_count
            )
        except Exception as e:
            logger.error(f"Error processing Radarr Grab webhook: {e}")
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))
    
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
            
            # Check if the downloaded file meets quality cutoff
            quality_cutoff_met = True
            if webhook.movieFile:
                quality_cutoff_met = not webhook.movieFile.get('qualityCutoffNotMet', False)
                logger.info(f"Movie downloaded - Quality cutoff met: {quality_cutoff_met}")
            
            # Only cancel quality_waiting notifications if quality is correct
            if quality_cutoff_met:
                cancelled_count = db.query(Notification).filter(
                    Notification.request_id == request.id,
                    Notification.notification_type == "quality_waiting",
                    Notification.sent == False
                ).delete()
                
                if cancelled_count > 0:
                    logger.info(f"Cancelled {cancelled_count} pending quality_waiting notification(s) - correct quality downloaded")
            else:
                logger.info(f"Quality cutoff not met - skipping 'Movie Available' notification, keeping quality_waiting active")
                continue  # Skip to next request - don't send "available" for wrong quality
            
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
        
        # Check if this download resolves any reported issues
        background_tasks.add_task(_check_issue_resolution, webhook.movie.tmdbId, "movie")
        
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
    webhook: dict,  # Using dict because Seerr webhook format varies
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Handle webhooks from Seerr for request events and issue reports
    Supports: MEDIA_PENDING, MEDIA_APPROVED, MEDIA_AUTO_APPROVED, MEDIA_AVAILABLE,
              ISSUE_CREATED, ISSUE_COMMENT, ISSUE_RESOLVED, ISSUE_REOPENED
    """
    try:
        logger.info(f"Received Seerr webhook: {webhook.get('notification_type')}")
        logger.debug(f"Seerr webhook payload: {webhook}")
        
        notification_type = webhook.get('notification_type', '')
        
        # Handle issue events
        if notification_type in ('ISSUE_CREATED', 'ISSUE_COMMENT'):
            return await _handle_issue_webhook(webhook, background_tasks, db)
        
        if notification_type == 'ISSUE_RESOLVED':
            return await _handle_issue_resolved_webhook(webhook, db)
        
        if notification_type == 'ISSUE_REOPENED':
            return await _handle_issue_reopened_webhook(webhook, db)
        
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
        
        # Trigger immediate quality/release check for approved requests
        if notification_type in ['MEDIA_APPROVED', 'MEDIA_AUTO_APPROVED']:
            from app.config import settings
            if settings.quality_monitor_enabled and request_obj:
                # Schedule quality check in background (don't block webhook response)
                background_tasks.add_task(check_request_quality_status, request_obj.id)
        
        return WebhookResponse(
            success=True,
            message=f"Processed request: {title}",
            processed_items=1
        )
        
    except Exception as e:
        logger.error(f"Error processing Jellyseerr webhook: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


async def check_request_quality_status(request_id: int):
    """Background task to check if newly approved request needs quality/release notification"""
    try:
        from app.background.quality_monitor import QualityReleaseMonitor
        import asyncio
        
        # Wait 10 seconds to give Radarr/Sonarr time to add the content
        logger.info(f"Waiting 10 seconds before quality check for request {request_id}")
        await asyncio.sleep(10)
        
        db = next(get_db())
        request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
        
        if not request:
            logger.warning(f"Request {request_id} not found for quality check")
            return
        
        monitor = QualityReleaseMonitor()
        
        if request.media_type == 'tv':
            await monitor._check_tv_show(request, db)
        elif request.media_type == 'movie':
            await monitor._check_movie(request, db)
        
        db.close()
        logger.info(f"Completed immediate quality check for request {request_id}")
        
    except Exception as e:
        logger.error(f"Failed to check request quality: {e}")


async def _handle_issue_webhook(webhook: dict, background_tasks: BackgroundTasks, db: Session):
    """Handle ISSUE_CREATED / ISSUE_COMMENT webhooks from Seerr"""
    from app.database import ReportedIssue
    from app.config import settings as app_settings
    
    try:
        media = webhook.get('media', {})
        issue = webhook.get('issue', {})
        extra = webhook.get('extra', [])
        subject = webhook.get('subject', '')
        message_text = webhook.get('message', '')
        
        media_type = media.get('media_type', '')
        tmdb_id = media.get('tmdbId')
        
        if not tmdb_id:
            logger.warning("Issue webhook missing TMDB ID")
            return WebhookResponse(success=False, message="No TMDB ID in issue webhook")
        
        # Extract issue details
        seerr_issue_id = issue.get('issue_id') or issue.get('id') if issue else None
        
        # Extract issue type - try multiple field names and formats
        issue_type = 'other'
        if issue:
            # Try camelCase (issueType) - may be int or string
            raw_type = issue.get('issueType')
            # Try snake_case (issue_type) - Seerr template variable format
            if raw_type is None:
                raw_type = issue.get('issue_type')
            # Try just 'type'
            if raw_type is None:
                raw_type = issue.get('type')
            
            if raw_type is not None:
                # Map integer values: 1=video, 2=audio, 3=subtitle, 4=other
                issue_type_map = {1: 'video', 2: 'audio', 3: 'subtitle', 4: 'other'}
                if isinstance(raw_type, int):
                    issue_type = issue_type_map.get(raw_type, 'other')
                elif isinstance(raw_type, str):
                    # Normalize string values from Seerr
                    type_lower = raw_type.lower().strip()
                    if type_lower in ('video', 'audio', 'subtitle', 'subtitles', 'other'):
                        issue_type = 'subtitle' if type_lower == 'subtitles' else type_lower
                    elif type_lower.isdigit():
                        issue_type = issue_type_map.get(int(type_lower), 'other')
                    else:
                        issue_type = 'other'
        
        # Fallback: try to extract issue type from subject line
        # Seerr subjects often contain "A video/audio/subtitle issue"
        if issue_type == 'other' and subject:
            subject_lower = subject.lower()
            if 'video' in subject_lower:
                issue_type = 'video'
            elif 'audio' in subject_lower:
                issue_type = 'audio'
            elif 'subtitle' in subject_lower:
                issue_type = 'subtitle'
        
        # Fallback: check extra array for issue type
        if issue_type == 'other' and extra:
            for item in extra:
                if item.get('name', '').lower() in ('issue type', 'issuetype', 'type'):
                    val = str(item.get('value', '')).lower().strip()
                    if val in ('video', 'audio', 'subtitle', 'subtitles'):
                        issue_type = 'subtitle' if val == 'subtitles' else val
                        break
        
        
        issue_message = message_text or ''
        
        # Try to get issue message from extra
        if not issue_message and extra:
            for item in extra:
                if item.get('name') == 'Comment' and item.get('value'):
                    issue_message = item.get('value')
                    break
        
        # Extract title
        title = subject or ''
        for item in (extra or []):
            if item.get('name') == 'Reported Media' and item.get('value'):
                title = item.get('value')
                break
        if not title:
            title = f"Unknown ({media_type} TMDB:{tmdb_id})"
        
        # Find the reporting user
        reported_by_username = None
        reported_by_email = None
        for item in (extra or []):
            if item.get('name') == 'Reported By' and item.get('value'):
                reported_by_username = item.get('value')
            elif item.get('name') == 'Email' and item.get('value'):
                reported_by_email = item.get('value')
        
        # Also check the issue object itself
        if not reported_by_username:
            reported_by_username = issue.get('reportedBy_username')
        if not reported_by_email:
            reported_by_email = issue.get('reportedBy_email')
        
        # Find user in our database
        user = None
        if reported_by_email:
            user = db.query(User).filter(User.email == reported_by_email).first()
        if not user and reported_by_username:
            user = db.query(User).filter(User.username == reported_by_username).first()
        
        # Find matching media request
        request_obj = db.query(MediaRequest).filter(
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.media_type == media_type
        ).first()
        
        # Create the reported issue record
        reported_issue = ReportedIssue(
            seerr_issue_id=seerr_issue_id,
            user_id=user.id if user else None,
            request_id=request_obj.id if request_obj else None,
            media_type=media_type,
            tmdb_id=tmdb_id,
            title=title,
            issue_type=issue_type,
            issue_message=issue_message,
            status="reported"
        )
        db.add(reported_issue)
        db.commit()
        db.refresh(reported_issue)
        
        logger.info(f"Issue reported: {title} ({media_type}) - Type: {issue_type} - By: {reported_by_username or 'Unknown'}")
        
        # Get autofix mode
        import os
        autofix_mode = os.getenv("ISSUE_AUTOFIX_MODE", app_settings.issue_autofix_mode)
        
        # Send admin notification (always in manual, optionally in auto modes)
        if autofix_mode == "manual" or autofix_mode == "auto_notify":
            background_tasks.add_task(
                _send_issue_admin_notification,
                reported_issue.id,
                reported_by_username or reported_by_email or "Unknown"
            )
        
        # Auto-fix if enabled
        if autofix_mode in ("auto", "auto_notify"):
            background_tasks.add_task(_auto_fix_issue, reported_issue.id)
        
        return WebhookResponse(
            success=True,
            message=f"Issue recorded: {title} (mode: {autofix_mode})",
            processed_items=1
        )
        
    except Exception as e:
        logger.error(f"Error processing issue webhook: {e}", exc_info=True)
        db.rollback()
        return WebhookResponse(success=False, message=f"Error: {str(e)}")


async def _handle_issue_resolved_webhook(webhook: dict, db: Session):
    """Handle ISSUE_RESOLVED webhook from Seerr — mark matching issues as resolved"""
    from app.database import ReportedIssue
    from datetime import datetime
    
    try:
        media = webhook.get('media', {})
        tmdb_id = media.get('tmdbId')
        media_type = media.get('media_type', '')
        
        if not tmdb_id:
            return WebhookResponse(success=False, message="No TMDB ID in resolved webhook")
        
        # Find open issues for this media
        open_issues = db.query(ReportedIssue).filter(
            ReportedIssue.tmdb_id == tmdb_id,
            ReportedIssue.media_type == media_type,
            ReportedIssue.status.in_(["reported", "fixing", "failed"])
        ).all()
        
        resolved_count = 0
        for issue in open_issues:
            issue.status = "resolved"
            issue.action_taken = issue.action_taken or "resolved_in_seerr"
            issue.resolved_at = datetime.utcnow()
            resolved_count += 1
        
        db.commit()
        logger.info(f"Issue resolved via Seerr: TMDB {tmdb_id} — marked {resolved_count} issue(s) as resolved")
        
        return WebhookResponse(
            success=True,
            message=f"Marked {resolved_count} issue(s) as resolved",
            processed_items=resolved_count
        )
    except Exception as e:
        logger.error(f"Error processing issue resolved webhook: {e}", exc_info=True)
        db.rollback()
        return WebhookResponse(success=False, message=f"Error: {str(e)}")


async def _handle_issue_reopened_webhook(webhook: dict, db: Session):
    """Handle ISSUE_REOPENED webhook from Seerr — set resolved issues back to reported"""
    from app.database import ReportedIssue
    
    try:
        media = webhook.get('media', {})
        tmdb_id = media.get('tmdbId')
        media_type = media.get('media_type', '')
        
        if not tmdb_id:
            return WebhookResponse(success=False, message="No TMDB ID in reopened webhook")
        
        # Find resolved issues for this media
        resolved_issues = db.query(ReportedIssue).filter(
            ReportedIssue.tmdb_id == tmdb_id,
            ReportedIssue.media_type == media_type,
            ReportedIssue.status == "resolved"
        ).all()
        
        reopened_count = 0
        for issue in resolved_issues:
            issue.status = "reported"
            issue.resolved_at = None
            issue.error_message = None
            reopened_count += 1
        
        db.commit()
        logger.info(f"Issue reopened via Seerr: TMDB {tmdb_id} — reopened {reopened_count} issue(s)")
        
        return WebhookResponse(
            success=True,
            message=f"Reopened {reopened_count} issue(s)",
            processed_items=reopened_count
        )
    except Exception as e:
        logger.error(f"Error processing issue reopened webhook: {e}", exc_info=True)
        db.rollback()
        return WebhookResponse(success=False, message=f"Error: {str(e)}")


async def _send_issue_admin_notification(issue_id: int, reported_by: str):
    """Background task to send admin notification about a reported issue"""
    try:
        from app.config import settings as app_settings
        from app.services.email_service import EmailService
        import os
        
        db = next(get_db())
        try:
            from app.database import ReportedIssue
            issue = db.query(ReportedIssue).filter(ReportedIssue.id == issue_id).first()
            if not issue:
                return
            
            admin_email = os.getenv("ADMIN_EMAIL") or app_settings.admin_email or app_settings.smtp_from
            if not admin_email:
                logger.warning("No admin email configured, skipping issue notification")
                return
            
            # Clean admin email (handle "Name <email>" format)
            if '<' in admin_email and '>' in admin_email:
                admin_email = admin_email.split('<')[1].split('>')[0]
            
            autofix_mode = os.getenv("ISSUE_AUTOFIX_MODE", app_settings.issue_autofix_mode)
            
            email_svc = EmailService()
            html_body = email_svc.render_issue_reported_admin_notification(
                title=issue.title,
                media_type=issue.media_type,
                issue_type=issue.issue_type or "other",
                issue_message=issue.issue_message or "",
                reported_by=reported_by,
                autofix_mode=autofix_mode
            )
            
            await email_svc.send_email(
                to_email=admin_email,
                subject=f"🚨 Issue Reported: {issue.title}",
                html_body=html_body
            )
            logger.info(f"Sent issue notification to admin: {admin_email}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to send admin issue notification: {e}")


async def _auto_fix_issue(issue_id: int):
    """Background task to automatically blacklist + re-search for a reported issue"""
    import asyncio
    
    try:
        # Small delay to let DB commit settle
        await asyncio.sleep(2)
        
        db = next(get_db())
        try:
            from app.database import ReportedIssue
            issue = db.query(ReportedIssue).filter(ReportedIssue.id == issue_id).first()
            if not issue:
                return
            
            issue.status = "fixing"
            db.commit()
            
            logger.info(f"Auto-fixing issue #{issue.id}: {issue.title} ({issue.media_type})")
            
            if issue.media_type == "movie":
                from app.services.radarr_service import RadarrService
                radarr = RadarrService()
                result = await radarr.blacklist_and_research_movie(issue.tmdb_id)
            elif issue.media_type == "tv":
                from app.services.sonarr_service import SonarrService
                sonarr_svc = SonarrService()
                result = await sonarr_svc.blacklist_and_research_series(issue.tmdb_id)
            else:
                result = {"success": False, "message": f"Unknown media type: {issue.media_type}"}
            
            if result["success"]:
                issue.status = "fixing"  # Will be set to 'resolved' when import webhook fires
                issue.action_taken = "blacklist_research"
                logger.info(f"Auto-fix initiated for issue #{issue.id}: {result['message']}")
            else:
                issue.status = "failed"
                issue.error_message = result["message"]
                logger.error(f"Auto-fix failed for issue #{issue.id}: {result['message']}")
            
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to auto-fix issue {issue_id}: {e}")


async def _check_issue_resolution(tmdb_id: int, media_type: str):
    """Background task: when a file is imported, check if it resolves any 'fixing' issues.
    If so, mark resolved and send 'Issue Resolved' email to the reporting user."""
    try:
        from app.database import ReportedIssue
        from app.services.email_service import EmailService
        from app.services.tmdb_service import TMDBService
        from app.config import settings as app_settings
        from datetime import datetime
        
        db = next(get_db())
        try:
            # Find issues in 'fixing' status for this media
            fixing_issues = db.query(ReportedIssue).filter(
                ReportedIssue.tmdb_id == tmdb_id,
                ReportedIssue.media_type == media_type,
                ReportedIssue.status == "fixing"
            ).all()
            
            if not fixing_issues:
                return
            
            logger.info(f"Found {len(fixing_issues)} fixing issue(s) for TMDB {tmdb_id} - marking as resolved")
            
            email_svc = EmailService()
            tmdb_service = TMDBService(app_settings.jellyseerr_url, app_settings.jellyseerr_api_key)
            
            # Get poster
            if media_type == "movie":
                poster_url = await tmdb_service.get_movie_poster(tmdb_id)
            else:
                poster_url = await tmdb_service.get_tv_poster(tmdb_id)
            
            for issue in fixing_issues:
                issue.status = "resolved"
                issue.resolved_at = datetime.utcnow()
                
                # Close the issue in Seerr
                if issue.seerr_issue_id:
                    try:
                        from app.services.seerr_service import SeerrService
                        seerr = SeerrService()
                        result = await seerr.resolve_issue(issue.seerr_issue_id)
                        if result["success"]:
                            logger.info(f"Closed issue #{issue.seerr_issue_id} in Seerr")
                        else:
                            logger.warning(f"Could not close issue in Seerr: {result['message']}")
                    except Exception as e:
                        logger.warning(f"Failed to close issue in Seerr: {e}")
                
                # Send "Issue Resolved" email to the user who reported it
                if issue.user_id and issue.user:
                    html_body = email_svc.render_issue_resolved_notification(
                        title=issue.title,
                        media_type=issue.media_type,
                        issue_type=issue.issue_type,
                        poster_url=poster_url
                    )
                    
                    from datetime import timedelta
                    send_after = datetime.utcnow() + timedelta(seconds=300)  # 5 min delay for Plex indexing
                    
                    notification = Notification(
                        user_id=issue.user_id,
                        request_id=issue.request_id or 0,
                        notification_type="issue_resolved",
                        subject=f"✅ Issue Resolved: {issue.title}",
                        body=html_body,
                        send_after=send_after
                    )
                    db.add(notification)
                    logger.info(f"Queued 'Issue Resolved' notification for user {issue.user.email}")
            
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to check issue resolution for TMDB {tmdb_id}: {e}")

