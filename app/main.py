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
from typing import Any

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
_REPOSITORY = "marlintodd2024/bingealert"
_LATEST_RELEASE_API_URL = (
    f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
)
_REPOSITORY_URL = f"https://github.com/{_REPOSITORY}"
_LATEST_RELEASE_URL = f"{_REPOSITORY_URL}/releases/latest"
_SECURITY_ALERTS_URL = f"{_REPOSITORY_URL}/security/dependabot"
_DEPENDENCY_GRAPH_URL = f"{_REPOSITORY_URL}/network/dependencies"
_ACTIONS_SECURITY_URL = f"{_REPOSITORY_URL}/actions/workflows/dependency-audit.yml"
_DOCKER_IMAGE = "ghcr.io/marlintodd2024/bingealert"
_DOCKER_PACKAGE_URL = f"{_REPOSITORY_URL}/pkgs/container/bingealert"
_VERSION_CACHE_TTL_SECONDS = 6 * 60 * 60
_VERSION_ERROR_CACHE_TTL_SECONDS = 30 * 60
_version_cache: dict[str, object] = {"payload": None, "expires_at": 0.0}
_version_cache_lock = asyncio.Lock()
_SEMVER_RE = re.compile(
    r"^v?"
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def _normalize_version(value: str) -> str:
    return value.strip().lstrip("vV")


def _parse_version(value: str) -> tuple[int, int, int, list[str] | None]:
    normalized = _normalize_version(value)
    match = _SEMVER_RE.match(normalized)
    if match:
        prerelease = match.group("prerelease")
        return (
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            prerelease.split(".") if prerelease else None,
        )

    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", normalized)
    if not match:
        return (0, 0, 0, None)
    major, minor, patch = (int(part or 0) for part in match.groups())
    return (major, minor, patch, None)


def _compare_prerelease(left: list[str] | None, right: list[str] | None) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return 1
    if right is None:
        return -1

    for left_part, right_part in zip(left, right):
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            left_int = int(left_part)
            right_int = int(right_part)
            if left_int != right_int:
                return 1 if left_int > right_int else -1
        elif left_numeric != right_numeric:
            return -1 if left_numeric else 1
        elif left_part != right_part:
            return 1 if left_part > right_part else -1

    if len(left) == len(right):
        return 0
    return 1 if len(left) > len(right) else -1


def _compare_versions(left: str, right: str) -> int:
    left_major, left_minor, left_patch, left_pre = _parse_version(left)
    right_major, right_minor, right_patch, right_pre = _parse_version(right)
    left_core = (left_major, left_minor, left_patch)
    right_core = (right_major, right_minor, right_patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1
    return _compare_prerelease(left_pre, right_pre)


def _is_newer_version(candidate: str, current: str) -> bool:
    return _compare_versions(candidate, current) > 0


async def _fetch_latest_release() -> dict[str, Any]:
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
    return {
        "version": latest_version,
        "name": data.get("name") or f"BingeAlert v{latest_version}",
        "url": latest_url,
        "notes": data.get("body") or "",
        "published_at": data.get("published_at"),
        "prerelease": data.get("prerelease") is True,
    }


def _build_metadata() -> dict[str, str | None]:
    source_commit = (
        os.getenv("BINGEALERT_BUILD_SHA")
        or os.getenv("BUILD_SHA")
        or os.getenv("GITHUB_SHA")
        or ""
    ).strip()
    source_commit_url = (
        f"{_REPOSITORY_URL}/commit/{source_commit}" if source_commit else None
    )
    return {
        "source_commit": source_commit or None,
        "source_commit_short": source_commit[:7] if source_commit else None,
        "source_commit_url": source_commit_url,
        "build_tag": os.getenv("BINGEALERT_BUILD_TAG") or None,
    }


def _base_version_payload() -> dict:
    return {
        "version": __version__,
        "repository_url": _REPOSITORY_URL,
        "latest_version": None,
        "latest_name": None,
        "latest_url": _LATEST_RELEASE_URL,
        "latest_release_notes": "",
        "latest_published_at": None,
        "update_available": False,
        "checked_at": None,
        "check_error": None,
        "security": {
            "dependabot_url": _SECURITY_ALERTS_URL,
            "dependency_graph_url": _DEPENDENCY_GRAPH_URL,
            "audit_workflow_url": _ACTIONS_SECURITY_URL,
        },
        "docker": {
            "image": _DOCKER_IMAGE,
            "package_url": _DOCKER_PACKAGE_URL,
            "current_tag": __version__,
            "latest_tag": None,
            "pull_command": f"docker pull {_DOCKER_IMAGE}:{__version__}",
            "compose_command": "docker compose pull && docker compose up -d --force-recreate",
        },
        **_build_metadata(),
    }


async def _version_payload(force_refresh: bool = False) -> dict:
    now = time.monotonic()
    cached_payload = _version_cache.get("payload")
    if not force_refresh and cached_payload and now < float(_version_cache["expires_at"]):
        return cached_payload

    async with _version_cache_lock:
        now = time.monotonic()
        cached_payload = _version_cache.get("payload")
        if not force_refresh and cached_payload and now < float(_version_cache["expires_at"]):
            return cached_payload

        payload = _base_version_payload()
        cache_ttl = _VERSION_ERROR_CACHE_TTL_SECONDS
        try:
            release = await _fetch_latest_release()
            latest_version = release["version"]
            payload.update(
                {
                    "latest_version": latest_version,
                    "latest_name": release["name"],
                    "latest_url": release["url"],
                    "latest_release_notes": release["notes"],
                    "latest_published_at": release["published_at"],
                    "update_available": _is_newer_version(
                        latest_version, __version__
                    ),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            payload["docker"]["latest_tag"] = latest_version
            payload["docker"][
                "pull_command"
            ] = f"docker pull {_DOCKER_IMAGE}:{latest_version}"
            cache_ttl = _VERSION_CACHE_TTL_SECONDS
        except (httpx.HTTPError, ValueError) as e:
            logger.debug("latest release check failed: %s", e)
            payload["check_error"] = "Latest release check failed"

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
    from app.background.system_health import (
        record_worker_failure,
        record_worker_started,
        record_worker_success,
    )
    from datetime import timedelta

    email_service = EmailService()

    while True:
        started_at = None
        try:
            await asyncio.sleep(60)
            if is_maintenance_active():
                logger.debug("maintenance active -- skipping notification drain")
                continue
            started_at = record_worker_started(
                "notification_processor",
                "Notification processor",
                next_run_at=datetime.utcnow() + timedelta(seconds=60),
            )
            db = SessionLocal()
            try:
                await email_service.process_pending_notifications(db)
            finally:
                db.close()
            record_worker_success(
                "notification_processor",
                "Notification processor",
                started_at=started_at,
                next_run_at=datetime.utcnow() + timedelta(seconds=60),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"notification processor error: {e}")
            record_worker_failure(
                "notification_processor",
                "Notification processor",
                e,
                started_at=started_at,
                next_run_at=datetime.utcnow() + timedelta(seconds=60),
            )


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
        from app.background.ops_maintenance import ops_maintenance_worker
        from app.background.quality_monitor import quality_release_monitor_worker
        from app.background.reconciliation import reconciliation_worker
        from app.background.stuck_monitor import stuck_download_monitor
        from app.background.system_health import system_health_worker
        from app.background.weekly_summary import weekly_summary_worker

        starts = [
            ("notification processor", _notification_processor()),
            ("reconciliation worker (every 2h)", reconciliation_worker()),
            ("weekly summary (Sun 9am UTC)", weekly_summary_worker()),
            ("stuck download monitor (every 30m)", stuck_download_monitor()),
            ("quality/release monitor (daily)", quality_release_monitor_worker()),
            ("maintenance window worker (every 60s)", maintenance_window_worker()),
            ("system health worker", system_health_worker()),
            ("operational maintenance worker", ops_maintenance_worker()),
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
async def api_version(request: Request) -> dict:
    """Public version probe -- consumed by the admin/login footer."""
    force_refresh = request.query_params.get("refresh") in {"1", "true", "yes"}
    return await _version_payload(force_refresh=force_refresh)


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
