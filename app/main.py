"""BingeAlert v2 entry point.

What this file is:
    - The minimum FastAPI app to (a) run the first-run wizard, (b) gate the
      rest of the app behind auth once setup completes, (c) serve a placeholder
      root until Phase 4 ports the admin dashboard.

What this file is NOT (yet):
    - It does not include the v1 routers (admin, webhooks, sse, health) or
      the v1 background workers. Those still live under app/ and are dormant
      until Phase 4 ports them onto v2 settings + SQLite session scoping.

Middleware order (Starlette applies last-added-first, so this reads outside-in):
    SetupGateMiddleware  (added second below; runs first; redirects to /setup
                          when not configured, locks /setup once configured)
    AuthMiddleware       (added first below; runs after SetupGate; enforces
                          login on protected paths)
"""
from __future__ import annotations

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
from app.routers import auth as auth_router
from app.routers import setup as setup_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("bingealert")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("BingeAlert v2 starting (configured=%s)", settings.is_minimally_configured())
    yield
    logger.info("BingeAlert v2 shutting down")


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

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "configured": settings.is_minimally_configured()}


@app.get("/")
async def root(request: Request):
    """Placeholder root. Phase 4 replaces this with the admin dashboard."""
    if not settings.is_minimally_configured():
        # SetupGateMiddleware already redirects unconfigured browser hits to
        # /setup, so this path means the middleware let us through (e.g. an
        # API client). Mirror its behaviour.
        return JSONResponse(
            status_code=503,
            content={"detail": "Setup required. Visit /setup in a browser."},
        )
    # When configured, AuthMiddleware has already gated this. The user is
    # authenticated (or on the local network). Serve a stub until Phase 4.
    placeholder = _STATIC_DIR / "admin.html"
    if placeholder.is_file():
        return FileResponse(placeholder, media_type="text/html")
    return JSONResponse(
        content={
            "service": "BingeAlert",
            "version": "2.0.0-dev",
            "status": "configured",
            "note": "Admin dashboard is being ported in Phase 4.",
        }
    )


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
