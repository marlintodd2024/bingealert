"""Authentication for BingeAlert v2.

What changed vs v1:
    - Auth settings (admin password hash, CIDRs, Turnstile keys, session timeout)
      now live in /data/config.json via app.config.settings, NOT in the
      system_config DB table. The wizard writes config.json; the bcrypt hash
      is set via settings.write_to_disk({"admin_password_hash": ...}).
    - AuthMiddleware no longer needs a DB session per request -- everything
      it needs is on the settings object. This also means it works during
      database migrations / outages.
    - Setup-mode gating moved out of here into app.middleware.SetupGateMiddleware.

What carried forward:
    - bcrypt password hash + verify
    - HMAC-signed session cookie (timestamp.signature)
    - Local network CIDR bypass with X-Forwarded-For / CF-Connecting-IP support
    - Login rate limit (5 attempts / IP / 5 minutes)
    - Optional Cloudflare Turnstile verification
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import time
from collections import defaultdict
from typing import Optional

import bcrypt
import httpx
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import settings


logger = logging.getLogger(__name__)


# Paths reachable without auth -- webhooks must be (upstream services post here),
# health is for orchestrators, login + auth/check power the login flow.
_PUBLIC_PATHS = (
    "/health",
    "/webhooks/",
    "/login",
    "/login.html",
    "/auth/login",
    "/auth/check",
    "/static/",
    "/favicon.ico",
    # The wizard frontend polls this *after* a restart but *before* the user
    # logs in -- needs to be reachable without auth.
    "/api/setup/status",
    # Version probe so the login page footer can render the running version.
    "/api/version",
    # Per-user .ics calendar feed -- the URL embeds a long random token that
    # IS the credential, so calendar apps subscribe without a login session.
    "/calendar/",
)


# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session token (HMAC over a timestamp)
# ---------------------------------------------------------------------------


def create_session_token(secret_key: str) -> str:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret_key.encode("utf-8"), timestamp.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{timestamp}.{signature}"


def verify_session_token(token: str, secret_key: str, max_age_seconds: int) -> bool:
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return False
        timestamp_str, signature = parts
        timestamp = int(timestamp_str)
        if time.time() - timestamp > max_age_seconds:
            return False
        expected = hmac.new(
            secret_key.encode("utf-8"), timestamp_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Client IP + CIDR bypass
# ---------------------------------------------------------------------------


def get_client_ip(request: Request) -> str:
    """Return client IP, trusting proxy headers only from trusted proxies."""
    peer_ip = request.client.host if request.client else "0.0.0.0"
    if is_local_network(peer_ip, settings.trusted_proxy_cidrs):
        candidate = None
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            candidate = fwd.split(",")[0].strip()
        else:
            real = request.headers.get("x-real-ip")
            if real:
                candidate = real.strip()
            else:
                cf = request.headers.get("cf-connecting-ip")
                if cf:
                    candidate = cf.strip()
        if candidate:
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                logger.warning("Ignoring invalid proxy client IP header")
    return peer_ip


def is_local_network(ip_str: str, cidr_csv: str) -> bool:
    if not cidr_csv:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for cidr in cidr_csv.split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# Login rate limiting
# ---------------------------------------------------------------------------


_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300


def login_attempt_allowed(ip: str) -> bool:
    """Return True if a new login attempt is allowed; record it if so."""
    now = time.time()
    _LOGIN_ATTEMPTS[ip] = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < LOGIN_WINDOW_SECONDS]
    if len(_LOGIN_ATTEMPTS[ip]) >= LOGIN_MAX_ATTEMPTS:
        return False
    _LOGIN_ATTEMPTS[ip].append(now)
    return True


def clear_login_attempts(ip: str) -> None:
    _LOGIN_ATTEMPTS.pop(ip, None)


# ---------------------------------------------------------------------------
# Cloudflare Turnstile
# ---------------------------------------------------------------------------


def get_auth_settings(db) -> dict:
    """Compat shim for v1 admin.py code that read auth state from system_config.

    In v2, auth state lives in app.config.settings (loaded from /data/config.json).
    This shim returns a dict shaped like the v1 system_config rows so the existing
    admin.py read paths (e.g. /admin/config GET) work without modification.

    The `db` arg is unused -- accepted only because callers still pass a session.
    """
    timeout_hours = max(1, int(settings.session_max_age_seconds // 3600))
    return {
        "auth_enabled": "true" if settings.auth_required else "false",
        "auth_password_hash": settings.admin_password_hash or "",
        "local_network_cidr": settings.local_network_cidrs or "",
        "trusted_proxy_cidrs": settings.trusted_proxy_cidrs or "",
        "session_timeout_hours": str(timeout_hours),
        "turnstile_enabled": "true" if settings.turnstile_secret_key else "false",
        "turnstile_site_key": settings.turnstile_site_key or "",
        "turnstile_secret_key": settings.turnstile_secret_key or "",
    }


async def verify_turnstile(token: str, secret_key: str, client_ip: Optional[str] = None) -> bool:
    if not token or not secret_key:
        return False
    data = {"secret": secret_key, "response": token}
    if client_ip:
        data["remoteip"] = client_ip
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify", data=data
            )
        return bool(resp.json().get("success"))
    except Exception as e:
        logger.error(f"Turnstile verification failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Require auth on protected routes once setup is complete.

    No-op when settings.auth_required is False. CIDR-matched clients bypass
    the password check. SetupGateMiddleware runs *before* this and gates
    everything to /setup until configured -- so AuthMiddleware can assume
    config is loaded.
    """

    SESSION_COOKIE = "ba_session"

    async def dispatch(self, request: Request, call_next):
        # Pre-setup, SetupGateMiddleware (which runs first) has already
        # restricted the surface to the wizard + housekeeping. Don't try
        # to enforce auth on routes that don't exist yet -- otherwise
        # /setup -> /login -> SetupGate redirect to /setup -> infinite loop.
        if not settings.is_minimally_configured():
            return await call_next(request)

        if not settings.auth_required:
            return await call_next(request)

        path = request.url.path
        for pub in _PUBLIC_PATHS:
            if path == pub or path.startswith(pub):
                return await call_next(request)

        client_ip = get_client_ip(request)
        if is_local_network(client_ip, settings.local_network_cidrs):
            logger.debug(f"local-network bypass for {client_ip}")
            return await call_next(request)

        token = request.cookies.get(self.SESSION_COOKIE)
        if (
            token
            and settings.app_secret_key
            and verify_session_token(token, settings.app_secret_key, settings.session_max_age_seconds)
        ):
            return await call_next(request)

        # Unauthenticated. API routes get JSON 401; everything else redirects to login.
        if path.startswith("/api/") or path.startswith("/admin/"):
            return JSONResponse(
                status_code=401, content={"detail": "Authentication required"}
            )
        return RedirectResponse(url="/login", status_code=302)
