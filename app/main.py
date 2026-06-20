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
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.auth import AuthMiddleware
from app.config import settings
from app.middleware import SetupGateMiddleware
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import calendar as calendar_router
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
_LATEST_RELEASE_API_URL = (
    "https://api.github.com/repos/marlintodd2024/bingealert/releases/latest"
)
_LATEST_RELEASE_URL = "https://github.com/marlintodd2024/bingealert/releases/latest"
_VERSION_CACHE_TTL_SECONDS = 6 * 60 * 60
_VERSION_ERROR_CACHE_TTL_SECONDS = 30 * 60
_version_cache: dict[str, object] = {"payload": None, "expires_at": 0.0}
_version_cache_lock = asyncio.Lock()


def _normalize_version(value: str) -> str:
    return value.strip().lstrip("vV")


def _version_key(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", _normalize_version(value))
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


def _is_newer_version(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


async def _fetch_latest_release() -> tuple[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"BingeAlert/{__version__}",
    }
    async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
        response = await client.get(_LATEST_RELEASE_API_URL)
    response.raise_for_status()
    data = response.json()
    latest_version = _normalize_version(data.get("tag_name") or data.get("name") or "")
    if not latest_version:
        raise ValueError("GitHub latest release response did not include a version")
    latest_url = data.get("html_url") or _LATEST_RELEASE_URL
    return latest_version, latest_url


def _base_version_payload() -> dict:
    return {
        "version": __version__,
        "latest_version": None,
        "latest_url": _LATEST_RELEASE_URL,
        "update_available": False,
        "checked_at": None,
    }


async def _version_payload() -> dict:
    now = time.monotonic()
    cached_payload = _version_cache.get("payload")
    if cached_payload and now < float(_version_cache["expires_at"]):
        return cached_payload

    async with _version_cache_lock:
        now = time.monotonic()
        cached_payload = _version_cache.get("payload")
        if cached_payload and now < float(_version_cache["expires_at"]):
            return cached_payload

        payload = _base_version_payload()
        cache_ttl = _VERSION_ERROR_CACHE_TTL_SECONDS
        try:
            latest_version, latest_url = await _fetch_latest_release()
            payload.update(
                {
                    "latest_version": latest_version,
                    "latest_url": latest_url,
                    "update_available": _is_newer_version(
                        latest_version, __version__
                    ),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            cache_ttl = _VERSION_CACHE_TTL_SECONDS
        except (httpx.HTTPError, ValueError) as e:
            logger.debug("latest release check failed: %s", e)

        _version_cache["payload"] = payload
        _version_cache["expires_at"] = time.monotonic() + cache_ttl
        return payload


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
    version=__version__,
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
app.include_router(calendar_router.router, tags=["Calendar"])  # public per-user .ics feed
app.include_router(sse_router.router, tags=["SSE"])  # already prefixes /sse internally

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/api/version")
async def api_version() -> dict:
    """Public version probe -- consumed by the admin/login footer."""
    return await _version_payload()


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
