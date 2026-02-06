from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.database import get_db
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def health_check(db: Session = Depends(get_db)):
    """Basic health check endpoint"""
    try:
        # Test database connection
        db.execute(text("SELECT 1"))
        
        return {
            "status": "healthy",
            "database": "connected",
            "services": {
                "jellyseerr_configured": bool(settings.jellyseerr_url and settings.jellyseerr_api_key),
                "sonarr_configured": bool(settings.sonarr_url and settings.sonarr_api_key),
                "radarr_configured": bool(settings.radarr_url and settings.radarr_api_key),
                "smtp_configured": bool(settings.smtp_host),
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }
