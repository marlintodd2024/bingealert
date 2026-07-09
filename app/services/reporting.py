"""Admin digest and operations report helpers."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import (
    MediaRequest,
    Notification,
    NotificationDeliveryLog,
    ReportedIssue,
    ServiceHealthStatus,
    SessionLocal,
    WebhookEventLog,
    WorkerHealthStatus,
)
from app.security import clean_email_address, html_escape, sanitize_for_log
from app.services.admin_activity import record_admin_activity
from app.services.email_service import EmailService
from app.services.ops_health import collect_ops_health


WEBHOOK_PROBLEM_STATUSES = {"error", "failed", "replay_blocked"}
OPEN_ISSUE_STATUSES = {"reported", "fixing", "failed"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _count(query) -> int:
    return int(query.scalar() or 0)


def _date_key(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d") if dt else None


def _duration_hours(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    seconds = (end - start).total_seconds()
    if seconds < 0:
        return None
    return round(seconds / 3600, 1)


def _safe_days(days: int | str | None) -> int:
    try:
        value = int(days or 7)
    except (TypeError, ValueError):
        value = 7
    return max(1, min(value, 90))


def _blank_trend(start: datetime, end: datetime) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    current = start.date()
    final = end.date()
    while current <= final:
        rows[current.isoformat()] = {
            "date": current.isoformat(),
            "requests_created": 0,
            "requests_fulfilled": 0,
            "notifications_sent": 0,
            "notification_failures": 0,
            "issues_reported": 0,
            "webhook_failures": 0,
        }
        current += timedelta(days=1)
    return rows


def _bump(trend: dict[str, dict[str, int]], dt: datetime | None, key: str) -> None:
    day = _date_key(dt)
    if day in trend:
        trend[day][key] += 1


def _public_admin_url(tab: str = "health") -> str | None:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base or not base.startswith(("http://", "https://")):
        return None
    return f"{base}/admin#{tab}"


def _failure_key(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    return text[:180] or "No detail recorded"


def _add_failure(
    bucket: dict[tuple[str, str, str], dict[str, Any]],
    category: str,
    name: str,
    detail: str | None,
    *,
    count: int = 1,
    last_seen_at: datetime | None = None,
) -> None:
    safe_detail = _failure_key(detail)
    key = (category, name, safe_detail)
    row = bucket.setdefault(
        key,
        {
            "category": category,
            "name": name,
            "detail": safe_detail,
            "count": 0,
            "last_seen_at": None,
        },
    )
    row["count"] += int(count or 0)
    if last_seen_at and (not row["last_seen_at"] or last_seen_at > row["last_seen_at"]):
        row["last_seen_at"] = last_seen_at


def _top_waiting_requests(db: Session, now: datetime) -> tuple[int, list[dict[str, Any]]]:
    old_cutoff = now - timedelta(days=7)
    old_waiting_count = _count(
        db.query(func.count(MediaRequest.id)).filter(
            MediaRequest.status != "available",
            MediaRequest.created_at <= old_cutoff,
        ),
    )
    rows = (
        db.query(MediaRequest)
        .options(joinedload(MediaRequest.user))
        .filter(MediaRequest.status != "available")
        .order_by(MediaRequest.created_at.asc())
        .limit(12)
        .all()
    )
    waiting = []
    for row in rows:
        age_hours = _duration_hours(row.created_at, now)
        waiting.append(
            {
                "id": row.id,
                "title": row.title,
                "media_type": row.media_type,
                "status": row.status,
                "requester": row.user.username if row.user else None,
                "requester_email": row.user.email if row.user else None,
                "created_at": _iso(row.created_at),
                "age_days": round((age_hours or 0) / 24, 1),
            }
        )
    return old_waiting_count, waiting


async def build_ops_report(
    db: Session,
    *,
    days: int | str | None = 7,
    include_live_ops: bool = True,
) -> dict[str, Any]:
    """Build a reusable admin report payload for API responses and emails."""
    window_days = _safe_days(days)
    now = datetime.utcnow()
    start = now - timedelta(days=window_days)
    trend = _blank_trend(start, now)

    requests_created = (
        db.query(MediaRequest)
        .options(joinedload(MediaRequest.user))
        .filter(MediaRequest.created_at >= start, MediaRequest.created_at <= now)
        .all()
    )
    requests_fulfilled = (
        db.query(MediaRequest)
        .options(joinedload(MediaRequest.user))
        .filter(
            MediaRequest.status == "available",
            MediaRequest.updated_at >= start,
            MediaRequest.updated_at <= now,
        )
        .all()
    )
    for row in requests_created:
        _bump(trend, row.created_at, "requests_created")
    for row in requests_fulfilled:
        _bump(trend, row.updated_at, "requests_fulfilled")

    fulfillment_hours = [
        value
        for value in (_duration_hours(row.created_at, row.updated_at) for row in requests_fulfilled)
        if value is not None
    ]
    slow_fulfilled = [
        {
            "id": row.id,
            "title": row.title,
            "media_type": row.media_type,
            "requester": row.user.username if row.user else None,
            "fulfilled_at": _iso(row.updated_at),
            "hours": _duration_hours(row.created_at, row.updated_at),
        }
        for row in requests_fulfilled
        if (_duration_hours(row.created_at, row.updated_at) or 0) >= 72
    ]
    slow_fulfilled.sort(key=lambda item: item["hours"] or 0, reverse=True)

    delivery_rows = (
        db.query(NotificationDeliveryLog)
        .filter(
            NotificationDeliveryLog.sent_at != None,
            NotificationDeliveryLog.sent_at >= start,
            NotificationDeliveryLog.sent_at <= now,
        )
        .all()
    )
    notification_rows_sent = (
        db.query(Notification)
        .options(joinedload(Notification.user))
        .filter(Notification.sent == True, Notification.sent_at >= start, Notification.sent_at <= now)
        .all()
    )
    sent_count = len(delivery_rows) if delivery_rows else len(notification_rows_sent)
    sent_dates = [row.sent_at for row in delivery_rows] if delivery_rows else [row.sent_at for row in notification_rows_sent]
    for sent_at in sent_dates:
        _bump(trend, sent_at, "notifications_sent")

    notification_failures_window = (
        db.query(Notification)
        .filter(
            Notification.sent == False,
            Notification.error_message != None,
            Notification.error_message != "",
            Notification.created_at >= start,
            Notification.created_at <= now,
        )
        .all()
    )
    for row in notification_failures_window:
        _bump(trend, row.created_at, "notification_failures")

    pending_notifications = _count(
        db.query(func.count(Notification.id)).filter(Notification.sent == False),
    )
    failed_notifications = _count(
        db.query(func.count(Notification.id)).filter(
            Notification.sent == False,
            Notification.error_message != None,
            Notification.error_message != "",
        ),
    )

    issues_reported_rows = (
        db.query(ReportedIssue)
        .filter(ReportedIssue.created_at >= start, ReportedIssue.created_at <= now)
        .all()
    )
    for row in issues_reported_rows:
        _bump(trend, row.created_at, "issues_reported")
    issues_resolved = _count(
        db.query(func.count(ReportedIssue.id)).filter(
            ReportedIssue.resolved_at != None,
            ReportedIssue.resolved_at >= start,
            ReportedIssue.resolved_at <= now,
        ),
    )
    open_issues = _count(
        db.query(func.count(ReportedIssue.id)).filter(ReportedIssue.status.in_(OPEN_ISSUE_STATUSES)),
    )

    webhook_failures = (
        db.query(WebhookEventLog)
        .filter(
            WebhookEventLog.created_at >= start,
            WebhookEventLog.created_at <= now,
            WebhookEventLog.status.in_(WEBHOOK_PROBLEM_STATUSES),
        )
        .order_by(WebhookEventLog.created_at.desc())
        .limit(500)
        .all()
    )
    for row in webhook_failures:
        _bump(trend, row.created_at, "webhook_failures")

    services = db.query(ServiceHealthStatus).order_by(ServiceHealthStatus.service_name.asc()).all()
    workers = db.query(WorkerHealthStatus).order_by(WorkerHealthStatus.worker_name.asc()).all()
    unhealthy_services = [
        row for row in services
        if row.configured and row.status in {"degraded", "down", "error", "failed"}
    ]
    worker_errors = [row for row in workers if row.status in {"error", "failed"}]

    ops_snapshot: dict[str, Any] | None = None
    if include_live_ops:
        try:
            ops_snapshot = await collect_ops_health(db)
        except Exception as e:
            ops_snapshot = {
                "checked_at": _iso(now),
                "summary": {"collection_error": str(e)[:300]},
                "queue": [],
                "storage": [],
                "lag": [],
            }
    ops_summary = ops_snapshot.get("summary", {}) if ops_snapshot else {}

    failures: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in notification_failures_window:
        _add_failure(
            failures,
            "Notification",
            row.notification_type,
            row.error_message,
            last_seen_at=row.created_at,
        )
    for row in webhook_failures:
        _add_failure(
            failures,
            "Webhook",
            f"{row.source_service} {row.event_type}",
            row.error_message or row.result_message or row.status,
            last_seen_at=row.created_at,
        )
    for row in unhealthy_services:
        _add_failure(
            failures,
            "Service",
            row.service_name,
            row.last_error or row.status,
            count=max(1, row.consecutive_failures),
            last_seen_at=row.last_checked_at or row.updated_at,
        )
    for row in worker_errors:
        _add_failure(
            failures,
            "Worker",
            row.worker_name,
            row.last_error or row.status,
            count=max(1, row.failure_count),
            last_seen_at=row.updated_at,
        )
    for item in (ops_snapshot or {}).get("queue", [])[:60]:
        if item.get("severity") in {"warning", "error"}:
            _add_failure(
                failures,
                "Queue",
                item.get("service_name") or "Queue",
                f"{item.get('title')}: {item.get('reason')}",
                last_seen_at=now,
            )
    for row in (ops_snapshot or {}).get("storage", [])[:60]:
        if row.get("status") in {"warning", "error"}:
            _add_failure(
                failures,
                "Storage",
                row.get("service_name") or "Storage",
                f"{row.get('path')}: {row.get('reason')}",
                last_seen_at=now,
            )

    recurring_failures = list(failures.values())
    recurring_failures.sort(
        key=lambda item: (
            item["count"],
            item["last_seen_at"] or datetime.min,
        ),
        reverse=True,
    )
    for row in recurring_failures:
        row["last_seen_at"] = _iso(row["last_seen_at"])

    old_waiting_count, waiting_requests = _top_waiting_requests(db, now)
    requesters: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "user_id": None,
        "username": None,
        "email": None,
        "requests": 0,
        "fulfilled": 0,
        "notifications_sent": 0,
    })
    for row in requests_created:
        user_id = row.user_id
        requesters[user_id].update({
            "user_id": user_id,
            "username": row.user.username if row.user else None,
            "email": row.user.email if row.user else None,
        })
        requesters[user_id]["requests"] += 1
    for row in requests_fulfilled:
        user_id = row.user_id
        requesters[user_id].update({
            "user_id": user_id,
            "username": row.user.username if row.user else None,
            "email": row.user.email if row.user else None,
        })
        requesters[user_id]["fulfilled"] += 1
    for row in notification_rows_sent:
        user_id = row.user_id
        requesters[user_id].update({
            "user_id": user_id,
            "username": row.user.username if row.user else None,
            "email": row.user.email if row.user else None,
        })
        requesters[user_id]["notifications_sent"] += 1
    top_requesters = list(requesters.values())
    top_requesters.sort(
        key=lambda item: (item["requests"], item["notifications_sent"], item["fulfilled"]),
        reverse=True,
    )

    attempts = sent_count + len(notification_failures_window)
    summary = {
        "requests_created": len(requests_created),
        "requests_fulfilled": len(requests_fulfilled),
        "request_mix": {
            "movies": sum(1 for row in requests_created if row.media_type == "movie"),
            "tv": sum(1 for row in requests_created if row.media_type == "tv"),
        },
        "tracking_requests": _count(
            db.query(func.count(MediaRequest.id)).filter(MediaRequest.status != "available"),
        ),
        "old_waiting_requests": old_waiting_count,
        "slow_fulfilled_requests": len(slow_fulfilled),
        "fulfillment_avg_hours": round(mean(fulfillment_hours), 1) if fulfillment_hours else None,
        "fulfillment_median_hours": round(median(fulfillment_hours), 1) if fulfillment_hours else None,
        "notifications_sent": sent_count,
        "notification_failures_window": len(notification_failures_window),
        "notification_failed_pending": failed_notifications,
        "notification_pending": pending_notifications,
        "notification_success_rate": round((sent_count / attempts) * 100, 1) if attempts else None,
        "issues_reported": len(issues_reported_rows),
        "issues_resolved": issues_resolved,
        "open_issues": open_issues,
        "webhook_failures": len(webhook_failures),
        "unhealthy_services": len(unhealthy_services),
        "worker_errors": len(worker_errors),
        "queue_problems": int(ops_summary.get("queue_problems") or 0),
        "low_storage_paths": int(ops_summary.get("low_storage_paths") or 0),
        "lag_problems": int(ops_summary.get("lag_problems") or 0),
        "ops_collection_error": ops_summary.get("collection_error"),
    }
    summary["ops_problem_total"] = (
        summary["unhealthy_services"]
        + summary["worker_errors"]
        + summary["queue_problems"]
        + summary["low_storage_paths"]
        + summary["lag_problems"]
        + summary["webhook_failures"]
        + summary["notification_failed_pending"]
    )

    return {
        "generated_at": _iso(now),
        "window": {
            "days": window_days,
            "start": _iso(start),
            "end": _iso(now),
        },
        "summary": summary,
        "trend": list(trend.values()),
        "top_requesters": top_requesters[:10],
        "recurring_failures": recurring_failures[:12],
        "waiting_requests": waiting_requests,
        "slow_fulfilled": slow_fulfilled[:10],
        "unhealthy_services": [
            {
                "service_name": row.service_name,
                "status": row.status,
                "consecutive_failures": row.consecutive_failures,
                "last_error": row.last_error,
                "last_checked_at": _iso(row.last_checked_at),
            }
            for row in unhealthy_services
        ],
        "worker_errors": [
            {
                "worker_name": row.worker_name,
                "status": row.status,
                "failure_count": row.failure_count,
                "last_error": row.last_error,
                "updated_at": _iso(row.updated_at),
            }
            for row in worker_errors
        ],
        "ops_health": ops_snapshot,
        "links": {
            "admin": _public_admin_url("reports"),
            "health": _public_admin_url("health"),
            "notifications": _public_admin_url("notifications"),
            "issues": _public_admin_url("issues"),
        },
    }


def _format_count(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def _format_hours(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number < 1:
        return f"{max(1, round(number * 60))} min"
    return f"{number:.1f} h"


def _metric(label: str, value: Any, note: str = "") -> str:
    return f"""
        <td style="padding:14px;border:1px solid #ececec;border-radius:8px;background:#fafafa;">
            <div style="font-size:12px;color:#666;text-transform:uppercase;font-weight:700;">{html_escape(label)}</div>
            <div style="font-size:28px;color:#d49300;font-weight:800;line-height:1.1;margin-top:6px;">{html_escape(value)}</div>
            <div style="font-size:12px;color:#777;margin-top:4px;">{html_escape(note)}</div>
        </td>
    """


def _failure_rows(report: dict[str, Any]) -> str:
    rows = []
    for item in report.get("recurring_failures", [])[:8]:
        rows.append(
            f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #eee;">{html_escape(item.get("category"))}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;"><strong>{html_escape(item.get("name"))}</strong></td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{html_escape(item.get("count"))}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;color:#666;">{html_escape(item.get("detail"))}</td>
            </tr>
            """
        )
    if not rows:
        return '<tr><td colspan="4" style="padding:12px;color:#666;">No recurring failures in this window.</td></tr>'
    return "".join(rows)


def _waiting_rows(report: dict[str, Any]) -> str:
    rows = []
    for item in report.get("waiting_requests", [])[:8]:
        rows.append(
            f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #eee;"><strong>{html_escape(item.get("title"))}</strong></td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{html_escape(item.get("media_type"))}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{html_escape(item.get("status"))}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{html_escape(item.get("age_days"))} days</td>
            </tr>
            """
        )
    if not rows:
        return '<tr><td colspan="4" style="padding:12px;color:#666;">No waiting requests need attention.</td></tr>'
    return "".join(rows)


def render_admin_report_html(report: dict[str, Any], report_kind: str = "weekly") -> str:
    """Render a compact HTML report email."""
    summary = report.get("summary", {})
    window = report.get("window", {})
    title = "Daily Admin Digest" if report_kind == "daily" else "Weekly Operations Report"
    admin_link = report.get("links", {}).get("admin")
    link_html = (
        f'<a href="{html_escape(admin_link)}" style="display:inline-block;margin-top:14px;padding:10px 14px;background:#e5a00d;color:#161616;text-decoration:none;border-radius:6px;font-weight:700;">Open BingeAlert Reports</a>'
        if admin_link else ""
    )
    generated = report.get("generated_at") or ""

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background:#f3f4f6;color:#222;font-family:Arial,sans-serif;">
        <div style="max-width:920px;margin:0 auto;padding:24px;">
            <div style="background:#17191f;color:#fff;border-radius:8px 8px 0 0;padding:26px;">
                <h1 style="margin:0;font-size:26px;">BingeAlert {html_escape(title)}</h1>
                <p style="margin:8px 0 0;color:#c7c9d1;">{html_escape(window.get("days"))}-day window ending {html_escape(generated)}</p>
                {link_html}
            </div>
            <div style="background:#fff;border:1px solid #e5e7eb;border-top:0;border-radius:0 0 8px 8px;padding:22px;">
                <table role="presentation" width="100%" cellspacing="8" cellpadding="0" style="border-collapse:separate;">
                    <tr>
                        {_metric("Requests", _format_count(summary.get("requests_created")), "created")}
                        {_metric("Fulfilled", _format_count(summary.get("requests_fulfilled")), "requests available")}
                        {_metric("Median Fulfillment", _format_hours(summary.get("fulfillment_median_hours")), "request to available")}
                    </tr>
                    <tr>
                        {_metric("Notifications", _format_count(summary.get("notifications_sent")), "sent/delivered")}
                        {_metric("Open Issues", _format_count(summary.get("open_issues")), "reported/fixing/failed")}
                        {_metric("Ops Problems", _format_count(summary.get("ops_problem_total")), "health, queue, storage")}
                    </tr>
                </table>

                <h2 style="font-size:18px;margin:24px 0 10px;">Recurring Failures</h2>
                <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;border:1px solid #eee;">
                    <thead>
                        <tr style="background:#fafafa;">
                            <th align="left" style="padding:10px;">Type</th>
                            <th align="left" style="padding:10px;">Name</th>
                            <th align="left" style="padding:10px;">Count</th>
                            <th align="left" style="padding:10px;">Detail</th>
                        </tr>
                    </thead>
                    <tbody>{_failure_rows(report)}</tbody>
                </table>

                <h2 style="font-size:18px;margin:24px 0 10px;">Oldest Waiting Requests</h2>
                <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;border:1px solid #eee;">
                    <thead>
                        <tr style="background:#fafafa;">
                            <th align="left" style="padding:10px;">Title</th>
                            <th align="left" style="padding:10px;">Type</th>
                            <th align="left" style="padding:10px;">Status</th>
                            <th align="left" style="padding:10px;">Age</th>
                        </tr>
                    </thead>
                    <tbody>{_waiting_rows(report)}</tbody>
                </table>
                <p style="margin-top:22px;color:#777;font-size:12px;">Generated by BingeAlert. Use the Reports, Health, Notifications, and Issues tabs to drill into these numbers.</p>
            </div>
        </div>
    </body>
    </html>
    """


async def send_admin_report(report_kind: str = "weekly", *, days: int | None = None) -> dict[str, Any]:
    """Build and email an admin report with an explicitly closed DB session."""
    safe_kind = "daily" if report_kind == "daily" else "weekly"
    window_days = 1 if safe_kind == "daily" else _safe_days(days or 7)
    db = SessionLocal()
    try:
        report = await build_ops_report(db, days=window_days, include_live_ops=True)
        admin_email = clean_email_address(settings.admin_email or settings.smtp_from)
        if not admin_email:
            record_admin_activity(
                f"{safe_kind}_report",
                "Skipped admin report because no admin email is configured",
                status="warning",
                db=db,
            )
            db.commit()
            return {"sent": False, "message": "No admin email configured", "report": report}

        subject = (
            "BingeAlert daily admin digest"
            if safe_kind == "daily"
            else "BingeAlert weekly operations report"
        )
        html = render_admin_report_html(report, safe_kind)
        success = await EmailService().send_email(admin_email, subject, html)
        if not success:
            record_admin_activity(
                f"{safe_kind}_report",
                "Admin report email failed",
                status="error",
                details={"email": sanitize_for_log(admin_email), "days": window_days},
                db=db,
            )
            db.commit()
            return {"sent": False, "message": "Admin report email failed", "report": report}

        record_admin_activity(
            f"{safe_kind}_report",
            "Admin report email sent",
            details={"email": sanitize_for_log(admin_email), "days": window_days},
            db=db,
        )
        db.commit()
        return {"sent": True, "message": "Admin report email sent", "report": report}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
