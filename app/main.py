from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import os

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("Starting Plex Notification Portal...")
    
    # Create database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")
    
    # Initial sync with Jellyseerr
    try:
        sync_service = JellyseerrSyncService()
        await sync_service.sync_users()
        await sync_service.sync_requests()
        logger.info("Initial Jellyseerr sync completed")
    except Exception as e:
        logger.error(f"Failed to sync with Jellyseerr: {e}")
    
    yield
    
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
    return {
        "message": "Plex Notification Portal API",
        "version": "1.0.0",
        "docs": "/docs",
        "admin_dashboard": "/dashboard"
    }


@app.get("/dashboard")
async def dashboard():
    """Serve the admin dashboard"""
    static_file = os.path.join(os.path.dirname(__file__), "static", "admin.html")
    if os.path.exists(static_file):
        return FileResponse(static_file)
    return {"error": "Dashboard not found"}
