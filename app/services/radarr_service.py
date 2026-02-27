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
    
    async def get_movies(self) -> list:
        """Get all movies from Radarr"""
        try:
            movies = await self._get("/movie")
            return movies
        except Exception as e:
            logger.error(f"Failed to fetch all movies from Radarr: {e}")
            return []
    
    async def get_quality_profiles(self) -> list:
        """Get all quality profiles from Radarr"""
        try:
            profiles = await self._get("/qualityProfile")
            return profiles
        except Exception as e:
            logger.error(f"Failed to fetch quality profiles from Radarr: {e}")
            return []
    
    async def _delete(self, endpoint: str, params: dict = None) -> bool:
        """Make DELETE request to Radarr API"""
        url = f"{self.base_url}/api/v3{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.delete(url, headers=self.headers, params=params)
            response.raise_for_status()
            return True
    
    async def _post(self, endpoint: str, data: dict) -> dict:
        """Make POST request to Radarr API"""
        url = f"{self.base_url}/api/v3{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
    
    async def blacklist_and_research_movie(self, tmdb_id: int) -> dict:
        """Blacklist current movie file and trigger a new search.
        Returns dict with 'success', 'message', and optionally 'details'."""
        try:
            # Step 1: Find the movie in Radarr by TMDB ID
            movie = await self.get_movie_by_tmdb(tmdb_id)
            if not movie:
                return {"success": False, "message": f"Movie with TMDB ID {tmdb_id} not found in Radarr"}
            
            movie_id = movie.get("id")
            movie_file_id = movie.get("movieFileId") or movie.get("movieFile", {}).get("id")
            
            if not movie_file_id and movie.get("hasFile"):
                # Try to get movie file from movie details
                movie_file = movie.get("movieFile")
                if movie_file:
                    movie_file_id = movie_file.get("id")
            
            if not movie_file_id:
                # No file to blacklist, just trigger search
                logger.info(f"No movie file found for {movie.get('title')}, triggering search directly")
                await self._post("/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
                return {"success": True, "message": f"No existing file to blacklist. Triggered new search for {movie.get('title')}"}
            
            # Step 2: Delete the movie file with blacklist
            logger.info(f"Blacklisting movie file {movie_file_id} for {movie.get('title')}")
            await self._delete(f"/moviefile/{movie_file_id}", params={"addImportExclusion": "true"})
            
            # Step 3: Trigger a new search
            logger.info(f"Triggering new search for {movie.get('title')}")
            await self._post("/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            
            return {
                "success": True,
                "message": f"Blacklisted current file and triggered re-search for {movie.get('title')}",
                "details": {"movie_id": movie_id, "blacklisted_file_id": movie_file_id}
            }
            
        except Exception as e:
            logger.error(f"Failed to blacklist and re-search movie TMDB {tmdb_id}: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
