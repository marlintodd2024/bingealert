"""Login / logout / check endpoints.

Talks to the existing app/static/login.html unchanged -- the contract
matches what that page already calls (/auth/check, /auth/login).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.auth import (
    AuthMiddleware,
    create_session_token,
    get_client_ip,
    is_local_network,
    login_attempt_allowed,
    verify_password,
    verify_session_token,
    verify_turnstile,
)
from app.config import settings


logger = logging.getLogger(__name__)
router = APIRouter()
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/login")
async def login_page() -> FileResponse:
    page = _STATIC_DIR / "login.html"
    if not page.is_file():
        return JSONResponse(status_code=404, content={"detail": "login.html missing"})
    return FileResponse(page, media_type="text/html")


@router.get("/auth/check")
async def auth_check(request: Request) -> dict:
    """Used by login.html on load to decide whether to show the form / Turnstile."""
    client_ip = get_client_ip(request)

    if not settings.auth_required:
        return {"authenticated": True, "auth_enabled": False, "client_ip": client_ip}

    if is_local_network(client_ip, settings.local_network_cidrs):
        return {
            "authenticated": True,
            "auth_enabled": True,
            "local_network": True,
            "client_ip": client_ip,
        }

    token = request.cookies.get(AuthMiddleware.SESSION_COOKIE)
    authed = bool(
        token
        and settings.app_secret_key
        and verify_session_token(
            token, settings.app_secret_key, settings.session_max_age_seconds
        )
    )

    turnstile_on = bool(settings.turnstile_site_key and settings.turnstile_secret_key)
    return {
        "authenticated": authed,
        "auth_enabled": True,
        "client_ip": client_ip,
        "turnstile_enabled": turnstile_on,
        "turnstile_site_key": settings.turnstile_site_key or "",
    }


class LoginPayload(BaseModel):
    password: str
    turnstile_token: Optional[str] = None


@router.post("/auth/login")
async def auth_login(payload: LoginPayload, request: Request) -> JSONResponse:
    client_ip = get_client_ip(request)

    if not login_attempt_allowed(client_ip):
        logger.warning(f"login rate-limited from {client_ip}")
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many login attempts. Try again in a few minutes."},
        )

    if settings.turnstile_secret_key:
        ok = await verify_turnstile(
            payload.turnstile_token or "", settings.turnstile_secret_key, client_ip
        )
        if not ok:
            logger.warning(f"turnstile failed from {client_ip}")
            return JSONResponse(
                status_code=403, content={"detail": "Verification failed. Try again."}
            )

    if not settings.admin_password_hash:
        return JSONResponse(
            status_code=500, content={"detail": "No admin password configured"}
        )

    if not verify_password(payload.password, settings.admin_password_hash):
        logger.warning(f"failed login from {client_ip}")
        return JSONResponse(status_code=401, content={"detail": "Invalid password"})

    if not settings.app_secret_key:
        return JSONResponse(
            status_code=500, content={"detail": "No session secret configured"}
        )

    token = create_session_token(settings.app_secret_key)
    response = JSONResponse(content={"success": True})
    response.set_cookie(
        key=AuthMiddleware.SESSION_COOKIE,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=False,  # flip to True when fronted with HTTPS
    )
    logger.info(f"login from {client_ip}")
    return response


@router.post("/auth/logout")
async def auth_logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=302)
    # Pass the same path/httponly/samesite that we used on set_cookie above.
    # Some browsers (notably recent Chrome/Edge) match cookies for deletion
    # by attribute set; without these the Max-Age=0 cookie is treated as a
    # different cookie and the original sticks around -- resulting in
    # "logout doesn't actually log me out".
    response.delete_cookie(
        key=AuthMiddleware.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return response
