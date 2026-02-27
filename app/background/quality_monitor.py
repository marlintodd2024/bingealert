"""
Quality and Release Monitor
Checks pending requests for:
1. Content not yet released (send "coming soon" notification with premiere date)
2. Content available but wrong quality (send "waiting for quality" notification)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db, MediaRequest, Notification, User, EpisodeTracking
from app.services.email_service import EmailService
from app.services.sonarr_service import SonarrService
from app.services.radarr_service import RadarrService
from app.services.tmdb_service import TMDBService
from app.config import settings

logger = logging.getLogger(__name__)


class QualityReleaseMonitor:
    def __init__(self):
        self.email_service = EmailService()
        self.sonarr = SonarrService()
        self.radarr = RadarrService()
        self.tmdb = TMDBService(settings.jellyseerr_url, settings.jellyseerr_api_key)
    
    async def run(self):
        """Run the quality/release monitoring check"""
        logger.info("Starting quality/release monitoring check...")
        
        db = next(get_db())
        try:
            # Get all approved requests that aren't available yet
            pending_requests = db.query(MediaRequest).filter(
                MediaRequest.status.in_(['pending', 'approved']),
                MediaRequest.jellyseerr_request_id.isnot(None)
            ).all()
            
            logger.info(f"Checking {len(pending_requests)} pending requests")
            
            for request in pending_requests:
                try:
                    if request.media_type == 'tv':
                        await self._check_tv_show(request, db)
                    elif request.media_type == 'movie':
                        await self._check_movie(request, db)
                except Exception as e:
                    logger.error(f"Failed to check request {request.id} ({request.title}): {e}")
            
            db.commit()
            logger.info("Quality/release monitoring check completed")
            
        except Exception as e:
            logger.error(f"Quality monitoring failed: {e}")
            db.rollback()
        finally:
            db.close()
    
    async def _check_tv_show(self, request: MediaRequest, db: Session):
        """Check TV show for release status and quality"""
        # If we don't have a series_id, try to find it in Sonarr by TMDB ID
        series = None
        
        if hasattr(request, 'series_id') and request.series_id:
            series = await self.sonarr.get_series(request.series_id)
        else:
            # Look up series by TMDB ID in Sonarr
            all_series = await self.sonarr.get_all_series()
            if all_series:
                series = next((s for s in all_series if s.get('tvdbId') == request.tmdb_id), None)
        
        if not series:
            logger.debug(f"Series not yet in Sonarr for request {request.id} ({request.title})")
            return
        
        # Check if series hasn't premiered yet
        if series.get('status') == 'upcoming':
            premiere_date = series.get('firstAired')
            if premiere_date:
                await self._send_coming_soon_notification(
                    request=request,
                    premiere_date=premiere_date,
                    db=db
                )
                return
        
        # Check if waiting for better quality
        # Get all episodes for this series
        episodes = await self.sonarr.get_episodes_by_series(series.get('id'))
        if not episodes:
            return
        
        # Check if any episodes are available but in wrong quality
        quality_profile = series.get('qualityProfileId')
        for episode in episodes:
            if episode.get('hasFile') and not episode.get('episodeFile', {}).get('qualityCutoffNotMet', False):
                continue  # Episode has file and meets quality requirements
            
            # Check if episode aired but waiting for quality
            air_date = episode.get('airDateUtc')
            if air_date:
                air_datetime = datetime.fromisoformat(air_date.replace('Z', '+00:00'))
                if air_datetime < datetime.now(timezone.utc) - timedelta(days=7):  # Aired more than a week ago
                    # Check if we already notified about quality waiting
                    if not self._already_notified_quality_wait(request, db):
                        # Get quality profile name from series
                        quality_profile_id = series.get('qualityProfileId')
                        quality_profile_name = 'Unknown'
                        
                        # Try to get the actual profile name from Sonarr
                        if quality_profile_id:
                            # Quality profiles are in the series object as qualityProfile
                            quality_obj = series.get('qualityProfile')
                            if quality_obj and isinstance(quality_obj, dict):
                                quality_profile_name = quality_obj.get('name', 'Unknown')
                            else:
                                # Look up profile name from Sonarr API
                                try:
                                    profiles = await self.sonarr.get_quality_profiles()
                                    profile = next((p for p in profiles if p.get('id') == quality_profile_id), None)
                                    if profile:
                                        quality_profile_name = profile.get('name', f"Profile ID {quality_profile_id}")
                                    else:
                                        quality_profile_name = f"Profile ID {quality_profile_id}"
                                except Exception as e:
                                    logger.error(f"Failed to lookup quality profile: {e}")
                                    quality_profile_name = f"Profile ID {quality_profile_id}"
                        
                        await self._send_quality_waiting_notification(
                            request=request,
                            quality_profile_name=quality_profile_name,
                            db=db
                        )
                        return  # Only send one notification per check
    
    async def _check_movie(self, request: MediaRequest, db: Session):
        """Check movie for release status and quality"""
        # Get all movies from Radarr
        movies = await self.radarr.get_movies()
        
        # Find movie by movie_id or TMDB ID
        movie = None
        if hasattr(request, 'movie_id') and request.movie_id:
            movie = next((m for m in movies if m.get('id') == request.movie_id), None)
        
        if not movie and request.tmdb_id:
            # Look up by TMDB ID
            movie = next((m for m in movies if m.get('tmdbId') == request.tmdb_id), None)
        
        if not movie:
            logger.info(f"Movie '{request.title}' (TMDB: {request.tmdb_id}) not yet in Radarr - skipping quality check")
            return
        
        logger.info(f"Checking movie '{request.title}' - Status: {movie.get('status')}, HasFile: {movie.get('hasFile')}")
        
        # Check release status
        status = movie.get('status', '')
        
        # Check if movie is announced/inCinemas and not released yet
        if status in ['announced', 'inCinemas']:
            # Check digital release date
            digital_release = movie.get('digitalRelease')
            physical_release = movie.get('physicalRelease')
            in_cinemas = movie.get('inCinemas')
            
            release_date = digital_release or physical_release or in_cinemas
            
            logger.info(f"Movie status '{status}' - Release date: {release_date}")
            
            if release_date:
                release_datetime = datetime.fromisoformat(release_date.replace('Z', '+00:00'))
                if release_datetime > datetime.now(timezone.utc):
                    logger.info(f"Movie not yet released - sending coming soon notification")
                    await self._send_coming_soon_notification(
                        request=request,
                        premiere_date=release_date,
                        db=db
                    )
                    return
                else:
                    logger.info(f"Movie released on {release_date} but not downloaded - will check quality below")
                    # Don't return - continue to quality check below
        
        
        # Check if movie has file but wrong quality OR if released but no file yet
        if movie.get('hasFile'):
            # Movie downloaded but might be wrong quality
            movie_file = movie.get('movieFile', {})
            quality_cutoff_not_met = movie_file.get('qualityCutoffNotMet', False)
            
            if quality_cutoff_not_met:
                logger.info(f"Movie has file but quality cutoff not met - sending quality waiting notification")
                if not self._already_notified_quality_wait(request, db):
                    # Get quality profile name from movie
                    quality_profile_id = movie.get('qualityProfileId')
                    quality_profile_name = 'Unknown'
                    
                    # Try to get the actual profile name
                    quality_obj = movie.get('qualityProfile')
                    if quality_obj and isinstance(quality_obj, dict):
                        quality_profile_name = quality_obj.get('name', 'Unknown')
                    elif quality_profile_id:
                        # Look up profile name from Radarr API
                        try:
                            profiles = await self.radarr.get_quality_profiles()
                            profile = next((p for p in profiles if p.get('id') == quality_profile_id), None)
                            if profile:
                                quality_profile_name = profile.get('name', f"Profile ID {quality_profile_id}")
                            else:
                                quality_profile_name = f"Profile ID {quality_profile_id}"
                        except Exception as e:
                            logger.error(f"Failed to lookup quality profile: {e}")
                            quality_profile_name = f"Profile ID {quality_profile_id}"
                    
                    await self._send_quality_waiting_notification(
                        request=request,
                        quality_profile_name=quality_profile_name,
                        db=db
                    )
        elif status in ['released', 'inCinemas']:
            # Movie is released/inCinemas but hasn't been downloaded yet - likely waiting for quality
            logger.info(f"Movie status '{status}' but no file - likely waiting for quality profile")
            
            already_notified = self._already_notified_quality_wait(request, db)
            logger.info(f"Already notified check: {already_notified}")
            
            if not already_notified:
                # Get quality profile name from movie
                quality_profile_id = movie.get('qualityProfileId')
                quality_profile_name = 'Unknown'
                
                # Try to get the actual profile name
                quality_obj = movie.get('qualityProfile')
                if quality_obj and isinstance(quality_obj, dict):
                    quality_profile_name = quality_obj.get('name', 'Unknown')
                elif quality_profile_id:
                    # Look up profile name from Radarr API
                    try:
                        profiles = await self.radarr.get_quality_profiles()
                        profile = next((p for p in profiles if p.get('id') == quality_profile_id), None)
                        if profile:
                            quality_profile_name = profile.get('name', f"Profile ID {quality_profile_id}")
                        else:
                            quality_profile_name = f"Profile ID {quality_profile_id}"
                    except Exception as e:
                        logger.error(f"Failed to lookup quality profile: {e}")
                        quality_profile_name = f"Profile ID {quality_profile_id}"
                
                await self._send_quality_waiting_notification(
                    request=request,
                    quality_profile_name=quality_profile_name,
                    db=db
                )
    
    async def _send_coming_soon_notification(
        self, 
        request: MediaRequest, 
        premiere_date: str,
        db: Session
    ):
        """Send 'coming soon' notification with premiere date"""
        
        # Check if we already sent this notification
        if self._already_notified_coming_soon(request, db):
            return
        
        # Get poster
        poster_url = None
        if request.media_type == 'tv':
            poster_url = await self.tmdb.get_tv_poster(request.tmdb_id)
        else:
            poster_url = await self.tmdb.get_movie_poster(request.tmdb_id)
        
        # Parse premiere date
        try:
            premiere_datetime = datetime.fromisoformat(premiere_date.replace('Z', '+00:00'))
            formatted_date = premiere_datetime.strftime('%B %d, %Y')
        except:
            formatted_date = premiere_date
        
        # Get user
        user = request.user
        
        # Create HTML email
        html_body = self.email_service.render_coming_soon_notification(
            title=request.title,
            media_type=request.media_type,
            premiere_date=formatted_date,
            poster_url=poster_url
        )
        
        subject = f"Coming Soon: {request.title}"
        if request.media_type == 'movie':
            subject += f" - Releases {formatted_date}"
        else:
            subject += f" - Premieres {formatted_date}"
        
        # Create notification record
        notification = Notification(
            user_id=user.id,
            request_id=request.id,
            notification_type="coming_soon",
            subject=subject,
            body=html_body,
            sent=False
        )
        db.add(notification)
        db.commit()
        
        # Send immediately
        try:
            await self.email_service.send_email(
                to_email=user.email,
                subject=subject,
                html_body=html_body
            )
            notification.sent = True
            notification.sent_at = datetime.now(timezone.utc)
            db.commit()
            
            logger.info(f"Sent 'coming soon' notification for {request.title} to {user.email}")
        except Exception as e:
            logger.error(f"Failed to send coming soon notification: {e}")
            notification.error_message = str(e)
            db.commit()
    
    async def _send_quality_waiting_notification(
        self,
        request: MediaRequest,
        quality_profile_name: str,
        db: Session
    ):
        """Send 'waiting for quality' notification"""
        
        # Get poster
        poster_url = None
        if request.media_type == 'tv':
            poster_url = await self.tmdb.get_tv_poster(request.tmdb_id)
        else:
            poster_url = await self.tmdb.get_movie_poster(request.tmdb_id)
        
        # Get user
        user = request.user
        
        # Create HTML email
        html_body = self.email_service.render_quality_waiting_notification(
            title=request.title,
            media_type=request.media_type,
            quality_profile=quality_profile_name,
            poster_url=poster_url
        )
        
        subject = f"Waiting for {quality_profile_name}: {request.title}"
        
        # Add delay to allow cancellation if content downloads quickly
        from datetime import timedelta
        from app.config import settings
        send_after = datetime.now(timezone.utc) + timedelta(seconds=settings.quality_waiting_delay_seconds)
        
        # Create notification record
        notification = Notification(
            user_id=user.id,
            request_id=request.id,
            notification_type="quality_waiting",
            subject=subject,
            body=html_body,
            sent=False,
            send_after=send_after
        )
        db.add(notification)
        db.commit()
        
        logger.info(f"Queued 'quality waiting' notification for {request.title} to {user.email}, will send after {send_after}")
        
        # Don't send immediately - let the notification processor handle it
        # This allows it to be cancelled if the movie downloads in correct quality before the delay expires
    
    def _already_notified_coming_soon(self, request: MediaRequest, db: Session) -> bool:
        """Check if we already sent a 'coming soon' notification for this request"""
        # Only send once every 30 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        
        existing = db.query(Notification).filter(
            Notification.request_id == request.id,
            Notification.notification_type == "coming_soon",
            Notification.sent == True,
            Notification.sent_at > cutoff
        ).first()
        
        return existing is not None
    
    def _already_notified_quality_wait(self, request: MediaRequest, db: Session) -> bool:
        """Check if we already sent a 'quality waiting' notification for this request"""
        # Only send once every 7 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        
        existing = db.query(Notification).filter(
            Notification.request_id == request.id,
            Notification.notification_type == "quality_waiting",
            Notification.sent == True,
            Notification.sent_at > cutoff
        ).first()
        
        return existing is not None


async def run_quality_release_monitor():
    """Entry point for background task"""
    monitor = QualityReleaseMonitor()
    await monitor.run()


async def quality_release_monitor_worker():
    """Background worker that runs at configured interval"""
    from app.config import settings
    
    logger.info("Quality/Release monitor worker started")
    
    while True:
        try:
            if settings.quality_monitor_enabled:
                await run_quality_release_monitor()
            else:
                logger.debug("Quality monitoring is disabled in settings")
            
            # Wait configured interval before next check
            interval_seconds = settings.quality_monitor_interval_hours * 3600
            logger.info(f"Next quality check in {settings.quality_monitor_interval_hours} hours")
            await asyncio.sleep(interval_seconds)
        except Exception as e:
            logger.error(f"Quality/release monitor failed: {e}")
            # Wait 1 hour on error before retrying
            await asyncio.sleep(3600)


# For testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_quality_release_monitor())
