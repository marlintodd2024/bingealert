"""Scheduled user digest and full-season notification delivery."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import Notification, SessionLocal, User
from app.security import clean_email_address, html_escape
from app.services.notification_history import (
    delivery_entries_for_notification,
    mark_notification_delivered,
)


logger = logging.getLogger(__name__)

USER_DIGEST_TYPES = {
    "episode",
    "movie",
    "quality_waiting",
    "coming_soon",
    "issue_resolved",
}


def should_defer_to_user_batch(notification: Notification) -> bool:
    """Return whether the dedicated digest worker owns this queue row."""
    user = notification.user
    if not user or notification.notification_type not in USER_DIGEST_TYPES:
        return False
    if notification.notification_type == "episode" and bool(
        getattr(user, "notify_full_season_only", False)
    ):
        return True
    return (getattr(user, "notification_mode", "instant") or "instant") == "digest"


def next_digest_at(created_at: datetime, hour_utc: int) -> datetime:
    """Return the first configured daily digest time after a row was created."""
    hour = max(0, min(23, int(hour_utc or 0)))
    scheduled = created_at.replace(hour=hour, minute=0, second=0, microsecond=0)
    if created_at >= scheduled:
        scheduled += timedelta(days=1)
    return scheduled


def _parse_hhmm(value: str | None) -> dt_time | None:
    try:
        hour_text, minute_text = str(value or "").split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return dt_time(hour, minute)
    except (TypeError, ValueError):
        pass
    return None


def quiet_hours_until(user: User, now: datetime) -> datetime | None:
    if not bool(getattr(user, "quiet_hours_enabled", False)):
        return None
    start = _parse_hhmm(getattr(user, "quiet_hours_start", None))
    end = _parse_hhmm(getattr(user, "quiet_hours_end", None))
    if not start or not end or start == end:
        return None

    current = now.time()
    if start < end:
        return datetime.combine(now.date(), end) if start <= current < end else None
    if current >= start:
        return datetime.combine(now.date() + timedelta(days=1), end)
    if current < end:
        return datetime.combine(now.date(), end)
    return None


def _episode_coordinate(notification: Notification) -> tuple[int, int] | None:
    for entry in delivery_entries_for_notification(notification):
        season = entry.get("season_number")
        episode = entry.get("episode_number")
        if season is not None and episode is not None:
            return int(season), int(episode)
    return None


def season_is_complete(episodes: list[dict[str, Any]], season_number: int) -> bool:
    """True when every monitored, non-special Sonarr episode has a file."""
    season_episodes = [
        episode
        for episode in episodes
        if int(episode.get("seasonNumber") or -1) == int(season_number)
        and int(episode.get("episodeNumber") or 0) > 0
        and episode.get("monitored") is not False
    ]
    return bool(season_episodes) and all(bool(episode.get("hasFile")) for episode in season_episodes)


async def _completed_seasons(
    notifications: list[Notification],
    sonarr_instances: list[Any] | None = None,
) -> set[tuple[int, int]]:
    requested: set[tuple[int, int]] = set()
    for notification in notifications:
        if notification.notification_type != "episode" or not notification.series_id:
            continue
        coordinate = _episode_coordinate(notification)
        if coordinate:
            requested.add((int(notification.series_id), coordinate[0]))
    if not requested:
        return set()

    if sonarr_instances is None:
        from app.services.sonarr_service import get_all_sonarr_instances

        sonarr_instances = get_all_sonarr_instances()

    series_cache: dict[int, list[dict[str, Any]]] = {}
    for series_id, _ in sorted(requested):
        if series_id in series_cache:
            continue
        series_cache[series_id] = []
        for sonarr in sonarr_instances:
            try:
                episodes = await sonarr.get_episodes_by_series(series_id)
            except Exception as error:
                logger.warning(
                    "Sonarr episode lookup failed for series %s: %s",
                    series_id,
                    error,
                )
                continue
            if episodes:
                series_cache[series_id] = episodes
                break

    return {
        (series_id, season_number)
        for series_id, season_number in requested
        if season_is_complete(series_cache.get(series_id, []), season_number)
    }


def _notification_due(notification: Notification, now: datetime, force: bool) -> bool:
    if notification.send_after and notification.send_after > now:
        return False
    user = notification.user
    if (getattr(user, "notification_mode", "instant") or "instant") != "digest":
        return True
    if force:
        return True
    created_at = notification.created_at or now
    return next_digest_at(created_at, settings.user_digest_hour_utc) <= now


def _eligible_for_delivery(
    notification: Notification,
    now: datetime,
    completed_seasons: set[tuple[int, int]],
    force: bool,
) -> bool:
    if not _notification_due(notification, now, force):
        return False
    user = notification.user
    if notification.notification_type != "episode" or not bool(
        getattr(user, "notify_full_season_only", False)
    ):
        return True
    coordinate = _episode_coordinate(notification)
    if not coordinate or not notification.series_id:
        return False
    return (int(notification.series_id), coordinate[0]) in completed_seasons


def _digest_row(notification: Notification) -> str:
    request_title = notification.request.title if notification.request else notification.subject
    type_label = {
        "episode": "Episode available",
        "movie": "Movie available",
        "quality_waiting": "Quality waiting",
        "coming_soon": "Coming soon",
        "issue_resolved": "Issue resolved",
    }.get(notification.notification_type, notification.notification_type.replace("_", " ").title())
    return (
        '<tr style="border-bottom:1px solid #ececec;">'
        f'<td style="padding:12px;"><strong>{html_escape(request_title)}</strong>'
        f'<br><span style="color:#777;font-size:12px;">{html_escape(notification.subject)}</span></td>'
        f'<td style="padding:12px;white-space:nowrap;">{html_escape(type_label)}</td>'
        "</tr>"
    )


def render_user_digest_html(user: User, notifications: list[Notification]) -> str:
    rows = "".join(_digest_row(notification) for notification in notifications)
    count = len(notifications)
    return f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#f3f4f6;font-family:Arial,sans-serif;color:#262626;">
  <div style="max-width:680px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
    <div style="background:#202124;color:#fff;padding:24px;">
      <div style="color:#f0b429;font-size:13px;font-weight:bold;text-transform:uppercase;">BingeAlert</div>
      <h1 style="margin:8px 0 4px;font-size:24px;">Your request updates</h1>
      <p style="margin:0;color:#d0d3d8;">{count} update{'s' if count != 1 else ''} for {html_escape(user.username)}</p>
    </div>
    <div style="padding:20px 24px 28px;">
      <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;border:1px solid #ececec;">
        <thead>
          <tr style="background:#fafafa;">
            <th align="left" style="padding:10px 12px;">Title</th>
            <th align="left" style="padding:10px 12px;">Update</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="margin:20px 0 0;color:#777;font-size:12px;">These updates were grouped using your BingeAlert notification preferences.</p>
    </div>
  </div>
</body>
</html>
"""


async def send_due_user_digests(
    *,
    now: datetime | None = None,
    force: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
    email_service: Any | None = None,
    sonarr_instances: list[Any] | None = None,
) -> dict[str, int]:
    """Send due grouped user updates using an explicitly closed DB session."""
    current = now or datetime.utcnow()
    db = session_factory()
    try:
        pending = (
            db.query(Notification)
            .options(joinedload(Notification.user), joinedload(Notification.request))
            .join(User, Notification.user_id == User.id)
            .filter(
                Notification.sent.is_(False),
                User.is_active.is_(True),
                Notification.notification_type.in_(USER_DIGEST_TYPES),
                or_(
                    User.notification_mode == "digest",
                    User.notify_full_season_only.is_(True),
                ),
            )
            .order_by(Notification.user_id.asc(), Notification.created_at.asc())
            .all()
        )
        deferred = [notification for notification in pending if should_defer_to_user_batch(notification)]
        season_waiting = [
            notification
            for notification in deferred
            if notification.notification_type == "episode"
            and bool(getattr(notification.user, "notify_full_season_only", False))
        ]
        completed = await _completed_seasons(season_waiting, sonarr_instances=sonarr_instances)
        grouped: dict[int, list[Notification]] = defaultdict(list)
        held = 0
        for notification in deferred:
            if _eligible_for_delivery(notification, current, completed, force):
                grouped[notification.user_id].append(notification)
            else:
                held += 1

        if email_service is None:
            from app.services.email_service import EmailService

            email_service = EmailService()

        sent_users = 0
        sent_notifications = 0
        failed_users = 0
        for notifications in grouped.values():
            user = notifications[0].user
            quiet_until = quiet_hours_until(user, current)
            if quiet_until:
                for notification in notifications:
                    notification.send_after = quiet_until
                held += len(notifications)
                continue

            recipient = clean_email_address(user.email)
            if not recipient:
                for notification in notifications:
                    notification.error_message = "Invalid user email address"
                    notification.send_after = current + timedelta(days=1)
                failed_users += 1
                continue

            count = len(notifications)
            subject = f"Your BingeAlert digest: {count} update{'s' if count != 1 else ''}"
            success = await email_service.send_email(
                to_email=recipient,
                subject=subject,
                html_body=render_user_digest_html(user, notifications),
                user=user,
            )
            if success:
                sent_at = current
                for notification in notifications:
                    notification.sent = True
                    notification.sent_at = sent_at
                    notification.error_message = None
                    mark_notification_delivered(db, notification, sent_at=sent_at)
                sent_users += 1
                sent_notifications += count
            else:
                for notification in notifications:
                    notification.error_message = "SMTP digest send failed"
                    notification.send_after = current + timedelta(hours=1)
                failed_users += 1

        db.commit()
        return {
            "users_sent": sent_users,
            "notifications_sent": sent_notifications,
            "users_failed": failed_users,
            "notifications_held": held,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
