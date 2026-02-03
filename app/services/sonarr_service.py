import httpx
import logging
from typing import Optional, Dict

from app.config import settings

logger = logging.getLogger(__name__)


class SonarrService:
    def __init__(self):
        self.base_url = settings.sonarr_url.rstrip('/')
        self.api_key = settings.sonarr_api_key
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        }
    
    async def _get(self, endpoint: str) -> dict:
        """Make GET request to Sonarr API"""
        url = f"{self.base_url}/api/v3{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
    
    async def get_series(self, series_id: int) -> Optional[Dict]:
        """Get series details from Sonarr"""
        try:
            series = await self._get(f"/series/{series_id}")
            return series
        except Exception as e:
            logger.error(f"Failed to fetch series {series_id} from Sonarr: {e}")
            return None
    
    async def get_episode(self, episode_id: int) -> Optional[Dict]:
        """Get episode details from Sonarr"""
        try:
            episode = await self._get(f"/episode/{episode_id}")
            return episode
        except Exception as e:
            logger.error(f"Failed to fetch episode {episode_id} from Sonarr: {e}")
            return None
    
    async def get_series_by_tmdb(self, tmdb_id: int) -> Optional[Dict]:
        """Get series by TMDB ID"""
        try:
            all_series = await self._get("/series")
            for series in all_series:
                if series.get("tmdbId") == tmdb_id:
                    return series
            return None
        except Exception as e:
            logger.error(f"Failed to find series with TMDB ID {tmdb_id}: {e}")
            return None
