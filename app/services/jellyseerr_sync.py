import httpx
import logging
from typing import List, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.database import User, MediaRequest, EpisodeTracking, get_db
from app.schemas import JellyseerrUser, JellyseerrRequest

logger = logging.getLogger(__name__)


class JellyseerrSyncService:
    def __init__(self):
        self.base_url = settings.jellyseerr_url.rstrip('/')
        self.api_key = settings.jellyseerr_api_key
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        }
    
    async def _get(self, endpoint: str) -> dict:
        """Make GET request to Jellyseerr API"""
        url = f"{self.base_url}/api/v1{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
    
    async def get_users(self) -> List[dict]:
        """Fetch all users from Jellyseerr"""
        try:
            data = await self._get("/user")
            return data.get("results", [])
        except Exception as e:
            logger.error(f"Failed to fetch users from Jellyseerr: {e}")
            return []
    
    async def get_requests(self, take: int = 100, skip: int = 0) -> List[dict]:
        """Fetch requests from Jellyseerr"""
        try:
            data = await self._get(f"/request?take={take}&skip={skip}")
            return data.get("results", [])
        except Exception as e:
            logger.error(f"Failed to fetch requests from Jellyseerr: {e}")
            return []
    
    async def sync_users(self):
        """Sync users from Jellyseerr to local database"""
        logger.info("Starting user sync from Jellyseerr...")
        users_data = await self.get_users()
        
        db = next(get_db())
        synced_count = 0
        
        try:
            for user_data in users_data:
                # Skip users without email
                if not user_data.get("email"):
                    logger.warning(f"Skipping user {user_data.get('id')} - no email address")
                    continue
                
                jellyseerr_id = user_data.get("id")
                existing_user = db.query(User).filter(User.jellyseerr_id == jellyseerr_id).first()
                
                # Use username, displayName, plexUsername, or email as fallback
                username = (user_data.get("username") or 
                          user_data.get("displayName") or 
                          user_data.get("plexUsername") or 
                          user_data.get("email").split("@")[0])
                
                if existing_user:
                    # Update existing user
                    existing_user.email = user_data.get("email")
                    existing_user.username = username
                    existing_user.plex_id = user_data.get("plexId")
                    logger.info(f"Updated user: {username} ({user_data.get('email')})")
                else:
                    # Create new user
                    new_user = User(
                        jellyseerr_id=jellyseerr_id,
                        email=user_data.get("email"),
                        username=username,
                        plex_id=user_data.get("plexId")
                    )
                    db.add(new_user)
                    logger.info(f"Created new user: {username} ({user_data.get('email')})")
                
                synced_count += 1
            
            db.commit()
            logger.info(f"Synced {synced_count} users from Jellyseerr")
        except Exception as e:
            db.rollback()
            logger.error(f"Error syncing users: {e}")
        finally:
            db.close()
    
    async def sync_requests(self):
        """Sync media requests from Jellyseerr to local database"""
        logger.info("Starting request sync from Jellyseerr...")
        requests_data = await self.get_requests(take=200)
        
        db = next(get_db())
        synced_count = 0
        
        try:
            # Import SonarrService for episode checking
            from app.services.sonarr_service import SonarrService
            sonarr = SonarrService()
            
            for request_data in requests_data:
                jellyseerr_request_id = request_data.get("id")
                existing_request = db.query(MediaRequest).filter(
                    MediaRequest.jellyseerr_request_id == jellyseerr_request_id
                ).first()
                
                # Get user
                requested_by = request_data.get("requestedBy", {})
                user = db.query(User).filter(
                    User.jellyseerr_id == requested_by.get("id")
                ).first()
                
                if not user:
                    logger.warning(f"User not found for request {jellyseerr_request_id}")
                    continue
                
                media = request_data.get("media", {})
                media_type = request_data.get("type", "").lower()
                
                # Get the actual title from the media object
                # Try different fields where title might be stored
                title = (media.get("title") or 
                        media.get("name") or
                        media.get("originalTitle") or
                        media.get("originalName") or
                        request_data.get("media", {}).get("tmdbId", "Unknown"))
                
                # If still no title, try to get it from the full media details
                if title == "Unknown" or str(title).isdigit():
                    try:
                        media_id = media.get("id")
                        if media_id:
                            full_media = await self._get(f"/media/{media_id}")
                            title = (full_media.get("title") or 
                                   full_media.get("name") or 
                                   f"TMDB ID {media.get('tmdbId')}")
                    except Exception as e:
                        logger.warning(f"Could not fetch full media details: {e}")
                        title = f"TMDB ID {media.get('tmdbId')}"
                
                # Map Jellyseerr status codes
                status_code = request_data.get("status", 1)
                status_map = {1: "pending", 2: "approved", 3: "declined", 4: "available"}
                status = status_map.get(status_code, "pending")
                
                if existing_request:
                    # Update existing request
                    existing_request.status = status
                    existing_request.title = title  # Update title in case it changed
                    logger.info(f"Updated request: {title} ({media_type})")
                    request_to_check = existing_request
                else:
                    # Create new request
                    season_count = None
                    if media_type == "tv" and "seasons" in request_data:
                        season_count = len(request_data["seasons"])
                    
                    new_request = MediaRequest(
                        user_id=user.id,
                        jellyseerr_request_id=jellyseerr_request_id,
                        media_type=media_type,
                        tmdb_id=media.get("tmdbId"),
                        title=title,
                        status=status,
                        season_count=season_count
                    )
                    db.add(new_request)
                    db.flush()  # Get the ID for the new request
                    logger.info(f"Created new request: {title} ({media_type})")
                    request_to_check = new_request
                
                # For TV shows, check Sonarr for existing episodes
                if media_type == "tv":
                    await self._import_existing_episodes(
                        db, 
                        request_to_check, 
                        media.get("tmdbId"),
                        sonarr
                    )
                
                synced_count += 1
            
            db.commit()
            logger.info(f"Synced {synced_count} requests from Jellyseerr")
        except Exception as e:
            db.rollback()
            logger.error(f"Error syncing requests: {e}")
        finally:
            db.close()
    
    async def _import_existing_episodes(self, db, request: MediaRequest, tmdb_id: int, sonarr):
        """Import existing episodes from Sonarr for a TV show request"""
        try:
            # Find the series in Sonarr by TMDB ID
            series = await sonarr.get_series_by_tmdb(tmdb_id)
            
            if not series:
                logger.info(f"Series with TMDB ID {tmdb_id} not found in Sonarr")
                return
            
            series_id = series.get("id")
            logger.info(f"Found series '{series.get('title')}' (ID: {series_id}) in Sonarr")
            
            # Get all episodes for this series
            episodes = await sonarr.get_episodes_by_series(series_id)
            
            if not episodes:
                logger.info(f"No episodes found for series ID {series_id}")
                return
            
            imported_count = 0
            for episode in episodes:
                # Only track episodes that have an episode file (downloaded)
                if not episode.get("hasFile"):
                    continue
                
                season_number = episode.get("seasonNumber")
                episode_number = episode.get("episodeNumber")
                
                # Check if we're already tracking this episode
                existing_tracking = db.query(EpisodeTracking).filter(
                    EpisodeTracking.series_id == series_id,
                    EpisodeTracking.season_number == season_number,
                    EpisodeTracking.episode_number == episode_number
                ).first()
                
                if existing_tracking:
                    continue  # Already tracked
                
                # Create episode tracking record
                from datetime import datetime
                
                air_date = None
                if episode.get("airDateUtc"):
                    try:
                        air_date = datetime.fromisoformat(episode.get("airDateUtc").replace('Z', '+00:00'))
                    except:
                        pass
                
                episode_tracking = EpisodeTracking(
                    request_id=request.id,
                    series_id=series_id,
                    season_number=season_number,
                    episode_number=episode_number,
                    episode_title=episode.get("title"),
                    air_date=air_date,
                    notified=True,  # Mark as already notified to prevent spam
                    available_in_plex=True
                )
                db.add(episode_tracking)
                imported_count += 1
            
            if imported_count > 0:
                logger.info(f"Imported {imported_count} existing episodes for '{series.get('title')}'")
            
        except Exception as e:
            logger.error(f"Error importing existing episodes for request {request.id}: {e}")
