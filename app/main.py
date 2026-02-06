from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import os
import asyncio

from app.config import settings
from app.database import engine, Base
from app.routers import webhooks, admin, health
from app.services.jellyseerr_sync import JellyseerrSyncService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def periodic_sync():
    """Background task to sync with Jellyseerr once daily as backup"""
    while True:
        try:
            await asyncio.sleep(86400)  # 86400 seconds = 24 hours
            logger.info("Starting daily backup sync with Jellyseerr...")
            sync_service = JellyseerrSyncService()
            await sync_service.sync_users()
            await sync_service.sync_requests()
            logger.info("Daily backup sync completed")
        except Exception as e:
            logger.error(f"Daily backup sync failed: {e}")


async def process_notifications_periodically():
    """Background task to process pending notifications every minute"""
    from app.services.email_service import EmailService
    from app.database import get_db
    
    email_service = EmailService()
    
    while True:
        try:
            await asyncio.sleep(60)  # Check every 60 seconds
            logger.debug("Checking for pending notifications...")
            
            db = next(get_db())
            try:
                await email_service.process_pending_notifications(db)
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Notification processing failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("Starting Plex Notification Portal...")
    
    # Create database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")
    
    # Initial sync with Jellyseerr - DISABLED (using webhooks instead)
    # Uncomment below if you want to sync existing requests on startup
    # try:
    #     sync_service = JellyseerrSyncService()
    #     await sync_service.sync_users()
    #     await sync_service.sync_requests()
    #     logger.info("Initial Jellyseerr sync completed")
    # except Exception as e:
    #     logger.error(f"Failed to sync with Jellyseerr: {e}")
    
    # Daily backup sync - DISABLED (webhooks handle everything)
    # sync_task = asyncio.create_task(periodic_sync())
    # logger.info("Started daily backup sync task (runs every 24 hours)")
    
    # Start notification processor (checks every minute for delayed notifications)
    notification_task = asyncio.create_task(process_notifications_periodically())
    logger.info("Started notification processor (checks every 60 seconds)")
    
    logger.info("Using Jellyseerr webhooks for real-time request tracking")
    
    yield
    
    # Cancel background tasks on shutdown
    notification_task.cancel()
    try:
        await notification_task
    except asyncio.CancelledError:
        logger.info("Notification processor cancelled")
    
    # Cancel background task on shutdown (if enabled)
    # sync_task.cancel()
    # try:
    #     await sync_task
    # except asyncio.CancelledError:
    #     logger.info("Periodic sync task cancelled")
    
    logger.info("Shutting down Plex Notification Portal...")


app = FastAPI(
    title="Plex Notification Portal",
    description="Notification service for Sonarr/Radarr content available in Plex",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


@app.get("/")
async def root():
    """Serve the admin dashboard at root"""
    static_file = os.path.join(os.path.dirname(__file__), "static", "admin.html")
    if os.path.exists(static_file):
        return FileResponse(static_file)
    return {"error": "Dashboard not found"}


@app.get("/api-info")
async def api_info():
    return {
        "message": "Plex Notification Portal API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "dashboard": "/"
    }


@app.get("/dashboard")
async def dashboard_redirect():
    """Redirect /dashboard to root for backwards compatibility"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=301)
