"""Durable notification delivery dedupe helpers."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database import EpisodeTracking, Notification, NotificationDeliveryLog


logger = logging.getLogger(__name__)

_EPISODE_RE = re.compile(r"S(\d{1,3})E(\d{1,3})", re.IGNORECASE)


def episode_dedupe_key(series_id: int | None, season: int, episode: int) -> str:
    series_part = series_id if series_id is not None else "unknown"
    return f"episode:{series_part}:S{int(season):02d}E{int(episode):02d}"


def movie_dedupe_key(request_id: int) -> str:
    return f"movie:{int(request_id)}"


def has_delivery(
    db: Session,
    *,
    user_id: int,
    request_id: int,
    notification_type: str,
    dedupe_key: str,
) -> bool:
    return db.query(NotificationDeliveryLog.id).filter(
        NotificationDeliveryLog.user_id == user_id,
        NotificationDeliveryLog.request_id == request_id,
        NotificationDeliveryLog.notification_type == notification_type,
        NotificationDeliveryLog.dedupe_key == dedupe_key,
    ).first() is not None


def record_delivery(
    db: Session,
    *,
    user_id: int,
    request_id: int,
    notification_type: str,
    dedupe_key: str,
    series_id: int | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
    sent_at: datetime | None = None,
) -> bool:
    stmt = sqlite_insert(NotificationDeliveryLog).values(
        user_id=user_id,
        request_id=request_id,
        notification_type=notification_type,
        dedupe_key=dedupe_key,
        series_id=series_id,
        season_number=season_number,
        episode_number=episode_number,
        sent_at=sent_at,
        created_at=sent_at or datetime.utcnow(),
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[
            "user_id",
            "request_id",
            "notification_type",
            "dedupe_key",
        ]
    )
    result = db.execute(stmt)
    created = bool(result.rowcount)

    if not created and sent_at:
        db.query(NotificationDeliveryLog).filter(
            NotificationDeliveryLog.user_id == user_id,
            NotificationDeliveryLog.request_id == request_id,
            NotificationDeliveryLog.notification_type == notification_type,
            NotificationDeliveryLog.dedupe_key == dedupe_key,
            NotificationDeliveryLog.sent_at.is_(None),
        ).update({"sent_at": sent_at}, synchronize_session=False)

    return created


def _episode_matches(notification: Notification) -> set[tuple[int, int]]:
    text = f"{notification.subject or ''}\n{notification.body or ''}"
    return {
        (int(match.group(1)), int(match.group(2)))
        for match in _EPISODE_RE.finditer(text)
    }


def delivery_entries_for_notification(notification: Notification) -> Iterable[dict]:
    if notification.notification_type == "movie":
        yield {
            "notification_type": "movie",
            "dedupe_key": movie_dedupe_key(notification.request_id),
            "series_id": None,
            "season_number": None,
            "episode_number": None,
        }
        return

    if notification.notification_type == "episode":
        for season, episode in _episode_matches(notification):
            yield {
                "notification_type": "episode",
                "dedupe_key": episode_dedupe_key(notification.series_id, season, episode),
                "series_id": notification.series_id,
                "season_number": season,
                "episode_number": episode,
            }


def record_delivery_for_notification(
    db: Session,
    notification: Notification,
    *,
    sent_at: datetime | None = None,
) -> int:
    count = 0
    for entry in delivery_entries_for_notification(notification):
        if record_delivery(
            db,
            user_id=notification.user_id,
            request_id=notification.request_id,
            sent_at=sent_at or notification.sent_at,
            **entry,
        ):
            count += 1
    return count


def mark_notification_delivered(
    db: Session,
    notification: Notification,
    *,
    sent_at: datetime,
) -> None:
    """Update durable dedupe and request/episode delivery state together."""
    record_delivery_for_notification(db, notification, sent_at=sent_at)
    if notification.notification_type == "movie" and notification.request:
        notification.request.status = "available"
        return
    if notification.notification_type != "episode":
        return

    for entry in delivery_entries_for_notification(notification):
        season_number = entry.get("season_number")
        episode_number = entry.get("episode_number")
        if season_number is None or episode_number is None:
            continue
        query = db.query(EpisodeTracking).filter(
            EpisodeTracking.request_id == notification.request_id,
            EpisodeTracking.season_number == season_number,
            EpisodeTracking.episode_number == episode_number,
        )
        if entry.get("series_id") is not None:
            query = query.filter(EpisodeTracking.series_id == entry["series_id"])
        tracking = query.first()
        if tracking:
            tracking.notified = True
            tracking.available_in_plex = True


def backfill_delivery_log_from_notifications(db: Session) -> int:
    """Backfill ledger rows from sent notifications before retention purges them."""
    rows = db.query(Notification).filter(Notification.sent.is_(True)).all()
    created = 0
    for notification in rows:
        try:
            created += record_delivery_for_notification(
                db,
                notification,
                sent_at=notification.sent_at,
            )
        except Exception:
            logger.debug(
                "failed to backfill delivery log from notification %s",
                notification.id,
                exc_info=True,
            )
    return created
