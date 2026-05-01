"""
Plex service for checking if media exists in Plex library
"""
import httpx
import logging
from app.config import settings

logger = logging.getLogger(__name__)


class PlexService:
    """Service for interacting with Plex Media Server"""
    
    def __init__(self):
        self.base_url = settings.plex_url.rstrip('/')
        self.token = settings.plex_token
        
    async def _get(self, endpoint: str):
        """Make GET request to Plex API"""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "X-Plex-Token": self.token,
            "Accept": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    
    async def check_episode_in_plex(self, series_title: str, season: int, episode: int) -> bool:
        """Check if a specific episode exists in Plex library"""
        try:
            # Search for the series
            search_results = await self._get(f"/search?query={series_title}&type=2")  # type=2 is TV shows
            
            if not search_results or 'MediaContainer' not in search_results:
                return False
            
            metadata = search_results['MediaContainer'].get('Metadata', [])
            if not metadata:
                return False
            
            # Get the first matching series
            series = metadata[0]
            series_key = series.get('ratingKey')
            
            if not series_key:
                return False
            
            # Get all episodes for this series
            episodes = await self._get(f"/library/metadata/{series_key}/allLeaves")
            
            if not episodes or 'MediaContainer' not in episodes:
                return False
            
            episode_metadata = episodes['MediaContainer'].get('Metadata', [])
            
            # Check if our specific episode exists
            for ep in episode_metadata:
                if ep.get('parentIndex') == season and ep.get('index') == episode:
                    # Check if it has a media file
                    if ep.get('Media'):
                        logger.info(f"Found episode in Plex: {series_title} S{season:02d}E{episode:02d}")
                        return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Failed to check Plex for {series_title} S{season:02d}E{episode:02d}: {e}")
            return False
    
    async def check_movie_in_plex(self, movie_title: str, year: int = None) -> bool:
        """Check if a specific movie exists in Plex library"""
        try:
            # Search for the movie
            search_query = f"{movie_title} {year}" if year else movie_title
            search_results = await self._get(f"/search?query={search_query}&type=1")  # type=1 is movies
            
            if not search_results or 'MediaContainer' not in search_results:
                return False
            
            metadata = search_results['MediaContainer'].get('Metadata', [])
            
            for movie in metadata:
                # Match by title and optionally year
                if movie.get('title', '').lower() == movie_title.lower():
                    if year is None or movie.get('year') == year:
                        # Check if it has a media file
                        if movie.get('Media'):
                            logger.info(f"Found movie in Plex: {movie_title} ({year})")
                            return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Failed to check Plex for {movie_title} ({year}): {e}")
            return False
