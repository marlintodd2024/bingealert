"""Token-authenticated user status and preference portal."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import (
    EpisodeTracking,
    MediaRequest,
    Notification,
    SharedRequest,
    User,
    get_db,
)


router = APIRouter()
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_NOTIFICATION_MODES = {"instant", "digest"}
_CHANNELS = {"email"}


def _public_url(path: str) -> str | None:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base or not base.startswith(("http://", "https://")):
        return None
    return f"{base}{path}"


def _require_user(token: str, db: Session) -> User:
    if not _TOKEN_RE.match(token or ""):
        raise HTTPException(status_code=404, detail="Not found")
    user = db.query(User).filter(User.status_token == token).first()
    if not user or user.is_active is False:
        raise HTTPException(status_code=404, detail="Not found")
    return user


def _preference_dict(user: User) -> dict[str, Any]:
    return {
        "notification_mode": getattr(user, "notification_mode", None) or "instant",
        "quiet_hours_enabled": bool(getattr(user, "quiet_hours_enabled", False)),
        "quiet_hours_start": getattr(user, "quiet_hours_start", None) or "22:00",
        "quiet_hours_end": getattr(user, "quiet_hours_end", None) or "07:00",
        "notify_full_season_only": bool(getattr(user, "notify_full_season_only", False)),
        "notify_quality_upgrades": bool(getattr(user, "notify_quality_upgrades", True)),
        "preferred_channel": getattr(user, "preferred_channel", None) or "email",
        "available_channels": sorted(_CHANNELS),
    }


def _request_state(request: MediaRequest, episodes: list[EpisodeTracking], notifications: list[Notification]) -> str:
    if request.status == "available":
        return "available"
    if request.media_type == "tv" and any(ep.available_in_plex for ep in episodes):
        return "downloaded"
    if any(n.notification_type in {"quality_waiting", "coming_soon"} for n in notifications):
        return "waiting"
    if any(not n.sent for n in notifications):
        return "downloaded"
    if request.status == "approved":
        return "waiting"
    return request.status or "pending"


def _request_to_dict(
    request: MediaRequest,
    role: str,
    episodes: list[EpisodeTracking],
    notifications: list[Notification],
) -> dict[str, Any]:
    available_episodes = sum(1 for ep in episodes if ep.available_in_plex)
    notified_episodes = sum(1 for ep in episodes if ep.notified)
    latest_notification = notifications[0] if notifications else None
    return {
        "id": request.id,
        "title": request.title,
        "media_type": request.media_type,
        "status": request.status,
        "state": _request_state(request, episodes, notifications),
        "role": role,
        "created_at": request.created_at.isoformat() if request.created_at else None,
        "updated_at": request.updated_at.isoformat() if request.updated_at else None,
        "episode_count": len(episodes),
        "available_episodes": available_episodes,
        "notified_episodes": notified_episodes,
        "latest_notification": latest_notification.created_at.isoformat() if latest_notification else None,
    }


def _notification_to_dict(notification: Notification) -> dict[str, Any]:
    return {
        "id": notification.id,
        "request_id": notification.request_id,
        "request_title": notification.request.title if notification.request else "",
        "notification_type": notification.notification_type,
        "subject": notification.subject,
        "sent": bool(notification.sent),
        "sent_at": notification.sent_at.isoformat() if notification.sent_at else None,
        "send_after": notification.send_after.isoformat() if notification.send_after else None,
        "error_message": notification.error_message,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
    }


def _collect_user_status(user: User, token: str, db: Session) -> dict[str, Any]:
    shared_rows = db.query(SharedRequest).filter(SharedRequest.user_id == user.id).all()
    shared_ids = [row.request_id for row in shared_rows]
    shared_id_set = set(shared_ids)
    filters = [MediaRequest.user_id == user.id]
    if shared_ids:
        filters.append(MediaRequest.id.in_(shared_ids))
    requests = (
        db.query(MediaRequest)
        .filter(or_(*filters))
        .order_by(MediaRequest.updated_at.desc())
        .limit(200)
        .all()
    )
    request_ids = [request.id for request in requests]
    episodes_by_request: dict[int, list[EpisodeTracking]] = {request.id: [] for request in requests}
    notifications_by_request: dict[int, list[Notification]] = {request.id: [] for request in requests}
    if request_ids:
        episodes = (
            db.query(EpisodeTracking)
            .filter(EpisodeTracking.request_id.in_(request_ids))
            .order_by(EpisodeTracking.season_number.asc(), EpisodeTracking.episode_number.asc())
            .all()
        )
        for episode in episodes:
            episodes_by_request.setdefault(episode.request_id, []).append(episode)
        scoped_notifications = (
            db.query(Notification)
            .filter(Notification.request_id.in_(request_ids), Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .all()
        )
        for notification in scoped_notifications:
            notifications_by_request.setdefault(notification.request_id, []).append(notification)

    notifications = (
        db.query(Notification)
        .options(joinedload(Notification.request))
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(75)
        .all()
    )
    status_path = f"/user/{token}"
    calendar_path = f"/calendar/{user.calendar_token}.ics" if user.calendar_token else None
    calendar_url = _public_url(calendar_path) if calendar_path else None
    webcal_url = None
    if calendar_url:
        webcal_url = calendar_url
        for scheme in ("https://", "http://"):
            if webcal_url.startswith(scheme):
                webcal_url = "webcal://" + webcal_url[len(scheme):]
                break

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        },
        "preferences": _preference_dict(user),
        "links": {
            "status_path": status_path,
            "status_url": _public_url(status_path),
            "calendar_path": calendar_path,
            "calendar_url": calendar_url,
            "webcal_url": webcal_url,
            "seerr_url": settings.jellyseerr_url if settings.jellyseerr_url else None,
        },
        "requests": [
            _request_to_dict(
                request,
                "shared" if request.id in shared_id_set and request.user_id != user.id else "owner",
                episodes_by_request.get(request.id, []),
                notifications_by_request.get(request.id, []),
            )
            for request in requests
        ],
        "notifications": [_notification_to_dict(notification) for notification in notifications],
    }


@router.get("/user/api/{token}")
async def get_user_status(token: str, db: Session = Depends(get_db)):
    user = _require_user(token, db)
    return _collect_user_status(user, token, db)


@router.put("/user/api/{token}/preferences")
async def update_user_preferences(token: str, payload: dict[str, Any], db: Session = Depends(get_db)):
    user = _require_user(token, db)

    mode = str(payload.get("notification_mode") or "instant").strip().lower()
    if mode not in _NOTIFICATION_MODES:
        raise HTTPException(status_code=400, detail="Invalid notification mode")
    quiet_start = str(payload.get("quiet_hours_start") or "22:00").strip()
    quiet_end = str(payload.get("quiet_hours_end") or "07:00").strip()
    if not _TIME_RE.match(quiet_start) or not _TIME_RE.match(quiet_end):
        raise HTTPException(status_code=400, detail="Quiet hours must use HH:MM")
    channel = str(payload.get("preferred_channel") or "email").strip().lower()
    if channel not in _CHANNELS:
        raise HTTPException(status_code=400, detail="Invalid notification channel")

    user.notification_mode = mode
    user.quiet_hours_enabled = bool(payload.get("quiet_hours_enabled"))
    user.quiet_hours_start = quiet_start
    user.quiet_hours_end = quiet_end
    user.notify_full_season_only = bool(payload.get("notify_full_season_only"))
    user.notify_quality_upgrades = bool(payload.get("notify_quality_upgrades"))
    user.preferred_channel = channel
    db.commit()
    db.refresh(user)
    return {
        "success": True,
        "preferences": _preference_dict(user),
    }


@router.get("/user/{token}")
async def user_status_page(token: str, db: Session = Depends(get_db)):
    _require_user(token, db)
    page = _STATIC_DIR / "user_status.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Status page not found")
    return FileResponse(page, media_type="text/html")
