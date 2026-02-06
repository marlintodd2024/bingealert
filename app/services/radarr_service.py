import httpx
import logging
from typing import Optional, Dict

from app.config import settings

logger = logging.getLogger(__name__)


class RadarrService:
    def __init__(self):
        self.base_url = settings.radarr_url.rstrip('/')
        self.api_key = settings.radarr_api_key
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        }
    
    async def _get(self, endpoint: str) -> dict:
        """Make GET request to Radarr API"""
        url = f"{self.base_url}/api/v3{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
    
    async def get_movie(self, movie_id: int) -> Optional[Dict]:
        """Get movie details from Radarr"""
        try:
            movie = await self._get(f"/movie/{movie_id}")
            return movie
        except Exception as e:
            logger.error(f"Failed to fetch movie {movie_id} from Radarr: {e}")
            return None
    
    async def get_movie_by_tmdb(self, tmdb_id: int) -> Optional[Dict]:
        """Get movie by TMDB ID"""
        try:
            all_movies = await self._get("/movie")
            for movie in all_movies:
                if movie.get("tmdbId") == tmdb_id:
                    return movie
            return None
        except Exception as e:
            logger.error(f"Failed to find movie with TMDB ID {tmdb_id}: {e}")
            return None
