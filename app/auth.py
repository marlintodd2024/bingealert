"""
Authentication middleware for the Plex Notification Portal.

Features:
- Local network bypass (configurable CIDR)
- Session cookie auth for external access
- Cloudflare Turnstile integration (optional)
- Password stored as bcrypt hash in database
"""

import os
import re
import time
import hmac
import hashlib
import json
import logging
import ipaddress
from datetime import datetime, timezone
from typing import Optional

import bcrypt
import httpx
from fastapi import Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Paths that never require auth
PUBLIC_PATHS = [
    "/health",
    "/api/webhooks/",
    "/webhooks/",
    "/api/sse/",
    "/auth/login",
    "/auth/check",
    "/login",
    "/login.html",
    "/static/login.html",
    "/setup",
    "/setup.html",
    "/static/setup.html",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
]


def get_auth_settings(db) -> dict:
    """Load auth settings from database system_config table"""
    from app.database import SystemConfig
    
    settings = {}
    keys = [
        'auth_enabled', 'auth_password_hash', 'local_network_cidr',
        'session_timeout_hours', 'turnstile_enabled',
        'turnstile_site_key', 'turnstile_secret_key'
    ]
    
    for key in keys:
        config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if config:
            settings[key] = config.value
    
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


def verify_session_token(token: str, secret_key: str, max_age_hours: int = 24) -> bool:
    """Verify a session token is valid and not expired"""
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return False
        
        timestamp_str, signature = parts
        
        # Verify signature
        expected = hmac.new(
            secret_key.encode('utf-8'),
            timestamp_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected):
            return False
        
        # Check expiry
        timestamp = int(timestamp_str)
        age_hours = (time.time() - timestamp) / 3600
        
        return age_hours < max_age_hours
    except Exception:
        return False


def is_local_network(client_ip: str, cidr: str) -> bool:
    """Check if a client IP is within the local network CIDR range"""
    try:
        if not cidr:
            return False
        
        # Support multiple CIDRs separated by comma
        cidrs = [c.strip() for c in cidr.split(',')]
        
        client = ipaddress.ip_address(client_ip)
        
        for network_cidr in cidrs:
            try:
                network = ipaddress.ip_network(network_cidr, strict=False)
                if client in network:
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


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that handles authentication for all requests"""
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # Always allow public paths
        for public_path in PUBLIC_PATHS:
            if path.startswith(public_path) or path == public_path:
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
            # On DB error, allow access (don't lock people out)
            return await call_next(request)
        
        auth_enabled = settings.get('auth_enabled', 'false').lower() == 'true'
        
        if not auth_enabled:
            return await call_next(request)
        
        # Check if local network
        client_ip = get_client_ip(request)
        local_cidr = settings.get('local_network_cidr', '')
        
        if local_cidr and is_local_network(client_ip, local_cidr):
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
        return RedirectResponse(url="/login", status_code=302)
