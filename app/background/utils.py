"""
Shared utilities for background workers.
"""

import logging

logger = logging.getLogger(__name__)


def is_maintenance_active() -> bool:
    """Check if there is an active maintenance window. 
    Background workers should skip their cycle when maintenance is active
    to avoid noisy errors from unavailable services.
    
    Returns True if a maintenance window is currently active, False otherwise.
    """
    try:
        from app.database import get_db, MaintenanceWindow
        db = next(get_db())
        try:
            window = db.query(MaintenanceWindow).filter(
                MaintenanceWindow.status == "active",
                MaintenanceWindow.cancelled == False
            ).first()
            if window:
                logger.debug(f"Maintenance window active: '{window.title}' — skipping worker cycle")
                return True
            return False
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"Could not check maintenance status: {e}")
        return False  # If we can't check, assume no maintenance (don't block workers)
