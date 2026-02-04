import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TMDBService:
    """Service to fetch media info from TMDB (via Jellyseerr proxy or direct)"""
    
    TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
    
    def __init__(self, jellyseerr_url: str = None, jellyseerr_api_key: str = None):
        """Initialize with Jellyseerr credentials to use as TMDB proxy"""
        self.jellyseerr_url = jellyseerr_url.rstrip('/') if jellyseerr_url else None
        self.jellyseerr_api_key = jellyseerr_api_key
        self.use_jellyseerr = bool(jellyseerr_url and jellyseerr_api_key)
    
    async def get_tv_poster(self, tmdb_id: int) -> Optional[str]:
        """Get poster URL for a TV show"""
        try:
            if self.use_jellyseerr:
                # Use Jellyseerr as a proxy (already has TMDB data)
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.jellyseerr_url}/api/v1/tv/{tmdb_id}",
                        headers={"X-Api-Key": self.jellyseerr_api_key},
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        data = response.json()
                        poster_path = data.get("posterPath")
                        if poster_path:
                            poster_url = f"{self.TMDB_IMAGE_BASE}{poster_path}"
                            logger.info(f"Fetched TV poster for TMDB ID {tmdb_id}: {poster_url}")
                            return poster_url
                        else:
                            logger.warning(f"No posterPath in Jellyseerr response for TV TMDB ID {tmdb_id}")
                    else:
                        logger.warning(f"Failed to fetch TV show from Jellyseerr (TMDB ID {tmdb_id}): Status {response.status_code}")
            else:
                logger.warning("TMDB service not configured - Jellyseerr URL/API key missing")
            
            return None
        except Exception as e:
            logger.error(f"Failed to fetch TV poster for TMDB ID {tmdb_id}: {e}")
            return None
    
    async def get_movie_poster(self, tmdb_id: int) -> Optional[str]:
        """Get poster URL for a movie"""
        try:
            if self.use_jellyseerr:
                # Use Jellyseerr as a proxy (already has TMDB data)
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.jellyseerr_url}/api/v1/movie/{tmdb_id}",
                        headers={"X-Api-Key": self.jellyseerr_api_key},
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        data = response.json()
                        poster_path = data.get("posterPath")
                        if poster_path:
                            poster_url = f"{self.TMDB_IMAGE_BASE}{poster_path}"
                            logger.info(f"Fetched movie poster for TMDB ID {tmdb_id}: {poster_url}")
                            return poster_url
                        else:
                            logger.warning(f"No posterPath in Jellyseerr response for movie TMDB ID {tmdb_id}")
                    else:
                        logger.warning(f"Failed to fetch movie from Jellyseerr (TMDB ID {tmdb_id}): Status {response.status_code}")
            else:
                logger.warning("TMDB service not configured - Jellyseerr URL/API key missing")
            
            return None
        except Exception as e:
            logger.error(f"Failed to fetch movie poster for TMDB ID {tmdb_id}: {e}")
            return None
