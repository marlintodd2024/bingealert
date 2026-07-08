"""Scheduled operational maintenance tasks."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Notification, SessionLocal, SystemConfig
from app.services.admin_activity import record_admin_activity
from app.services.backup_service import BackupService


logger = logging.getLogger(__name__)


def purge_sent_notifications(db: Session, days_old: int) -> int:
    """Delete sent notifications older than the retention window."""
    days = max(1, min(int(days_old or 90), 3650))
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = db.query(Notification).filter(
        Notification.sent == True,
        func.coalesce(Notification.sent_at, Notification.created_at) < cutoff,
    ).delete(synchronize_session=False)
    return int(deleted or 0)


def _get_state_datetime(db: Session, key: str) -> datetime | None:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def _set_state_datetime(db: Session, key: str, value: datetime) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value.isoformat()
        row.updated_at = datetime.utcnow()
    else:
        db.add(SystemConfig(key=key, value=value.isoformat()))


def _due(last_run: datetime | None, interval_hours: int) -> bool:
    if not last_run:
        return True
    return datetime.utcnow() - last_run >= timedelta(hours=max(1, int(interval_hours or 1)))


def _run_notification_retention(db: Session) -> int:
    if not settings.notification_retention_enabled:
        return 0
    state_key = "ops_notification_retention_last_run"
    if not _due(_get_state_datetime(db, state_key), settings.notification_retention_interval_hours):
        return 0

    deleted = purge_sent_notifications(db, settings.notification_retention_days)
    _set_state_datetime(db, state_key, datetime.utcnow())
    record_admin_activity(
        "notification_retention",
        f"Purged {deleted} sent notification(s)",
        details={"days": settings.notification_retention_days, "count": deleted},
        db=db,
    )
    return deleted


def _run_scheduled_backup(db: Session) -> str | None:
    if not settings.backup_schedule_enabled:
        return None
    state_key = "ops_backup_schedule_last_run"
    if not _due(_get_state_datetime(db, state_key), settings.backup_schedule_interval_hours):
        return None

    service = BackupService()
    backup_path = service.create_backup(include_config=True)
    if not backup_path:
        record_admin_activity(
            "scheduled_backup",
            "Scheduled backup failed",
            status="error",
        )
        raise RuntimeError("scheduled backup failed")

    backups = service.list_backups()
    retention = max(1, int(settings.backup_schedule_retention_count or 8))
    for old in backups[retention:]:
        service.delete_backup(old["filename"])

    _set_state_datetime(db, state_key, datetime.utcnow())
    record_admin_activity(
        "scheduled_backup",
        "Scheduled backup created",
        details={"backup": backup_path, "retention_count": retention},
        db=db,
    )
    return backup_path


async def run_ops_maintenance_cycle() -> dict[str, object]:
    from app.background.system_health import (
        record_worker_failure,
        record_worker_started,
        record_worker_success,
    )

    next_run_at = datetime.utcnow() + timedelta(hours=1)
    started_at = record_worker_started(
        "ops_maintenance",
        "Operational maintenance",
        next_run_at=next_run_at,
    )
    db = SessionLocal()
    try:
        deleted = _run_notification_retention(db)
        backup_path = _run_scheduled_backup(db)
        db.commit()
        record_worker_success(
            "ops_maintenance",
            "Operational maintenance",
            started_at=started_at,
            next_run_at=next_run_at,
        )
        return {"deleted_notifications": deleted, "backup_path": backup_path}
    except Exception as e:
        db.rollback()
        logger.error("operational maintenance failed: %s", e, exc_info=True)
        record_worker_failure(
            "ops_maintenance",
            "Operational maintenance",
            e,
            started_at=started_at,
            next_run_at=next_run_at,
        )
        raise
    finally:
        db.close()


async def ops_maintenance_worker() -> None:
    logger.info("Operational maintenance worker started")
    while True:
        try:
            await run_ops_maintenance_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(60 * 60)
