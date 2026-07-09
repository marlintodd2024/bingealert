"""Pushover push alerts for admin/operations events."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings


logger = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


def _truncate(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _admin_url() -> str | None:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None
    return f"{base}/admin"


class PushoverService:
    """Send optional Pushover alerts when selected as the alert provider."""

    @property
    def enabled(self) -> bool:
        return (
            bool(settings.alert_webhook_enabled)
            and (settings.alert_webhook_type or "").strip().lower() == "pushover"
        )

    @property
    def configured(self) -> bool:
        return bool(settings.pushover_app_token and settings.pushover_user_key)

    async def send(
        self,
        *,
        title: str,
        message: str,
        priority: int = 0,
        url: str | None = None,
        url_title: str | None = None,
        app_token: str | None = None,
        user_key: str | None = None,
        sound: str | None = None,
        require_enabled: bool = True,
    ) -> bool:
        if require_enabled and not self.enabled:
            return False
        token = (app_token if app_token is not None else settings.pushover_app_token) or ""
        user = (user_key if user_key is not None else settings.pushover_user_key) or ""
        selected_sound = sound if sound is not None else settings.pushover_sound

        if not token or not user:
            logger.warning("Pushover alert skipped: app token or user/group key missing")
            return False

        payload: dict[str, Any] = {
            "token": token,
            "user": user,
            "title": _truncate(title, 250),
            "message": _truncate(message, 1024),
            "priority": max(-2, min(1, int(priority or 0))),
        }
        if selected_sound:
            payload["sound"] = selected_sound.strip()
        if url:
            payload["url"] = url
            payload["url_title"] = url_title or "Open BingeAlert"

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(PUSHOVER_API_URL, data=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error("Pushover alert failed: %s", e)
            return False

    async def send_service_health(self, kind: str, row_data: dict[str, Any]) -> bool:
        is_recovery = kind == "recovery"
        service_name = row_data.get("service_name") or row_data.get("service_key") or "Service"
        title = f"{service_name} recovered" if is_recovery else f"{service_name} is down"
        message = (
            f"Status: {row_data.get('status') or 'unknown'}\n"
            f"Failures: {row_data.get('consecutive_failures') or 0}\n"
            f"Checked: {row_data.get('last_checked_at') or 'unknown'}\n"
            f"Error: {row_data.get('last_error') or 'none'}"
        )
        return await self.send(
            title=f"BingeAlert: {title}",
            message=message,
            priority=0 if is_recovery else 1,
            url=_admin_url(),
        )

    async def send_episode_available(
        self,
        *,
        series_title: str,
        episodes: list[dict[str, Any]],
        notification_count: int = 0,
    ) -> bool:
        episode_labels = []
        seen: set[tuple[Any, Any]] = set()
        for episode in episodes:
            key = (episode.get("season"), episode.get("episode"))
            if key in seen:
                continue
            seen.add(key)
            label = f"S{int(episode.get('season') or 0):02d}E{int(episode.get('episode') or 0):02d}"
            if episode.get("title"):
                label += f" - {episode['title']}"
            episode_labels.append(label)

        count_label = f"{len(episode_labels)} episode" + ("" if len(episode_labels) == 1 else "s")
        message = f"{count_label} available"
        if episode_labels:
            message += ":\n" + "\n".join(episode_labels[:8])
            if len(episode_labels) > 8:
                message += f"\n...and {len(episode_labels) - 8} more"
        if notification_count:
            message += f"\nQueued notifications: {notification_count}"

        return await self.send(
            title=f"New episodes: {series_title}",
            message=message,
            priority=0,
            url=_admin_url(),
        )

    async def send_movie_available(self, *, movie_title: str, notification_count: int = 0) -> bool:
        message = "Movie is available."
        if notification_count:
            message += f"\nQueued notifications: {notification_count}"
        return await self.send(
            title=f"Movie available: {movie_title}",
            message=message,
            priority=0,
            url=_admin_url(),
        )

    async def send_issue_reported(
        self,
        *,
        title: str,
        media_type: str,
        issue_type: str,
        reported_by: str,
        autofix_mode: str,
    ) -> bool:
        message = (
            f"Media type: {media_type or 'unknown'}\n"
            f"Issue type: {issue_type or 'other'}\n"
            f"Reported by: {reported_by or 'Unknown'}\n"
            f"Mode: {autofix_mode or 'manual'}"
        )
        return await self.send(
            title=f"Issue reported: {title}",
            message=message,
            priority=1,
            url=_admin_url(),
        )

    async def send_issue_resolved(
        self,
        *,
        title: str,
        media_type: str,
        resolved_count: int = 1,
    ) -> bool:
        message = f"Resolved {resolved_count} issue" + ("" if resolved_count == 1 else "s")
        message += f"\nMedia type: {media_type or 'unknown'}"
        return await self.send(
            title=f"Issue resolved: {title}",
            message=message,
            priority=0,
            url=_admin_url(),
        )
