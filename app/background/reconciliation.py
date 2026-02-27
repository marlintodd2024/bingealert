"""
Reconciliation service to catch missed webhooks
Runs periodically to check if downloads completed but notifications weren't sent
"""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.database import SessionLocal, MediaRequest, EpisodeTracking, Notification, User
from app.services.sonarr_service import SonarrService
from app.services.radarr_service import RadarrService
from app.services.plex_service import PlexService
from app.services.email_service import EmailService
from app.services.tmdb_service import TMDBService
from app.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def reconcile_tv_episodes(db: Session):
    """Check for TV episodes that are downloaded but not notified"""
    logger.info("Starting TV episode reconciliation...")
    
    sonarr = SonarrService()
    plex = PlexService()
    email_service = EmailService()
    tmdb_service = TMDBService(settings.jellyseerr_url, settings.jellyseerr_api_key)
    
    # FIRST: Check for episodes that are tracked but never notified (missed webhooks!)
    logger.info("Checking for tracked episodes that never got notifications...")
    
    # Check ALL tracking records - webhook might have marked notified=True but failed to create notification
    all_tracking = db.query(EpisodeTracking).all()
    
    logger.info(f"Found {len(all_tracking)} total tracked episodes, checking for missing notifications...")
    
    notifications_created = 0
    orphaned_count = 0
    
    for tracking in all_tracking:
        try:
            # Get the request for this tracking
            request = db.query(MediaRequest).filter(
                MediaRequest.id == tracking.request_id
            ).first()
            
            if not request:
                logger.warning(f"Tracking {tracking.id} has no associated request - cleaning up")
                db.delete(tracking)
                continue
            
            # Check if notification already exists
            existing_notification = db.query(Notification).filter(
                Notification.user_id == request.user_id,
                Notification.request_id == request.id,
                Notification.notification_type == "episode",
                Notification.subject.contains(f"S{tracking.season_number:02d}E{tracking.episode_number:02d}")
            ).first()
            
            if existing_notification:
                # Notification exists - mark tracking as notified if not already
                if not tracking.notified:
                    tracking.notified = True
                continue
            
            # NO NOTIFICATION EXISTS! Orphaned episode
            orphaned_count += 1
            logger.info(f"🎯 Orphaned: {request.title} S{tracking.season_number:02d}E{tracking.episode_number:02d} (notified={tracking.notified})")

            
            # Get series info from Sonarr to get the title
            series_list = await sonarr._get("/series")
            series = None
            for s in series_list:
                if s.get("id") == tracking.series_id:
                    series = s
                    break
            
            if not series:
                logger.warning(f"Series {tracking.series_id} not found in Sonarr - skipping")
                continue
            
            # Check if episode is in Plex
            in_plex = await plex.check_episode_in_plex(
                series.get("title"),
                tracking.season_number,
                tracking.episode_number
            )
            
            if not in_plex:
                logger.info(f"  Episode NOT in Plex yet: {series.get('title')} S{tracking.season_number:02d}E{tracking.episode_number:02d} - will check next time")
                continue
            
            logger.info(f"  ✅ Episode IS in Plex!")
            
            # Episode is tracked, in Plex, but never notified - CREATE NOTIFICATION!
            logger.info(f"🎯 Found orphaned episode: {series.get('title')} S{tracking.season_number:02d}E{tracking.episode_number:02d}")
            
            # Get poster
            poster_url = await tmdb_service.get_tv_poster(request.tmdb_id)
            
            # Create notification
            subject = f"New Episode: {series.get('title')} S{tracking.season_number:02d}E{tracking.episode_number:02d}"
            html_body = email_service.render_episode_notification(
                series_title=series.get("title"),
                episodes=[{
                    'season': tracking.season_number,
                    'episode': tracking.episode_number,
                    'title': tracking.episode_title or ""
                }],
                poster_url=poster_url
            )
            
            notification = Notification(
                user_id=request.user_id,
                request_id=request.id,
                notification_type="episode",
                subject=subject,
                body=html_body,
                send_after=datetime.utcnow(),  # Send immediately
                series_id=tracking.series_id
            )
            db.add(notification)
            tracking.notified = True
            notifications_created += 1
            
        except Exception as e:
            logger.error(f"Error processing orphaned tracking {tracking.id}: {e}")
            continue
    
    db.commit()
    logger.info(f"Found {orphaned_count} orphaned episodes from tracking table, created {notifications_created} notifications")
    
    # SECOND: Check for new episodes that aren't tracked yet (original logic)
    logger.info("Checking for untracked downloaded episodes...")
    
    # Get all TV requests
    tv_requests = db.query(MediaRequest).filter(
        MediaRequest.media_type == "tv",
        MediaRequest.status == "approved"
    ).all()
    
    new_episodes_found = 0
    
    for request in tv_requests:
        try:
            # Get series info from Sonarr
            series_list = await sonarr._get("/series")
            series = None
            for s in series_list:
                if s.get("tvdbId") == request.tmdb_id or s.get("title", "").lower() == request.title.lower():
                    series = s
                    break
            
            if not series:
                continue
            
            series_id = series["id"]
            
            # Get all episodes for this series
            episodes = await sonarr._get(f"/episode?seriesId={series_id}")
            
            for episode in episodes:
                # Skip if not downloaded (hasFile = False means not downloaded)
                if not episode.get("hasFile"):
                    continue
                
                season_num = episode.get("seasonNumber")
                episode_num = episode.get("episodeNumber")
                
                # Check if we're tracking this episode
                tracking = db.query(EpisodeTracking).filter(
                    EpisodeTracking.series_id == series_id,
                    EpisodeTracking.season_number == season_num,
                    EpisodeTracking.episode_number == episode_num
                ).first()
                
                # If not tracking, check if it's in Plex (might have been imported before tracking started)
                if not tracking:
                    # Check if episode is in Plex
                    in_plex = await plex.check_episode_in_plex(
                        series.get("title"),
                        season_num,
                        episode_num
                    )
                    
                    if not in_plex:
                        continue  # Not in Plex yet, skip
                    
                    # Create tracking record
                    tracking = EpisodeTracking(
                        series_id=series_id,
                        season_number=season_num,
                        episode_number=episode_num,
                        episode_title=episode.get("title"),
                        notified=False,
                        request_id=request.id
                    )
                    db.add(tracking)
                    db.commit()
                    logger.info(f"Created tracking for episode: {series.get('title')} S{season_num:02d}E{episode_num:02d}")
                
                # If already notified, skip
                if tracking.notified:
                    continue
                
                # Check if episode is actually in Plex
                in_plex = await plex.check_episode_in_plex(
                    series.get("title"),
                    season_num,
                    episode_num
                )
                
                if not in_plex:
                    continue  # Not in Plex yet, skip
                
                # Check if notification already exists
                existing_notification = db.query(Notification).filter(
                    Notification.user_id == request.user_id,
                    Notification.request_id == request.id,
                    Notification.notification_type == "episode",
                    Notification.subject.contains(f"S{season_num:02d}E{episode_num:02d}")
                ).first()
                
                if existing_notification:
                    # Notification exists but tracking wasn't marked - fix it
                    tracking.notified = True
                    db.commit()
                    continue
                
                # Missing notification! Episode is downloaded but never notified
                logger.info(f"Found missed episode notification: {series.get('title')} S{season_num:02d}E{episode_num:02d}")
                
                # Get poster
                poster_url = await tmdb_service.get_tv_poster(request.tmdb_id)
                
                # Create notification
                subject = f"New Episode: {series.get('title')} S{season_num:02d}E{episode_num:02d}"
                html_body = email_service.render_episode_notification(
                    series_title=series.get("title"),
                    episodes=[{
                        'season': season_num,
                        'episode': episode_num,
                        'title': episode.get("title", "")
                    }],
                    poster_url=poster_url
                )
                
                notification = Notification(
                    user_id=request.user_id,
                    request_id=request.id,
                    notification_type="episode",
                    subject=subject,
                    body=html_body,
                    send_after=datetime.utcnow(),  # Send immediately
                    series_id=series_id
                )
                db.add(notification)
                tracking.notified = True
                new_episodes_found += 1
            
            db.commit()
            
        except Exception as e:
            logger.error(f"Error reconciling series {request.title}: {e}")
            db.rollback()
            continue
    
    total_created = notifications_created + new_episodes_found
    logger.info(f"TV reconciliation complete. Created {total_created} notifications ({notifications_created} orphaned + {new_episodes_found} new)")
    return total_created


async def reconcile_movies(db: Session):
    """Check for movies that are downloaded but not notified"""
    logger.info("Starting movie reconciliation...")
    
    radarr = RadarrService()
    plex = PlexService()
    email_service = EmailService()
    tmdb_service = TMDBService(settings.jellyseerr_url, settings.jellyseerr_api_key)
    
    # Get all movie requests
    movie_requests = db.query(MediaRequest).filter(
        MediaRequest.media_type == "movie",
        MediaRequest.status == "approved"
    ).all()
    
    notifications_created = 0
    
    for request in movie_requests:
        try:
            # Check if already notified
            existing_notification = db.query(Notification).filter(
                Notification.user_id == request.user_id,
                Notification.request_id == request.id,
                Notification.notification_type == "movie"
            ).first()
            
            if existing_notification:
                continue  # Already notified
            
            # Get movie from Radarr
            movies = await radarr._get("/movie")
            movie = None
            for m in movies:
                if m.get("tmdbId") == request.tmdb_id or m.get("title", "").lower() == request.title.lower():
                    movie = m
                    break
            
            if not movie:
                continue
            
            # Check if downloaded (hasFile = True means downloaded)
            if not movie.get("hasFile"):
                continue
            
            # Check if in Plex
            in_plex = await plex.check_movie_in_plex(
                movie.get("title"),
                movie.get("year")
            )
            
            if not in_plex:
                continue  # Not in Plex yet
            
            # Missing notification! Movie is downloaded but never notified
            logger.info(f"Found missed movie notification: {movie.get('title')} ({movie.get('year')})")
            
            # Get poster
            poster_url = await tmdb_service.get_movie_poster(request.tmdb_id)
            
            # Create notification
            subject = f"New Movie: {movie.get('title')}"
            html_body = email_service.render_movie_notification(
                movie_title=movie.get("title"),
                year=movie.get("year"),
                poster_url=poster_url
            )
            
            notification = Notification(
                user_id=request.user_id,
                request_id=request.id,
                notification_type="movie",
                subject=subject,
                body=html_body,
                send_after=datetime.utcnow()  # Send immediately
            )
            db.add(notification)
            notifications_created += 1
            db.commit()
            
        except Exception as e:
            logger.error(f"Error reconciling movie {request.title}: {e}")
            db.rollback()
            continue
    
    logger.info(f"Movie reconciliation complete. Created {notifications_created} missed notifications.")
    return notifications_created


def get_reconciliation_settings():
    """Load reconciliation settings from database, with defaults"""
    try:
        from app.database import SessionLocal, SystemConfig
        db = SessionLocal()
        try:
            settings_map = {}
            for config in db.query(SystemConfig).filter(
                SystemConfig.key.like('reconciliation_%')
            ).all():
                settings_map[config.key] = config.value
            return {
                'interval_hours': int(settings_map.get('reconciliation_interval_hours', '2')),
                'issue_fixing_cutoff_hours': int(settings_map.get('reconciliation_issue_fixing_cutoff_hours', '1')),
                'issue_reported_cutoff_hours': int(settings_map.get('reconciliation_issue_reported_cutoff_hours', '24')),
                'issue_abandon_days': int(settings_map.get('reconciliation_issue_abandon_days', '7')),
            }
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to load reconciliation settings, using defaults: {e}")
        return {
            'interval_hours': 2,
            'issue_fixing_cutoff_hours': 1,
            'issue_reported_cutoff_hours': 24,
            'issue_abandon_days': 7,
        }


async def reconcile_issues(db: Session):
    """Check for issues stuck in 'fixing' or 'reported' status and resolve if content now available"""
    from app.database import ReportedIssue
    
    logger.info("Starting issue reconciliation...")
    
    radarr = RadarrService()
    sonarr = SonarrService()
    email_service = EmailService()
    tmdb_service = TMDBService(settings.jellyseerr_url, settings.jellyseerr_api_key)
    
    # Load configurable cutoffs
    recon_settings = get_reconciliation_settings()
    
    fixing_cutoff = datetime.utcnow() - timedelta(hours=recon_settings['issue_fixing_cutoff_hours'])
    reported_cutoff = datetime.utcnow() - timedelta(hours=recon_settings['issue_reported_cutoff_hours'])
    stale_cutoff = datetime.utcnow() - timedelta(days=recon_settings['issue_abandon_days'])
    
    fixing_issues = db.query(ReportedIssue).filter(
        ReportedIssue.status == "fixing",
        ReportedIssue.updated_at < fixing_cutoff
    ).all()
    
    reported_issues = db.query(ReportedIssue).filter(
        ReportedIssue.status == "reported",
        ReportedIssue.updated_at < reported_cutoff
    ).all()
    
    all_stale = fixing_issues + reported_issues
    
    if not all_stale:
        logger.info("No stale issues found")
        return 0
    
    logger.info(f"Found {len(fixing_issues)} fixing + {len(reported_issues)} reported stale issues to check")
    
    resolved_count = 0
    failed_count = 0
    
    for issue in all_stale:
        try:
            has_file = False
            
            if issue.media_type == "movie":
                # Check Radarr for the movie file
                movies = await radarr._get("/movie")
                for m in movies:
                    if m.get("tmdbId") == issue.tmdb_id and m.get("hasFile"):
                        has_file = True
                        break
            else:
                # For TV, check if any recent episode file exists
                # This is a simpler check — if the series has files, the re-download likely worked
                series_list = await sonarr._get("/series")
                for s in series_list:
                    if s.get("tvdbId") == issue.tmdb_id or s.get("title", "").lower() == issue.title.lower():
                        # Check episode files
                        episodes = await sonarr._get(f"/episode?seriesId={s['id']}")
                        for ep in episodes:
                            if ep.get("hasFile"):
                                has_file = True
                                break
                        break
            
            if has_file:
                # Content is available — resolve the issue
                logger.info(f"✅ Issue #{issue.seerr_issue_id} '{issue.title}' now has file — resolving")
                
                issue.status = "resolved"
                issue.resolved_at = datetime.utcnow()
                issue.action_taken = issue.action_taken or "resolved_by_reconciliation"
                
                # Close in Seerr
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
                
                # Send resolved email to user
                if issue.user_id:
                    try:
                        user = db.query(User).filter(User.id == issue.user_id).first()
                        if user:
                            if issue.media_type == "movie":
                                poster_url = await tmdb_service.get_movie_poster(issue.tmdb_id)
                            else:
                                poster_url = await tmdb_service.get_tv_poster(issue.tmdb_id)
                            
                            html_body = email_service.render_issue_resolved_notification(
                                title=issue.title,
                                media_type=issue.media_type,
                                issue_type=issue.issue_type,
                                poster_url=poster_url
                            )
                            
                            notification = Notification(
                                user_id=issue.user_id,
                                request_id=issue.request_id or 0,
                                notification_type="issue_resolved",
                                subject=f"✅ Issue Resolved: {issue.title}",
                                body=html_body,
                                send_after=datetime.utcnow() + timedelta(seconds=300)
                            )
                            db.add(notification)
                            logger.info(f"Queued 'Issue Resolved' notification for {user.email}")
                    except Exception as e:
                        logger.warning(f"Failed to send resolved notification: {e}")
                
                resolved_count += 1
            
            elif issue.updated_at < stale_cutoff:
                # Issue has been stuck too long with no file — mark as failed
                logger.warning(f"⚠️ Issue #{issue.seerr_issue_id} '{issue.title}' stuck for {recon_settings['issue_abandon_days']}+ days — marking failed")
                issue.status = "failed"
                issue.error_message = f"No replacement file found after {recon_settings['issue_abandon_days']} days"
                failed_count += 1
            
        except Exception as e:
            logger.error(f"Error reconciling issue {issue.id} '{issue.title}': {e}")
            continue
    
    db.commit()
    logger.info(f"Issue reconciliation complete: {resolved_count} resolved, {failed_count} failed")
    return resolved_count


async def run_reconciliation():
    """Main reconciliation task - runs periodically"""
    logger.info("=" * 60)
    logger.info("Starting reconciliation check...")
    logger.info("=" * 60)
    
    db = SessionLocal()
    try:
        tv_count = await reconcile_tv_episodes(db)
        movie_count = await reconcile_movies(db)
        issue_count = await reconcile_issues(db)
        
        total = tv_count + movie_count
        if total > 0:
            logger.info(f"✅ Reconciliation found {total} missed notifications!")
        if issue_count > 0:
            logger.info(f"✅ Reconciliation resolved {issue_count} stale issues!")
        if total == 0 and issue_count == 0:
            logger.info("✅ Reconciliation complete - nothing missed")
        
    except Exception as e:
        logger.error(f"Reconciliation error: {e}")
    finally:
        db.close()


async def reconciliation_worker():
    """Background worker that runs reconciliation periodically"""
    logger.info("🔄 Reconciliation worker started")
    
    while True:
        try:
            recon_settings = get_reconciliation_settings()
            interval_hours = recon_settings['interval_hours']
            await run_reconciliation()
        except Exception as e:
            logger.error(f"Reconciliation worker error: {e}")
            interval_hours = 2  # fallback
        
        logger.info(f"💤 Sleeping for {interval_hours} hours until next reconciliation check...")
        await asyncio.sleep(interval_hours * 60 * 60)
