from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import os
import asyncio

from app.config import settings
from app.database import engine, Base
from app.routers import webhooks, admin, health, sse
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
    
    # Start reconciliation worker (checks every 2 hours for missed webhooks)
    try:
        from app.background.reconciliation import reconciliation_worker
        reconciliation_task = asyncio.create_task(reconciliation_worker())
        logger.info("Started reconciliation worker (checks every 2 hours)")
    except Exception as e:
        logger.warning(f"Failed to start reconciliation worker: {e}")
        reconciliation_task = None
    
    # Start weekly summary worker (sends summary every Sunday at 9 AM UTC)
    try:
        from app.background.weekly_summary import weekly_summary_worker
        weekly_summary_task = asyncio.create_task(weekly_summary_worker())
        logger.info("Started weekly summary worker (runs Sundays at 9 AM UTC)")
    except Exception as e:
        logger.warning(f"Failed to start weekly summary worker: {e}")
        weekly_summary_task = None
    
    # Start stuck download monitor (checks every 30 minutes)
    try:
        from app.background.stuck_monitor import stuck_download_monitor
        stuck_monitor_task = asyncio.create_task(stuck_download_monitor())
        logger.info("Started stuck download monitor (checks every 30 minutes)")
    except Exception as e:
        logger.warning(f"Failed to start stuck download monitor: {e}")
        stuck_monitor_task = None
    
    # Start quality/release monitor (checks daily)
    try:
        from app.background.quality_monitor import quality_release_monitor_worker
        quality_monitor_task = asyncio.create_task(quality_release_monitor_worker())
        logger.info("Started quality/release monitor (checks daily for unreleased content and quality profiles)")
    except Exception as e:
        logger.warning(f"Failed to start quality/release monitor: {e}")
        quality_monitor_task = None
    
    logger.info("Using Jellyseerr webhooks for real-time request tracking")
    
    yield
    
    # Cancel background tasks on shutdown
    notification_task.cancel()
    if reconciliation_task:
        reconciliation_task.cancel()
    if weekly_summary_task:
        weekly_summary_task.cancel()
    if stuck_monitor_task:
        stuck_monitor_task.cancel()
    if quality_monitor_task:
        quality_monitor_task.cancel()
    try:
        await notification_task
    except asyncio.CancelledError:
        logger.info("Notification processor cancelled")
    if reconciliation_task:
        try:
            await reconciliation_task
        except asyncio.CancelledError:
            logger.info("Reconciliation worker cancelled")
    if weekly_summary_task:
        try:
            await weekly_summary_task
        except asyncio.CancelledError:
            logger.info("Weekly summary worker cancelled")
    if stuck_monitor_task:
        try:
            await stuck_monitor_task
        except asyncio.CancelledError:
            logger.info("Stuck download monitor cancelled")
    
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

# Add authentication middleware
from app.auth import AuthMiddleware
app.add_middleware(AuthMiddleware)

# Include routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(sse.router, tags=["SSE"])  # Real-time updates

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


# ──────────────────────────────────────
# Auth Routes
# ──────────────────────────────────────

@app.get("/login")
@app.get("/login.html")
async def login_page():
    """Serve the login page"""
    static_file = os.path.join(os.path.dirname(__file__), "static", "login.html")
    if os.path.exists(static_file):
        return FileResponse(static_file)
    return JSONResponse(status_code=404, content={"detail": "Login page not found"})


@app.get("/auth/check")
async def auth_check(request: Request):
    """Check current auth status and return turnstile config for login page"""
    from app.auth import get_auth_settings, get_client_ip, verify_session_token, is_local_network
    from app.database import get_db
    
    client_ip = get_client_ip(request)
    
    try:
        db = next(get_db())
        try:
            settings_dict = get_auth_settings(db)
        finally:
            db.close()
    except Exception:
        return {"authenticated": False, "auth_enabled": False, "client_ip": client_ip}
    
    auth_enabled = settings_dict.get('auth_enabled', 'false').lower() == 'true'
    
    if not auth_enabled:
        return {"authenticated": True, "auth_enabled": False, "client_ip": client_ip}
    
    # Check local network
    local_cidr = settings_dict.get('local_network_cidr', '')
    if local_cidr and is_local_network(client_ip, local_cidr):
        return {"authenticated": True, "auth_enabled": True, "local_network": True, "client_ip": client_ip}
    
    # Check session
    session_token = request.cookies.get('pnp_session')
    secret_key = os.getenv('APP_SECRET_KEY', 'default-secret')
    timeout_hours = int(settings_dict.get('session_timeout_hours', '24'))
    
    authenticated = bool(session_token and verify_session_token(session_token, secret_key, timeout_hours))
    
    result = {
        "authenticated": authenticated,
        "auth_enabled": True,
        "client_ip": client_ip,
        "turnstile_enabled": settings_dict.get('turnstile_enabled', 'false').lower() == 'true',
        "turnstile_site_key": settings_dict.get('turnstile_site_key', '') if settings_dict.get('turnstile_enabled', 'false').lower() == 'true' else ''
    }
    
    return result


@app.post("/auth/login")
async def auth_login(request: Request):
    """Handle login attempt"""
    from app.auth import (
        get_auth_settings, verify_password, create_session_token,
        verify_turnstile, get_client_ip
    )
    from app.database import get_db
    
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid request"})
    
    password = body.get('password', '')
    turnstile_token = body.get('turnstile_token', '')
    client_ip = get_client_ip(request)
    
    db = next(get_db())
    try:
        settings_dict = get_auth_settings(db)
    finally:
        db.close()
    
    # Verify Turnstile if enabled
    turnstile_enabled = settings_dict.get('turnstile_enabled', 'false').lower() == 'true'
    turnstile_secret = settings_dict.get('turnstile_secret_key', '')
    
    if turnstile_enabled and turnstile_secret:
        if not turnstile_token:
            return JSONResponse(status_code=400, content={"detail": "Verification challenge required"})
        
        valid = await verify_turnstile(turnstile_token, turnstile_secret, client_ip)
        if not valid:
            logger.warning(f"Turnstile verification failed from {client_ip}")
            return JSONResponse(status_code=403, content={"detail": "Verification failed. Please try again."})
    
    # Verify password
    password_hash = settings_dict.get('auth_password_hash', '')
    
    if not password_hash:
        return JSONResponse(status_code=500, content={"detail": "No admin password configured"})
    
    if not verify_password(password, password_hash):
        logger.warning(f"Failed login attempt from {client_ip}")
        return JSONResponse(status_code=401, content={"detail": "Invalid password"})
    
    # Create session
    secret_key = os.getenv('APP_SECRET_KEY', 'default-secret')
    token = create_session_token(secret_key)
    
    logger.info(f"Successful login from {client_ip}")
    
    response = JSONResponse(content={"success": True, "message": "Login successful"})
    
    timeout_hours = int(settings_dict.get('session_timeout_hours', '24'))
    response.set_cookie(
        key="pnp_session",
        value=token,
        max_age=timeout_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=False  # Set to True if using HTTPS
    )
    
    return response


@app.post("/auth/logout")
async def auth_logout():
    """Clear session cookie"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("pnp_session")
    return response


@app.get("/")
async def root():
    """Serve the admin dashboard at root, or redirect to setup if needed"""
    # Check if setup is complete in database
    try:
        from app.database import get_db, SystemConfig
        db = next(get_db())
        try:
            config = db.query(SystemConfig).filter(SystemConfig.key == "setup_complete").first()
            setup_complete = config and config.value == "true"
        finally:
            db.close()
        
        if not setup_complete:
            # Redirect to setup wizard
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/setup.html", status_code=302)
    except Exception as e:
        logger.error(f"Error checking setup status: {e}")
        # On error, allow access to dashboard
    
    static_file = os.path.join(os.path.dirname(__file__), "static", "admin.html")
    if os.path.exists(static_file):
        return FileResponse(static_file)
    return {"error": "Dashboard not found"}


@app.get("/setup")
async def setup_redirect():
    """Redirect /setup to /setup.html"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/setup.html", status_code=301)


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
