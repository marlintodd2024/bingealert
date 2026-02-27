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
    
    async def _post(self, endpoint: str, data: dict) -> dict:
        """Make POST request to Sonarr API"""
        url = f"{self.base_url}/api/v3{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=data)
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
    
    async def get_queue(self) -> list:
        """Get current download/import queue from Sonarr"""
        try:
            queue_data = await self._get("/queue")
            return queue_data.get("records", [])
        except Exception as e:
            logger.error(f"Failed to fetch Sonarr queue: {e}")
            return []
    
    async def get_series_episodes_in_queue(self, series_id: int) -> list:
        """Get episodes for a specific series that are currently in the queue (downloading or importing)"""
        try:
            queue = await self.get_queue()
            series_queue = []
            
            for item in queue:
                # Check if this queue item is for our series
                if item.get("series", {}).get("id") == series_id:
                    # Only include items that are downloading or importing
                    status = item.get("status", "")
                    if status.lower() in ["downloading", "queued", "importPending"]:
                        episode = item.get("episode", {})
                        series_queue.append({
                            "season": episode.get("seasonNumber"),
                            "episode": episode.get("episodeNumber"),
                            "title": episode.get("title"),
                            "status": status
                        })
            
            logger.info(f"Found {len(series_queue)} episodes in queue for series {series_id}")
            return series_queue
            
        except Exception as e:
            logger.error(f"Failed to get queue for series {series_id}: {e}")
            return []
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
    
    async def get_episodes_by_series(self, series_id: int) -> Optional[list]:
        """Get all episodes for a series"""
        try:
            episodes = await self._get(f"/episode?seriesId={series_id}")
            return episodes
        except Exception as e:
            logger.error(f"Failed to fetch episodes for series {series_id}: {e}")
            return None
    
    async def get_calendar(self, start_date: str = None, end_date: str = None) -> Optional[list]:
        """Get calendar of upcoming episodes"""
        try:
            # Default to next 7 days if no dates provided
            from datetime import datetime, timedelta
            if not start_date:
                start_date = datetime.utcnow().strftime('%Y-%m-%d')
            if not end_date:
                end = datetime.utcnow() + timedelta(days=30)
                end_date = end.strftime('%Y-%m-%d')
            
            calendar = await self._get(f"/calendar?start={start_date}&end={end_date}")
            return calendar
        except Exception as e:
            logger.error(f"Failed to fetch Sonarr calendar: {e}")
            return None
    
    async def get_all_series(self) -> Optional[list]:
        """Get all series from Sonarr"""
        try:
            series_list = await self._get("/series")
            return series_list
        except Exception as e:
            logger.error(f"Failed to fetch all series from Sonarr: {e}")
            return None
    
    async def get_quality_profiles(self) -> list:
        """Get all quality profiles from Sonarr"""
        try:
            profiles = await self._get("/qualityProfile")
            return profiles
        except Exception as e:
            logger.error(f"Failed to fetch quality profiles from Sonarr: {e}")
            return []
    
    async def _delete(self, endpoint: str, params: dict = None) -> bool:
        """Make DELETE request to Sonarr API"""
        url = f"{self.base_url}/api/v3{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.delete(url, headers=self.headers, params=params)
            response.raise_for_status()
            return True
    
    async def blacklist_and_research_series(self, tmdb_id: int) -> dict:
        """Blacklist current episode files for a series and trigger a new search.
        Returns dict with 'success', 'message', and optionally 'details'."""
        try:
            # Step 1: Find the series in Sonarr by TMDB ID
            series = await self.get_series_by_tmdb(tmdb_id)
            if not series:
                return {"success": False, "message": f"Series with TMDB ID {tmdb_id} not found in Sonarr"}
            
            series_id = series.get("id")
            
            # Step 2: Get episode files for this series
            try:
                episode_files = await self._get(f"/episodefile?seriesId={series_id}")
            except Exception:
                episode_files = []
            
            blacklisted_count = 0
            if episode_files:
                # Blacklist each episode file
                for ef in episode_files:
                    try:
                        await self._delete(f"/episodefile/{ef['id']}", params={"addImportExclusion": "true"})
                        blacklisted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to blacklist episode file {ef['id']}: {e}")
            
            # Step 3: Trigger a series search
            logger.info(f"Triggering new search for series {series.get('title')}")
            await self._post("/command", {"name": "SeriesSearch", "seriesId": series_id})
            
            return {
                "success": True,
                "message": f"Blacklisted {blacklisted_count} file(s) and triggered re-search for {series.get('title')}",
                "details": {"series_id": series_id, "blacklisted_files": blacklisted_count}
            }
            
        except Exception as e:
            logger.error(f"Failed to blacklist and re-search series TMDB {tmdb_id}: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
