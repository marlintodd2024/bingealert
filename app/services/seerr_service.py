import httpx
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class SeerrService:
    """Service for interacting with the Seerr API (issue management)"""
    
    def __init__(self):
        self.base_url = settings.jellyseerr_url.rstrip('/')
        self.api_key = settings.jellyseerr_api_key
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        }
    
    async def resolve_issue(self, seerr_issue_id: int) -> dict:
        """Mark an issue as resolved in Seerr via API"""
        try:
            url = f"{self.base_url}/api/v1/issue/{seerr_issue_id}/resolved"
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=self.headers)
                response.raise_for_status()
                logger.info(f"Resolved issue #{seerr_issue_id} in Seerr")
                return {"success": True, "message": f"Issue #{seerr_issue_id} resolved in Seerr"}
        except Exception as e:
            logger.error(f"Failed to resolve issue #{seerr_issue_id} in Seerr: {e}")
            return {"success": False, "message": f"Failed to resolve in Seerr: {str(e)}"}
