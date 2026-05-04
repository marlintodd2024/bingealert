"""BingeAlert v2 entry point.

Phase 4 in progress: webhooks, health, and the six background workers are
wired up. Admin endpoints and SSE log streaming come in 4d.

Middleware order (Starlette applies last-added-first, so this reads outside-in):
    SetupGateMiddleware  (added second below; runs first; redirects to /setup
                          when not configured, locks /setup once configured)
    AuthMiddleware       (added first below; runs after SetupGate; enforces
                          login on protected paths)
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import AuthMiddleware
from app.config import settings
from app.middleware import SetupGateMiddleware
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import health as health_router
from app.routers import setup as setup_router
from app.routers import sse as sse_router
from app.routers import webhooks as webhooks_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("bingealert")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# Notification processor -- defined here (was inline in v1 main.py).
# ---------------------------------------------------------------------------


async def _notification_processor() -> None:
    """Drain queued notifications every minute, skipping during maintenance."""
    from app.background.utils import is_maintenance_active
    from app.database import SessionLocal
    from app.services.email_service import EmailService

    email_service = EmailService()

    while True:
        try:
            await asyncio.sleep(60)
            if is_maintenance_active():
                logger.debug("maintenance active -- skipping notification drain")
                continue
            db = SessionLocal()
            try:
                await email_service.process_pending_notifications(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"notification processor error: {e}")


# ---------------------------------------------------------------------------
# Lifespan: only start workers once we're actually configured. Pre-setup the
# task list is empty so /setup is uncontested.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "BingeAlert v2 starting (configured=%s)", settings.is_minimally_configured()
    )

    tasks: list[asyncio.Task] = []
    if settings.is_minimally_configured():
        from app.background.maintenance_worker import maintenance_window_worker
        from app.background.quality_monitor import quality_release_monitor_worker
        from app.background.reconciliation import reconciliation_worker
        from app.background.stuck_monitor import stuck_download_monitor
        from app.background.weekly_summary import weekly_summary_worker

        starts = [
            ("notification processor", _notification_processor()),
            ("reconciliation worker (every 2h)", reconciliation_worker()),
            ("weekly summary (Sun 9am UTC)", weekly_summary_worker()),
            ("stuck download monitor (every 30m)", stuck_download_monitor()),
            ("quality/release monitor (daily)", quality_release_monitor_worker()),
            ("maintenance window worker (every 60s)", maintenance_window_worker()),
        ]
        for label, coro in starts:
            try:
                t = asyncio.create_task(coro)
                tasks.append(t)
                logger.info("started: %s", label)
            except Exception as e:
                logger.warning("failed to start %s: %s", label, e)
    else:
        logger.info("setup mode -- background workers will start after first-run wizard")

    yield

    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    logger.info("BingeAlert v2 shut down")


_is_dev = os.getenv("ENVIRONMENT", "production").lower() != "production"

app = FastAPI(
    title="BingeAlert",
    version="2.0.0-dev",
    lifespan=lifespan,
    docs_url="/docs" if _is_dev else None,
    redoc_url="/redoc" if _is_dev else None,
    openapi_url="/openapi.json" if _is_dev else None,
)

# Order matters -- last-added is outermost. SetupGate must run before Auth so
# unconfigured installs reach the wizard without being intercepted by login.
app.add_middleware(AuthMiddleware)
app.add_middleware(SetupGateMiddleware)

app.include_router(setup_router.router, tags=["Setup"])
app.include_router(auth_router.router, tags=["Auth"])
app.include_router(health_router.router, prefix="/health", tags=["Health"])
app.include_router(webhooks_router.router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(admin_router.router, prefix="/admin", tags=["Admin"])
app.include_router(sse_router.router, tags=["SSE"])  # already prefixes /sse internally

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/service-worker.js")
async def service_worker():
    sw = _STATIC_DIR / "service-worker.js"
    if sw.is_file():
        return FileResponse(sw, media_type="application/javascript")
    return JSONResponse(status_code=404, content={"detail": "service-worker.js missing"})


@app.get("/")
async def root(request: Request):
    """Placeholder root. Phase 4d replaces this with the admin dashboard."""
    if not settings.is_minimally_configured():
        return JSONResponse(
            status_code=503,
            content={"detail": "Setup required. Visit /setup in a browser."},
        )
    placeholder = _STATIC_DIR / "admin.html"
    if placeholder.is_file():
        return FileResponse(placeholder, media_type="text/html")
    return JSONResponse(
        content={
            "service": "BingeAlert",
            "version": "2.0.0-dev",
            "status": "configured",
            "note": "Admin dashboard is being ported in Phase 4d.",
        }
    )


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
