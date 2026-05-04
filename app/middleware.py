"""HTTP middleware for BingeAlert v2.

SetupGateMiddleware -- gates the entire app to /setup until is_minimally_configured()
returns True. This is the *only* path enforcement that matters in v2 setup mode;
auth (bcrypt + session + CIDR bypass) lives in app.auth.AuthMiddleware and runs
on top once setup is complete.

Both middlewares re-read settings from the module-level singleton on every
request. The singleton is rebuilt on process boot (which is when /data/config.json
is read), so the wizard's "save then restart" flow is what materialises new
config -- there's no in-process hot reload.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import settings


# Paths that are reachable even when setup is incomplete. /setup serves the
# wizard page; /api/setup is its backend; /static is required by the wizard
# (CSS, JS); /health and /favicon.ico are housekeeping.
_SETUP_MODE_ALLOWED = (
    "/setup",
    "/api/setup",
    "/static/",
    "/health",
    "/favicon.ico",
)


def _is_html_request(request: Request) -> bool:
    """Best-effort: does this request want HTML, or is it an API call?"""
    accept = request.headers.get("accept", "")
    return "text/html" in accept or accept == "" or accept == "*/*"


class SetupGateMiddleware(BaseHTTPMiddleware):
    """If the app isn't minimally configured, allow only the wizard routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if settings.is_minimally_configured():
            # Setup is complete -- lock the wizard page and the save endpoint
            # (turnover §2.5). Leave /api/setup/status reachable so the wizard
            # frontend can detect that the restart finished.
            if path == "/setup" or path == "/api/setup":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Setup already complete"},
                )
            return await call_next(request)

        # Not configured -- only let the wizard + housekeeping through.
        if any(path == p or path.startswith(p) for p in _SETUP_MODE_ALLOWED):
            return await call_next(request)

        if _is_html_request(request):
            return RedirectResponse(url="/setup", status_code=302)
        return JSONResponse(
            status_code=503,
            content={"detail": "Setup required. Visit /setup in a browser to configure."},
        )
