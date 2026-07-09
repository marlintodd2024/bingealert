"""System and integration health checks.

This module keeps the latest service reachability and worker run state in the
database so the admin dashboard can show health without scraping logs. All DB
sessions opened here are explicitly closed because these helpers run outside
FastAPI request dependency cleanup.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import aiosmtplib
import httpx

from app.config import normalize_smtp_security, settings
from app.database import (
    ServiceHealthEvent,
    ServiceHealthStatus,
    SessionLocal,
    WorkerHealthStatus,
)
from app.security import clean_email_address, html_escape, normalize_http_url
from app.services.email_service import EmailService
from app.services.pushover_service import PushoverService


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _trim_error(value: object, limit: int = 500) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def _service_rows() -> list[dict[str, Any]]:
    rows = [
        {
            "key": "jellyseerr",
            "name": "Jellyseerr / Overseerr",
            "type": "seerr",
            "configured": bool(settings.jellyseerr_url and settings.jellyseerr_api_key),
            "url": settings.jellyseerr_url,
            "endpoint": "/api/v1/status",
            "headers": {"X-Api-Key": settings.jellyseerr_api_key or ""},
        },
        {
            "key": "sonarr",
            "name": "Sonarr",
            "type": "sonarr",
            "configured": bool(settings.sonarr_url and settings.sonarr_api_key),
            "url": settings.sonarr_url,
            "endpoint": "/api/v3/system/status",
            "headers": {"X-Api-Key": settings.sonarr_api_key or ""},
        },
        {
            "key": "radarr",
            "name": "Radarr",
            "type": "radarr",
            "configured": bool(settings.radarr_url and settings.radarr_api_key),
            "url": settings.radarr_url,
            "endpoint": "/api/v3/system/status",
            "headers": {"X-Api-Key": settings.radarr_api_key or ""},
        },
        {
            "key": "plex",
            "name": "Plex",
            "type": "plex",
            "configured": bool(settings.plex_url and settings.plex_token),
            "url": settings.plex_url,
            "endpoint": "/identity",
            "headers": {"X-Plex-Token": settings.plex_token or ""},
        },
        {
            "key": "smtp",
            "name": "SMTP",
            "type": "smtp",
            "configured": bool(settings.smtp_host and settings.smtp_from),
        },
    ]

    if settings.sonarr_anime_url or settings.sonarr_anime_api_key:
        rows.insert(
            3,
            {
                "key": "sonarr_anime",
                "name": "Sonarr Anime",
                "type": "sonarr",
                "configured": bool(settings.sonarr_anime_url and settings.sonarr_anime_api_key),
                "url": settings.sonarr_anime_url,
                "endpoint": "/api/v3/system/status",
                "headers": {"X-Api-Key": settings.sonarr_anime_api_key or ""},
            },
        )
    return rows


async def _check_http_service(spec: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    if not spec["configured"]:
        return {
            **spec,
            "ok": False,
            "status": "not_configured",
            "latency_ms": None,
            "error": "Service is not configured",
        }

    try:
        base_url = normalize_http_url(spec.get("url") or "")
        url = f"{base_url}{spec['endpoint']}"
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(url, headers=spec.get("headers") or {})
        elapsed_ms = int((time.monotonic() - started) * 1000)
        response.raise_for_status()
        return {
            **spec,
            "ok": True,
            "status": "ok",
            "latency_ms": elapsed_ms,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            **spec,
            "ok": False,
            "status": "down",
            "latency_ms": elapsed_ms,
            "error": _trim_error(e),
        }


async def _check_smtp_service(spec: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    if not spec["configured"]:
        return {
            **spec,
            "ok": False,
            "status": "not_configured",
            "latency_ms": None,
            "error": "SMTP is not configured",
        }

    smtp = None
    try:
        mode = normalize_smtp_security(settings.smtp_security)
        smtp = aiosmtplib.SMTP(
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            use_tls=mode == "ssl",
            start_tls=False,
            timeout=8,
        )
        await smtp.connect()
        if mode == "starttls":
            await smtp.starttls()
        if settings.smtp_user and settings.smtp_password and settings.smtp_user.lower() != "none":
            await smtp.login(settings.smtp_user, settings.smtp_password)
        await smtp.noop()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            **spec,
            "ok": True,
            "status": "ok",
            "latency_ms": elapsed_ms,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            **spec,
            "ok": False,
            "status": "down",
            "latency_ms": elapsed_ms,
            "error": _trim_error(e),
        }
    finally:
        if smtp:
            try:
                await smtp.quit()
            except Exception:
                pass


async def _check_service(spec: dict[str, Any]) -> dict[str, Any]:
    if spec["type"] == "smtp":
        return await _check_smtp_service(spec)
    return await _check_http_service(spec)


def _service_to_dict(row: ServiceHealthStatus) -> dict[str, Any]:
    return {
        "service_key": row.service_key,
        "service_name": row.service_name,
        "service_type": row.service_type,
        "configured": row.configured,
        "status": row.status,
        "latency_ms": row.latency_ms,
        "consecutive_failures": row.consecutive_failures,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
        "last_error": row.last_error,
        "alert_sent": row.alert_sent,
        "last_alert_at": row.last_alert_at.isoformat() if row.last_alert_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _worker_to_dict(row: WorkerHealthStatus) -> dict[str, Any]:
    return {
        "worker_key": row.worker_key,
        "worker_name": row.worker_name,
        "status": row.status,
        "last_started_at": row.last_started_at.isoformat() if row.last_started_at else None,
        "last_finished_at": row.last_finished_at.isoformat() if row.last_finished_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "last_duration_ms": row.last_duration_ms,
        "run_count": row.run_count,
        "failure_count": row.failure_count,
        "last_error": row.last_error,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _event_to_dict(row: ServiceHealthEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "service_key": row.service_key,
        "service_name": row.service_name,
        "service_type": row.service_type,
        "configured": row.configured,
        "status": row.status,
        "latency_ms": row.latency_ms,
        "consecutive_failures": row.consecutive_failures,
        "error": row.error,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
    }


async def _send_service_alert(kind: str, row_data: dict[str, Any]) -> bool:
    admin_email = clean_email_address(settings.admin_email or settings.smtp_from)
    if not admin_email:
        logger.warning("service health alert skipped: no valid admin email")
        return False

    is_recovery = kind == "recovery"
    title = (
        f"{row_data['service_name']} is reachable again"
        if is_recovery
        else f"{row_data['service_name']} is unreachable"
    )
    color = "#4caf50" if is_recovery else "#f44336"
    error = row_data.get("last_error") or "No error detail"
    checked = row_data.get("last_checked_at") or "Unknown"
    failures = row_data.get("consecutive_failures") or 0

    html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#f5f5f5;font-family:Arial,sans-serif;color:#333;">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="padding:24px;background:{color};color:#fff;">
      <h1 style="margin:0;font-size:22px;">{html_escape(title)}</h1>
    </div>
    <div style="padding:24px;">
      <p><strong>Service:</strong> {html_escape(row_data['service_name'])}</p>
      <p><strong>Status:</strong> {html_escape(row_data['status'])}</p>
      <p><strong>Last checked:</strong> {html_escape(checked)}</p>
      <p><strong>Consecutive failures:</strong> {html_escape(failures)}</p>
      <p><strong>Last error:</strong> {html_escape(error)}</p>
      <p style="color:#777;font-size:13px;">BingeAlert will keep checking this service automatically.</p>
    </div>
  </div>
</body>
</html>
"""
    email = EmailService()
    return await email.send_email(
        to_email=admin_email,
        subject=f"BingeAlert service alert: {title}",
        html_body=html,
    )


async def _send_webhook_alert(kind: str, row_data: dict[str, Any]) -> bool:
    if not settings.alert_webhook_enabled:
        return False

    is_recovery = kind == "recovery"
    title = (
        f"{row_data['service_name']} is reachable again"
        if is_recovery
        else f"{row_data['service_name']} is unreachable"
    )
    text = (
        f"BingeAlert service alert: {title}\n"
        f"Status: {row_data.get('status')}\n"
        f"Checked: {row_data.get('last_checked_at') or 'unknown'}\n"
        f"Failures: {row_data.get('consecutive_failures') or 0}\n"
        f"Error: {row_data.get('last_error') or 'none'}"
    )
    webhook_type = (settings.alert_webhook_type or "generic").strip().lower()
    if webhook_type == "pushover":
        return await PushoverService().send_service_health(kind, row_data)

    if not settings.alert_webhook_url:
        return False

    if webhook_type == "discord":
        payload = {
            "content": text,
            "embeds": [
                {
                    "title": title,
                    "color": 5763719 if is_recovery else 15548997,
                    "fields": [
                        {"name": "Service", "value": str(row_data["service_name"]), "inline": True},
                        {"name": "Status", "value": str(row_data.get("status")), "inline": True},
                        {"name": "Failures", "value": str(row_data.get("consecutive_failures") or 0), "inline": True},
                    ],
                }
            ],
        }
    elif webhook_type == "slack":
        payload = {"text": text}
    else:
        payload = {"event": "service_health", "kind": kind, "title": title, "service": row_data}

    url = normalize_http_url(settings.alert_webhook_url)
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.post(url, json=payload)
    response.raise_for_status()
    return True


def _can_email_alert_service(row_data: dict[str, Any]) -> bool:
    return row_data.get("service_key") != "smtp" and row_data.get("service_type") != "smtp"


async def _send_alert_channels(kind: str, row_data: dict[str, Any]) -> None:
    if settings.service_health_email_alerts_enabled:
        if _can_email_alert_service(row_data):
            await _send_service_alert(kind, row_data)
        else:
            logger.info(
                "service health email alert skipped for %s because SMTP is the email transport",
                row_data.get("service_key"),
            )
    webhook_type = (settings.alert_webhook_type or "generic").strip().lower()
    if settings.alert_webhook_enabled and (settings.alert_webhook_url or webhook_type == "pushover"):
        await _send_webhook_alert(kind, row_data)


def _upsert_results(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    now = _utcnow()
    failure_threshold = max(1, int(settings.service_health_failure_threshold or 1))
    cooldown = timedelta(minutes=max(1, int(settings.service_health_alert_cooldown_minutes or 1)))
    outage_alerts: list[dict[str, Any]] = []
    recovery_alerts: list[dict[str, Any]] = []

    db = SessionLocal()
    try:
        for result in results:
            row = db.query(ServiceHealthStatus).filter(
                ServiceHealthStatus.service_key == result["key"]
            ).first()
            if not row:
                row = ServiceHealthStatus(
                    service_key=result["key"],
                    service_name=result["name"],
                    service_type=result["type"],
                )
                db.add(row)

            was_alerted = bool(row.alert_sent)
            previous_status = row.status

            row.service_name = result["name"]
            row.service_type = result["type"]
            row.configured = bool(result["configured"])
            row.last_checked_at = now
            row.latency_ms = result.get("latency_ms")
            row.updated_at = now

            if result["ok"]:
                row.status = "ok"
                row.consecutive_failures = 0
                row.last_ok_at = now
                row.last_error = None
                if was_alerted:
                    row.alert_sent = False
                    recovery_alerts.append(_service_to_dict(row))
            elif not row.configured:
                row.status = "not_configured"
                row.consecutive_failures = 0
                row.last_error = result.get("error")
            else:
                row.consecutive_failures = int(row.consecutive_failures or 0) + 1
                row.status = "down" if row.consecutive_failures >= failure_threshold else "degraded"
                row.last_error = result.get("error")
                cooldown_elapsed = not row.last_alert_at or (now - row.last_alert_at) >= cooldown
                if row.consecutive_failures >= failure_threshold and cooldown_elapsed:
                    row.alert_sent = True
                    row.last_alert_at = now
                    outage_alerts.append(_service_to_dict(row))

            db.add(
                ServiceHealthEvent(
                    service_key=row.service_key,
                    service_name=row.service_name,
                    service_type=row.service_type,
                    configured=row.configured,
                    status=row.status,
                    latency_ms=row.latency_ms,
                    consecutive_failures=row.consecutive_failures,
                    error=row.last_error,
                    checked_at=now,
                )
            )

            if previous_status != row.status:
                logger.info(
                    "service health changed: %s %s -> %s",
                    row.service_key,
                    previous_status,
                    row.status,
                )

        history_days = max(1, int(settings.service_health_history_days or 14))
        db.query(ServiceHealthEvent).filter(
            ServiceHealthEvent.checked_at < now - timedelta(days=history_days)
        ).delete(synchronize_session=False)

        db.commit()
        return outage_alerts, recovery_alerts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_service_health_checks(send_alerts: bool = True) -> dict[str, Any]:
    specs = _service_rows()
    results = await asyncio.gather(*[_check_service(spec) for spec in specs])
    outage_alerts, recovery_alerts = _upsert_results(list(results))

    if send_alerts:
        for row_data in outage_alerts:
            try:
                await _send_alert_channels("outage", row_data)
            except Exception as e:
                logger.error("service outage alert failed for %s: %s", row_data["service_key"], e)
        for row_data in recovery_alerts:
            try:
                await _send_alert_channels("recovery", row_data)
            except Exception as e:
                logger.error("service recovery alert failed for %s: %s", row_data["service_key"], e)

    return get_system_health_snapshot()


def get_system_health_snapshot() -> dict[str, Any]:
    db = SessionLocal()
    try:
        services = (
            db.query(ServiceHealthStatus)
            .order_by(ServiceHealthStatus.service_name.asc())
            .all()
        )
        workers = (
            db.query(WorkerHealthStatus)
            .order_by(WorkerHealthStatus.worker_name.asc())
            .all()
        )
        since = _utcnow() - timedelta(hours=24)
        recent_events = (
            db.query(ServiceHealthEvent)
            .filter(ServiceHealthEvent.checked_at >= since)
            .order_by(ServiceHealthEvent.checked_at.desc())
            .all()
        )
        metrics: dict[str, dict[str, Any]] = {}
        for event in recent_events:
            bucket = metrics.setdefault(
                event.service_key,
                {"checks_24h": 0, "ok_24h": 0, "failures_24h": 0, "uptime_24h": None},
            )
            if not event.configured:
                continue
            bucket["checks_24h"] += 1
            if event.status == "ok":
                bucket["ok_24h"] += 1
            elif event.status in {"degraded", "down"}:
                bucket["failures_24h"] += 1
        for bucket in metrics.values():
            checks = bucket["checks_24h"]
            bucket["uptime_24h"] = round((bucket["ok_24h"] / checks) * 100, 1) if checks else None

        unhealthy = sum(
            1 for service in services
            if service.configured and service.status in {"degraded", "down"}
        )
        service_rows = []
        for row in services:
            service_data = _service_to_dict(row)
            service_data.update(metrics.get(row.service_key, {
                "checks_24h": 0,
                "ok_24h": 0,
                "failures_24h": 0,
                "uptime_24h": None,
            }))
            service_rows.append(service_data)
        return {
            "services": service_rows,
            "workers": [_worker_to_dict(row) for row in workers],
            "history": [_event_to_dict(row) for row in recent_events[:50]],
            "unhealthy_services": unhealthy,
            "settings": {
                "enabled": settings.service_health_enabled,
                "interval_minutes": settings.service_health_interval_minutes,
                "failure_threshold": settings.service_health_failure_threshold,
                "alert_cooldown_minutes": settings.service_health_alert_cooldown_minutes,
                "email_alerts_enabled": settings.service_health_email_alerts_enabled,
                "history_days": settings.service_health_history_days,
                "webhook_alerts_enabled": settings.alert_webhook_enabled,
                "webhook_type": settings.alert_webhook_type,
            },
            "checked_at": _utcnow().isoformat(),
        }
    finally:
        db.close()


def get_service_health_history(hours: int = 24, limit: int = 200) -> dict[str, Any]:
    hours = max(1, min(int(hours or 24), 24 * 30))
    limit = max(1, min(int(limit or 200), 1000))
    since = _utcnow() - timedelta(hours=hours)
    db = SessionLocal()
    try:
        events = (
            db.query(ServiceHealthEvent)
            .filter(ServiceHealthEvent.checked_at >= since)
            .order_by(ServiceHealthEvent.checked_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "events": [_event_to_dict(row) for row in events],
            "hours": hours,
            "limit": limit,
        }
    finally:
        db.close()


def record_worker_started(worker_key: str, worker_name: str, next_run_at: datetime | None = None) -> datetime:
    started_at = _utcnow()
    db = SessionLocal()
    try:
        row = db.query(WorkerHealthStatus).filter(
            WorkerHealthStatus.worker_key == worker_key
        ).first()
        if not row:
            row = WorkerHealthStatus(worker_key=worker_key, worker_name=worker_name)
            db.add(row)
        row.worker_name = worker_name
        row.status = "running"
        row.last_started_at = started_at
        row.next_run_at = next_run_at
        row.updated_at = started_at
        db.commit()
        return started_at
    except Exception:
        db.rollback()
        logger.debug("failed recording worker start for %s", worker_key, exc_info=True)
        return started_at
    finally:
        db.close()


def record_worker_success(
    worker_key: str,
    worker_name: str,
    started_at: datetime | None = None,
    next_run_at: datetime | None = None,
) -> None:
    now = _utcnow()
    duration_ms = None
    if started_at:
        duration_ms = max(0, int((now - started_at).total_seconds() * 1000))
    db = SessionLocal()
    try:
        row = db.query(WorkerHealthStatus).filter(
            WorkerHealthStatus.worker_key == worker_key
        ).first()
        if not row:
            row = WorkerHealthStatus(worker_key=worker_key, worker_name=worker_name)
            db.add(row)
        row.worker_name = worker_name
        row.status = "ok"
        row.last_finished_at = now
        row.last_success_at = now
        row.next_run_at = next_run_at
        row.last_duration_ms = duration_ms
        row.run_count = int(row.run_count or 0) + 1
        row.last_error = None
        row.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        logger.debug("failed recording worker success for %s", worker_key, exc_info=True)
    finally:
        db.close()


def record_worker_failure(
    worker_key: str,
    worker_name: str,
    error: object,
    started_at: datetime | None = None,
    next_run_at: datetime | None = None,
) -> None:
    now = _utcnow()
    duration_ms = None
    if started_at:
        duration_ms = max(0, int((now - started_at).total_seconds() * 1000))
    db = SessionLocal()
    try:
        row = db.query(WorkerHealthStatus).filter(
            WorkerHealthStatus.worker_key == worker_key
        ).first()
        if not row:
            row = WorkerHealthStatus(worker_key=worker_key, worker_name=worker_name)
            db.add(row)
        row.worker_name = worker_name
        row.status = "error"
        row.last_finished_at = now
        row.next_run_at = next_run_at
        row.last_duration_ms = duration_ms
        row.failure_count = int(row.failure_count or 0) + 1
        row.last_error = _trim_error(error)
        row.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        logger.debug("failed recording worker failure for %s", worker_key, exc_info=True)
    finally:
        db.close()


async def system_health_worker() -> None:
    logger.info("System health worker started")

    while True:
        interval_minutes = max(1, int(settings.service_health_interval_minutes or 15))
        next_run_at = _utcnow() + timedelta(minutes=interval_minutes)
        started_at = record_worker_started(
            "system_health",
            "System health checks",
            next_run_at=next_run_at,
        )
        try:
            if settings.service_health_enabled:
                await run_service_health_checks(send_alerts=True)
            else:
                logger.debug("service health checks are disabled")
            record_worker_success(
                "system_health",
                "System health checks",
                started_at=started_at,
                next_run_at=next_run_at,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("system health worker error: %s", e)
            record_worker_failure(
                "system_health",
                "System health checks",
                e,
                started_at=started_at,
                next_run_at=next_run_at,
            )

        await asyncio.sleep(interval_minutes * 60)
