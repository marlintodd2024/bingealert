"""Setup wizard backend.

Flow:
    1. Browser GETs /setup -> we serve app/static/setup.html.
    2. User fills the form; the page POSTs JSON to /api/setup.
    3. We validate, hash the admin password, generate a secret key if missing,
       write everything to /data/config.json, and respond {success:true}.
    4. A BackgroundTask exits the process ~2 seconds later. Docker's restart
       policy starts a fresh container which boots with the new config.json,
       at which point SetupGateMiddleware lets traffic through.
    5. The browser polls /api/setup/status until restart completes, then sends
       the user to /login (or / if auth is disabled).

Once setup is complete, SetupGateMiddleware locks /setup and /api/setup with
a 403 -- the wizard cannot be re-run without manually deleting config.json.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.auth import hash_password
from app.config import settings


logger = logging.getLogger(__name__)
router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ---------------------------------------------------------------------------
# Wizard payload
# ---------------------------------------------------------------------------


class WizardPayload(BaseModel):
    # SMTP
    smtp_host: str
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: str
    admin_email: Optional[str] = None

    # Seerr (Jellyseerr / Overseerr)
    jellyseerr_url: str
    jellyseerr_api_key: str

    # Sonarr
    sonarr_url: str
    sonarr_api_key: str
    sonarr_anime_url: Optional[str] = None
    sonarr_anime_api_key: Optional[str] = None

    # Radarr
    radarr_url: str
    radarr_api_key: str

    # Plex (optional, used for availability checks)
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None

    # Auth
    auth_required: bool = True
    admin_password: Optional[str] = Field(
        default=None,
        description="Plain text from the form; hashed before saving. Required when auth_required.",
    )
    local_network_cidrs: str = "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8"
    turnstile_site_key: Optional[str] = None
    turnstile_secret_key: Optional[str] = None

    # Application
    app_secret_key: Optional[str] = Field(
        default=None,
        description="HMAC key for session cookies. Auto-generated if blank.",
    )
    environment: str = "production"
    webhook_allowed_ips: str = ""


# ---------------------------------------------------------------------------
# Restart helper
# ---------------------------------------------------------------------------


async def _restart_process_after_delay() -> None:
    """Wait long enough for the response to flush, then exit.

    Container restart policy will bring us back with the new /data/config.json
    overlaid on env+defaults, at which point is_minimally_configured() flips
    to True and SetupGateMiddleware unlocks the rest of the app.
    """
    await asyncio.sleep(2)
    logger.info("setup wizard saved -- exiting for container restart")
    os._exit(0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/setup")
async def setup_page() -> FileResponse:
    """Serve the wizard HTML."""
    page = _STATIC_DIR / "setup.html"
    if not page.is_file():
        raise HTTPException(status_code=500, detail="setup.html missing from build")
    return FileResponse(page, media_type="text/html")


@router.post("/api/setup")
async def save_setup(
    payload: WizardPayload, background: BackgroundTasks
) -> JSONResponse:
    """Validate the wizard payload, write config.json, schedule a restart."""
    if payload.auth_required and not payload.admin_password:
        raise HTTPException(
            status_code=400,
            detail="Admin password is required when auth_required is true.",
        )

    # If SMTP authentication is being used, both halves must be present.
    # Browsers occasionally blank password fields on form navigation; without
    # this check the wizard succeeds and the user finds out hours later when
    # the SMTP relay rejects with "relay access denied".
    if payload.smtp_user and not payload.smtp_password:
        raise HTTPException(
            status_code=400,
            detail=(
                "SMTP password is required when smtp_user is set. Re-enter it "
                "and resubmit (browsers sometimes clear password fields on "
                "form navigation)."
            ),
        )

    # Translate the wire payload into the on-disk config shape.
    config: dict = payload.model_dump(exclude={"admin_password"}, exclude_none=False)

    if payload.admin_password:
        config["admin_password_hash"] = hash_password(payload.admin_password)

    if not config.get("app_secret_key"):
        # 32 bytes hex = 256 bits; matches v1's recommended `secrets.token_hex(32)`.
        config["app_secret_key"] = secrets.token_hex(32)

    try:
        settings.write_to_disk(config)
    except OSError as e:
        logger.error(f"failed writing config.json: {e}")
        raise HTTPException(status_code=500, detail=f"Could not write config: {e}")

    background.add_task(_restart_process_after_delay)

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": "Configuration saved. The service is restarting...",
            "restart_seconds": 2,
        },
    )


@router.get("/api/setup/status")
async def setup_status(request: Request) -> JSONResponse:
    """Trivial liveness probe used by the wizard frontend during restart polling.

    Returns whether the app considers itself configured -- the frontend polls
    this and redirects the user to /login (or /) once it flips True.
    """
    return JSONResponse(
        content={
            "configured": settings.is_minimally_configured(),
            "auth_required": settings.auth_required,
        }
    )
