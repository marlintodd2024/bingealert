"""
Maintenance Window Background Worker
Handles automatic reminder emails and auto-completion of maintenance windows.

Checks every 60 seconds for:
1. Windows needing reminder emails (~60 minutes before start)
2. Windows that have started (update status to 'active')
3. Windows that have ended (auto-complete and send completion email)
"""

import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Configurable reminder threshold (minutes before start_time to send reminder)
REMINDER_THRESHOLD_MINUTES = 60


async def maintenance_window_worker():
    """Background worker that manages maintenance window lifecycle"""
    logger.info("Maintenance window worker started")
    
    while True:
        try:
            await asyncio.sleep(60)  # Check every 60 seconds
            await check_maintenance_windows()
        except asyncio.CancelledError:
            logger.info("Maintenance window worker cancelled")
            raise
        except Exception as e:
            logger.error(f"Maintenance window worker error: {e}")


async def check_maintenance_windows():
    """Check all active/scheduled maintenance windows and take appropriate actions"""
    from app.database import get_db, MaintenanceWindow
    from app.services.email_service import EmailService
    
    db = next(get_db())
    email_service = EmailService()
    
    try:
        now = datetime.utcnow()
        
        # Get all non-cancelled, non-completed windows
        windows = db.query(MaintenanceWindow).filter(
            MaintenanceWindow.cancelled == False,
            MaintenanceWindow.status.in_(["scheduled", "active"])
        ).all()
        
        for window in windows:
            try:
                # 1. Send reminder if within threshold and not yet sent
                if (not window.reminder_sent 
                    and window.status == "scheduled"
                    and window.start_time > now
                    and (window.start_time - now) <= timedelta(minutes=REMINDER_THRESHOLD_MINUTES)):
                    
                    logger.info(f"Sending maintenance reminder for '{window.title}' (starts in {int((window.start_time - now).total_seconds() / 60)} minutes)")
                    result = await email_service.send_maintenance_email_to_all_users(db, "reminder", window)
                    window.reminder_sent = True
                    db.commit()
                    logger.info(f"Maintenance reminder sent: {result}")
                
                # 2. Update status to 'active' when start_time is reached
                if window.status == "scheduled" and now >= window.start_time:
                    logger.info(f"Maintenance window '{window.title}' is now active")
                    window.status = "active"
                    db.commit()
                
                # 3. Auto-complete when end_time is reached
                if (window.status == "active" 
                    and not window.completion_sent 
                    and now >= window.end_time):
                    
                    logger.info(f"Auto-completing maintenance window '{window.title}' (end time reached)")
                    result = await email_service.send_maintenance_email_to_all_users(db, "complete", window)
                    window.completion_sent = True
                    window.status = "completed"
                    window.updated_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Maintenance completion email sent: {result}")
                    
            except Exception as e:
                logger.error(f"Error processing maintenance window '{window.title}': {e}")
                db.rollback()
                
    except Exception as e:
        logger.error(f"Error checking maintenance windows: {e}")
    finally:
        db.close()
