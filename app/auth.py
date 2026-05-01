"""
Authentication middleware for BingeAlert.

Features:
- Local network bypass (configurable CIDR) with proper X-Forwarded-For handling
- Session cookie auth for external access
- Cloudflare Turnstile integration (optional)
- Password stored as bcrypt hash in database
- Login rate limiting
- Setup page lockdown after initial setup
"""

import os
import re
import time
import hmac
import hashlib
import json
import logging
import ipaddress
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import bcrypt
import httpx
from fastapi import Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# ============================================================
# SECURITY FIX [CRIT-1, HIGH-3]: Removed /docs, /redoc,
# /openapi.json, and /api/sse/ from public paths.
# SECURITY FIX [MED-1]: Removed /setup paths — handled
# conditionally in middleware based on setup_complete status.
# ============================================================
PUBLIC_PATHS = [
    "/health",
    "/api/webhooks/",
    "/webhooks/",
    "/auth/login",
    "/auth/check",
    "/login",
    "/login.html",
    "/static/login.html",
    "/favicon.ico",
]

# ============================================================
# SECURITY FIX [MED-5]: Login rate limiting
# ============================================================
LOGIN_ATTEMPTS = defaultdict(list)  # IP -> [timestamp, ...]
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutes


def check_login_rate_limit(ip: str) -> bool:
    """Return True if login attempt is allowed, False if rate limited"""
    now = time.time()
    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < LOGIN_WINDOW_SECONDS]
    if len(LOGIN_ATTEMPTS[ip]) >= MAX_LOGIN_ATTEMPTS:
        return False
    LOGIN_ATTEMPTS[ip].append(now)
    return True


def get_auth_settings(db) -> dict:
    """Get all auth-related settings from the database"""
    from app.database import SystemConfig

    settings = {}
    configs = db.query(SystemConfig).filter(
        SystemConfig.key.in_([
            'auth_enabled', 'auth_password_hash', 'local_network_cidr',
            'session_timeout_hours', 'turnstile_enabled',
            'turnstile_site_key', 'turnstile_secret_key'
        ])
    ).all()

    for config in configs:
        settings[config.key] = config.value

    return settings


def set_auth_setting(db, key: str, value: str):
    """Set an auth setting in the database"""
    from app.database import SystemConfig

    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if config:
        config.value = value
        config.updated_at = datetime.utcnow()
    else:
        config = SystemConfig(key=key, value=value)
        db.add(config)
    db.commit()


def hash_password(password: str) -> str:
    """Hash a password with bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash"""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def create_session_token(secret_key: str) -> str:
    """Create a signed session token with timestamp"""
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret_key.encode('utf-8'),
        timestamp.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"{timestamp}.{signature}"


def verify_session_token(token: str, secret_key: str, timeout_hours: int = 24) -> bool:
    """Verify a session token is valid and not expired"""
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return False

        timestamp_str, signature = parts
        timestamp = int(timestamp_str)

        # Check expiry
        age_hours = (time.time() - timestamp) / 3600
        if age_hours > timeout_hours:
            return False

        # Check signature
        expected = hmac.new(
            secret_key.encode('utf-8'),
            timestamp_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


def is_local_network(ip_str: str, cidr_list: str) -> bool:
    """Check if an IP address is in the local network CIDR range(s)"""
    try:
        client_ip = ipaddress.ip_address(ip_str)

        for cidr in cidr_list.split(','):
            cidr = cidr.strip()
            if not cidr:
                continue
            try:
                network = ipaddress.ip_network(cidr, strict=False)
                if client_ip in network:
                    return True
            except ValueError:
                continue

        return False
    except Exception as e:
        logger.warning(f"Error checking local network: {e}")
        return False


def get_client_ip(request: Request) -> str:
    """Get the real client IP, checking forwarded headers"""
    # Check X-Forwarded-For (from reverse proxies like nginx, Cloudflare)
    forwarded_for = request.headers.get('x-forwarded-for')
    if forwarded_for:
        # First IP in the chain is the original client
        return forwarded_for.split(',')[0].strip()

    # Check X-Real-IP
    real_ip = request.headers.get('x-real-ip')
    if real_ip:
        return real_ip.strip()

    # Check CF-Connecting-IP (Cloudflare)
    cf_ip = request.headers.get('cf-connecting-ip')
    if cf_ip:
        return cf_ip.strip()

    # Fall back to direct connection IP
    return request.client.host if request.client else '0.0.0.0'


async def verify_turnstile(token: str, secret_key: str, client_ip: str = None) -> bool:
    """Verify a Cloudflare Turnstile token"""
    try:
        async with httpx.AsyncClient() as client:
            data = {
                'secret': secret_key,
                'response': token,
            }
            if client_ip:
                data['remoteip'] = client_ip

            resp = await client.post(
                'https://challenges.cloudflare.com/turnstile/v0/siteverify',
                data=data,
                timeout=10
            )

            result = resp.json()
            return result.get('success', False)
    except Exception as e:
        logger.error(f"Turnstile verification failed: {e}")
        return False


# ============================================================
# SECURITY FIX [MED-3]: Warn on weak/default APP_SECRET_KEY
# ============================================================
_secret_key_warned = False


def _warn_if_weak_secret():
    global _secret_key_warned
    if _secret_key_warned:
        return
    secret = os.getenv('APP_SECRET_KEY', '')
    if not secret or secret in ('default-secret', 'change-me', 'CHANGE_ME_TO_A_RANDOM_STRING'):
        logger.critical(
            "⚠️  APP_SECRET_KEY is not set or uses a default value! "
            "Session cookies can be forged by anyone. "
            "Set a strong random value in your .env file immediately."
        )
    _secret_key_warned = True


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that handles authentication for all requests"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Warn on first request if secret key is weak
        _warn_if_weak_secret()

        # Always allow public paths (webhooks, health, login page)
        for public_path in PUBLIC_PATHS:
            if path.startswith(public_path) or path == public_path:
                return await call_next(request)

        # ============================================================
        # SECURITY FIX [MED-1]: Setup pages only public if setup
        # hasn't been completed yet. After setup, require auth.
        # ============================================================
        setup_paths = ["/setup", "/setup.html", "/static/setup.html"]
        if any(path.startswith(sp) or path == sp for sp in setup_paths):
            try:
                from app.database import get_db, SystemConfig
                db = next(get_db())
                try:
                    setup_done = db.query(SystemConfig).filter(
                        SystemConfig.key == "setup_complete",
                        SystemConfig.value == "true"
                    ).first()
                finally:
                    db.close()
                if not setup_done:
                    # Setup not complete — allow public access
                    return await call_next(request)
                # Setup IS complete — fall through to require auth
            except Exception:
                # DB error during setup check — allow access to not lock out
                return await call_next(request)

        # Check if auth is enabled
        try:
            from app.database import get_db
            db = next(get_db())
            try:
                settings = get_auth_settings(db)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Auth middleware DB error: {e}")
            # ============================================================
            # SECURITY FIX [CRIT-0]: On DB error, DENY access instead of
            # allowing it. Previously this allowed everything through.
            # ============================================================
            return JSONResponse(
                status_code=503,
                content={"detail": "Service temporarily unavailable"}
            )

        auth_enabled = settings.get('auth_enabled', 'false').lower() == 'true'

        if not auth_enabled:
            return await call_next(request)

        # Check if local network
        client_ip = get_client_ip(request)
        local_cidr = settings.get('local_network_cidr', '')

        if local_cidr and is_local_network(client_ip, local_cidr):
            # ============================================================
            # SECURITY FIX [HIGH-2]: Log when CIDR bypass is used so
            # admin can detect if proxy IP is incorrectly matching.
            # ============================================================
            logger.debug(f"Local network bypass for IP: {client_ip}")
            return await call_next(request)

        # Check session cookie
        session_token = request.cookies.get('pnp_session')
        secret_key = os.getenv('APP_SECRET_KEY', 'default-secret')
        timeout_hours = int(settings.get('session_timeout_hours', '24'))

        if session_token and verify_session_token(session_token, secret_key, timeout_hours):
            return await call_next(request)

        # Not authenticated — redirect to login or return 401 for API calls
        if path.startswith('/admin/') or path.startswith('/api/'):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"}
            )

        # For page requests, redirect to login
        return RedirectResponse(url="/login")
