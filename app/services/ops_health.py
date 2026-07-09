"""Operational queue, storage, and notification-lag diagnostics."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import EpisodeTracking, MediaRequest, Notification, NotificationDeliveryLog
from app.security import normalize_http_url
from app.services.radarr_service import RadarrService
from app.services.sonarr_service import SonarrService


logger = logging.getLogger(__name__)

GIB = 1024 ** 3
LOW_SPACE_WARNING_BYTES = 50 * GIB
LOW_SPACE_CRITICAL_BYTES = 20 * GIB
LOW_SPACE_WARNING_PERCENT = 10
LOW_SPACE_CRITICAL_PERCENT = 5
QUEUE_STUCK_HOURS = 4
NOTIFICATION_OVERDUE_MINUTES = 15


def _utcnow() -> datetime:
    return datetime.utcnow()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _coerce_bytes(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _first_present(data: dict[str, Any], names: list[str], default: Any = None) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def _flatten_status_messages(item: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for entry in item.get("statusMessages") or []:
        raw = entry.get("messages") if isinstance(entry, dict) else entry
        if isinstance(raw, list):
            messages.extend(str(message) for message in raw if message)
        elif raw:
            messages.append(str(raw))
    return messages


def _is_import_failure(messages: list[str]) -> bool:
    patterns = [
        "no files found are eligible for import",
        "not eligible for import",
        "has already been imported",
        "manual import required",
        "matched to movie by id",
        "matched to series by id",
        "unable to import automatically",
    ]
    text = " ".join(messages).lower()
    return any(pattern in text for pattern in patterns)


def _classify_queue_item(item: dict[str, Any], now: datetime) -> dict[str, Any]:
    status = str(item.get("status") or "").lower()
    tracked_status = str(item.get("trackedDownloadStatus") or "").lower()
    tracked_state = str(item.get("trackedDownloadState") or "").lower()
    messages = _flatten_status_messages(item)
    message_text = " ".join(messages).lower()
    added_at = _parse_datetime(item.get("added"))
    age_hours = ((now - added_at).total_seconds() / 3600) if added_at else None
    size = _coerce_bytes(item.get("size")) or 0
    size_left = _coerce_bytes(_first_present(item, ["sizeleft", "sizeLeft"], 0)) or 0
    progress = round(max(0, min(100, (1 - (size_left / size)) * 100)), 1) if size else None

    if _is_import_failure(messages) or (tracked_state == "importpending" and tracked_status == "warning"):
        return {
            "severity": "error",
            "issue_type": "import_failure",
            "reason": messages[0] if messages else "Unable to import automatically",
            "action": "Manual import, blocklist, or search for a different release.",
            "age_hours": age_hours,
            "progress_percent": progress,
            "messages": messages,
        }

    if any(token in message_text for token in ["no seeders", "not enough seeders", "no seeds"]):
        return {
            "severity": "warning",
            "issue_type": "no_seeders",
            "reason": messages[0] if messages else "Release has no seeders.",
            "action": "Review indexer/download-client availability or search another release.",
            "age_hours": age_hours,
            "progress_percent": progress,
            "messages": messages,
        }

    if "download client" in message_text and any(token in message_text for token in ["unavailable", "error", "failed"]):
        return {
            "severity": "error",
            "issue_type": "download_client",
            "reason": messages[0] if messages else "Download client error.",
            "action": "Check the download client connection and category/path mapping.",
            "age_hours": age_hours,
            "progress_percent": progress,
            "messages": messages,
        }

    if status in {"failed", "stalled"}:
        return {
            "severity": "error" if status == "failed" else "warning",
            "issue_type": status,
            "reason": messages[0] if messages else f"Queue item is {status}.",
            "action": "Open the source service and inspect the queue item.",
            "age_hours": age_hours,
            "progress_percent": progress,
            "messages": messages,
        }

    if status == "warning" or tracked_status == "warning":
        return {
            "severity": "warning",
            "issue_type": "warning",
            "reason": messages[0] if messages else "Queue item is warning.",
            "action": "Review the queue warning before it becomes a missed import.",
            "age_hours": age_hours,
            "progress_percent": progress,
            "messages": messages,
        }

    if age_hours is not None and age_hours > QUEUE_STUCK_HOURS and size > 0 and progress not in {100, 100.0}:
        return {
            "severity": "warning",
            "issue_type": "slow_queue",
            "reason": f"In queue for {age_hours:.1f} hours.",
            "action": "Check whether the download is stalled or waiting on peers.",
            "age_hours": age_hours,
            "progress_percent": progress,
            "messages": messages,
        }

    return {
        "severity": "ok",
        "issue_type": "healthy",
        "reason": messages[0] if messages else "Queue item is moving normally.",
        "action": "",
        "age_hours": age_hours,
        "progress_percent": progress,
        "messages": messages,
    }


def _queue_item_to_dict(
    service_key: str,
    service_name: str,
    service_type: str,
    service_url: str,
    item: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    classification = _classify_queue_item(item, now)
    series = item.get("series") if isinstance(item.get("series"), dict) else {}
    movie = item.get("movie") if isinstance(item.get("movie"), dict) else {}
    episode = item.get("episode") if isinstance(item.get("episode"), dict) else {}
    quality_raw = item.get("quality") or {}
    quality_name = None
    if isinstance(quality_raw, dict):
        nested_quality = quality_raw.get("quality") if isinstance(quality_raw.get("quality"), dict) else {}
        quality_name = nested_quality.get("name") or quality_raw.get("name")
    elif quality_raw:
        quality_name = str(quality_raw)
    title = item.get("title") or movie.get("title") or series.get("title") or "Unknown"
    if service_type == "sonarr" and episode:
        season = episode.get("seasonNumber")
        episode_num = episode.get("episodeNumber")
        if season is not None and episode_num is not None:
            title = f"{series.get('title') or title} S{int(season):02d}E{int(episode_num):02d}"

    return {
        "service_key": service_key,
        "service_name": service_name,
        "service_type": service_type,
        "service_url": service_url,
        "queue_id": item.get("id"),
        "title": title,
        "status": item.get("status") or "unknown",
        "tracked_status": item.get("trackedDownloadStatus"),
        "tracked_state": item.get("trackedDownloadState"),
        "protocol": item.get("protocol"),
        "download_client": item.get("downloadClient"),
        "quality": quality_name,
        "added_at": item.get("added"),
        "age_hours": classification["age_hours"],
        "size_bytes": _coerce_bytes(item.get("size")),
        "size_left_bytes": _coerce_bytes(_first_present(item, ["sizeleft", "sizeLeft"])),
        "progress_percent": classification["progress_percent"],
        "severity": classification["severity"],
        "issue_type": classification["issue_type"],
        "reason": classification["reason"],
        "action": classification["action"],
        "messages": classification["messages"],
    }


def _storage_status(free_bytes: int | None, total_bytes: int | None) -> tuple[str, float | None, str]:
    if free_bytes is None:
        return "unknown", None, "Free-space data is not available."

    free_percent = None
    if total_bytes:
        free_percent = round((free_bytes / total_bytes) * 100, 1)

    if free_bytes <= LOW_SPACE_CRITICAL_BYTES or (free_percent is not None and free_percent <= LOW_SPACE_CRITICAL_PERCENT):
        return "error", free_percent, "Storage is critically low."
    if free_bytes <= LOW_SPACE_WARNING_BYTES or (free_percent is not None and free_percent <= LOW_SPACE_WARNING_PERCENT):
        return "warning", free_percent, "Storage is getting low."
    return "ok", free_percent, "Storage has comfortable free space."


def _closest_disk(path: str, disks: list[dict[str, Any]]) -> dict[str, Any]:
    if not path:
        return {}
    normalized = path.rstrip("/\\").lower()
    best: dict[str, Any] = {}
    best_len = -1
    for disk in disks:
        disk_path = str(disk.get("path") or "").rstrip("/\\").lower()
        if disk_path and normalized.startswith(disk_path) and len(disk_path) > best_len:
            best = disk
            best_len = len(disk_path)
    return best


def _storage_row(
    service_key: str,
    service_name: str,
    service_type: str,
    row_type: str,
    data: dict[str, Any],
    disks: list[dict[str, Any]],
) -> dict[str, Any]:
    path = data.get("path") or data.get("label") or "Unknown"
    disk = _closest_disk(path, disks)
    free_bytes = _coerce_bytes(_first_present(data, ["freeSpace", "freeSpaceBytes", "availableSpace"]))
    total_bytes = _coerce_bytes(_first_present(data, ["totalSpace", "totalSpaceBytes"]))
    if free_bytes is None:
        free_bytes = _coerce_bytes(_first_present(disk, ["freeSpace", "freeSpaceBytes", "availableSpace"]))
    if total_bytes is None:
        total_bytes = _coerce_bytes(_first_present(disk, ["totalSpace", "totalSpaceBytes"]))

    status, free_percent, reason = _storage_status(free_bytes, total_bytes)
    unmapped = data.get("unmappedFolders") or []
    accessible = data.get("accessible")
    if accessible is False:
        status = "error"
        reason = "Root folder is not accessible."

    return {
        "service_key": service_key,
        "service_name": service_name,
        "service_type": service_type,
        "row_type": row_type,
        "path": path,
        "label": data.get("label") or "",
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "free_percent": free_percent,
        "status": status,
        "reason": reason,
        "accessible": accessible,
        "unmapped_folders": len(unmapped) if isinstance(unmapped, list) else 0,
    }


async def _fetch_queue(service: Any, spec: dict[str, Any], now: datetime) -> dict[str, Any]:
    try:
        payload = await service._get("/queue?page=1&pageSize=500")
        records = payload.get("records", []) if isinstance(payload, dict) else payload
        records = records if isinstance(records, list) else []
        items = [
            _queue_item_to_dict(
                spec["key"],
                spec["name"],
                spec["type"],
                spec["url"],
                item,
                now,
            )
            for item in records
            if isinstance(item, dict)
        ]
        total_records = payload.get("totalRecords") if isinstance(payload, dict) else None
        return {
            "service_key": spec["key"],
            "service_name": spec["name"],
            "status": "ok",
            "queue_count": int(total_records if total_records is not None else len(items)),
            "items": items,
            "error": None,
        }
    except Exception as e:
        logger.warning("failed to fetch %s queue: %s", spec["name"], e)
        return {
            "service_key": spec["key"],
            "service_name": spec["name"],
            "status": "error",
            "queue_count": 0,
            "items": [],
            "error": str(e)[:500],
        }


async def _fetch_storage(service: Any, spec: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    disks: list[dict[str, Any]] = []
    try:
        raw_disks = await service._get("/diskspace")
        if isinstance(raw_disks, list):
            disks = [disk for disk in raw_disks if isinstance(disk, dict)]
            rows.extend(
                _storage_row(spec["key"], spec["name"], spec["type"], "disk", disk, disks)
                for disk in disks
            )
    except Exception as e:
        errors.append(f"diskspace: {str(e)[:240]}")

    try:
        raw_roots = await service._get("/rootfolder")
        roots = [root for root in raw_roots if isinstance(root, dict)] if isinstance(raw_roots, list) else []
        rows.extend(
            _storage_row(spec["key"], spec["name"], spec["type"], "root_folder", root, disks)
            for root in roots
        )
    except Exception as e:
        errors.append(f"rootfolder: {str(e)[:240]}")

    return {
        "service_key": spec["key"],
        "service_name": spec["name"],
        "status": "error" if errors and not rows else "ok",
        "rows": rows,
        "error": "; ".join(errors) or None,
    }


async def _inspect_service(spec: dict[str, Any]) -> dict[str, Any]:
    now = _utcnow()
    if spec["type"] == "sonarr":
        service = SonarrService(
            base_url=spec["url"],
            api_key=spec["api_key"],
            instance_name=spec["name"],
        )
    else:
        service = RadarrService()

    queue_result, storage_result = await asyncio.gather(
        _fetch_queue(service, spec, now),
        _fetch_storage(service, spec),
    )
    queue_items = queue_result["items"]
    storage_rows = storage_result["rows"]
    queue_problems = [item for item in queue_items if item["severity"] in {"warning", "error"}]
    low_storage = [row for row in storage_rows if row["status"] in {"warning", "error"}]
    status = "ok"
    if queue_result["status"] == "error" or storage_result["status"] == "error":
        status = "error"
    elif any(item["severity"] == "error" for item in queue_items) or any(row["status"] == "error" for row in storage_rows):
        status = "error"
    elif queue_problems or low_storage:
        status = "warning"

    return {
        "service_key": spec["key"],
        "service_name": spec["name"],
        "service_type": spec["type"],
        "service_url": spec["url"],
        "status": status,
        "queue": queue_result,
        "storage": storage_result,
        "queue_problem_count": len(queue_problems),
        "low_storage_count": len(low_storage),
        "error": queue_result.get("error") or storage_result.get("error"),
    }


def _configured_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if settings.sonarr_url and settings.sonarr_api_key:
        specs.append({
            "key": "sonarr",
            "name": "Sonarr",
            "type": "sonarr",
            "url": normalize_http_url(settings.sonarr_url),
            "api_key": settings.sonarr_api_key,
        })
    if settings.sonarr_anime_url and settings.sonarr_anime_api_key:
        specs.append({
            "key": "sonarr_anime",
            "name": "Sonarr Anime",
            "type": "sonarr",
            "url": normalize_http_url(settings.sonarr_anime_url),
            "api_key": settings.sonarr_anime_api_key,
        })
    if settings.radarr_url and settings.radarr_api_key:
        specs.append({
            "key": "radarr",
            "name": "Radarr",
            "type": "radarr",
            "url": normalize_http_url(settings.radarr_url),
            "api_key": settings.radarr_api_key,
        })
    return specs


def _notification_state(notification: Notification | None, now: datetime) -> tuple[str, str, str]:
    if not notification:
        return "warning", "missing_notification", "No queued or sent notification row was found."
    if notification.error_message:
        return "error", "notification_failed", notification.error_message[:500]
    if notification.sent:
        return "ok", "sent", "Notification was sent."
    send_after = _parse_datetime(notification.send_after)
    if send_after and send_after > now:
        return "info", "queued", f"Queued for {send_after.isoformat()}."
    overdue_cutoff = now - timedelta(minutes=NOTIFICATION_OVERDUE_MINUTES)
    if send_after and send_after <= overdue_cutoff:
        return "warning", "notification_overdue", "Notification is ready but still pending."
    return "info", "ready_to_send", "Notification is ready for the processor."


def _build_notification_index(db: Session, request_ids: list[int]) -> dict[int, list[Notification]]:
    if not request_ids:
        return {}
    rows = (
        db.query(Notification)
        .filter(Notification.request_id.in_(request_ids))
        .order_by(Notification.created_at.desc())
        .all()
    )
    by_request: dict[int, list[Notification]] = {}
    for row in rows:
        by_request.setdefault(row.request_id, []).append(row)
    return by_request


def _has_delivery(
    db: Session,
    request_id: int,
    notification_type: str,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> bool:
    query = db.query(NotificationDeliveryLog.id).filter(
        NotificationDeliveryLog.request_id == request_id,
        NotificationDeliveryLog.notification_type == notification_type,
    )
    if season_number is not None:
        query = query.filter(NotificationDeliveryLog.season_number == season_number)
    if episode_number is not None:
        query = query.filter(NotificationDeliveryLog.episode_number == episode_number)
    return query.first() is not None


def _collect_import_lag(db: Session) -> list[dict[str, Any]]:
    now = _utcnow()
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, int | None, int | None]] = set()

    failed_notifications = (
        db.query(Notification)
        .filter(Notification.sent == False, Notification.error_message != None, Notification.error_message != "")
        .order_by(Notification.created_at.desc())
        .limit(30)
        .all()
    )
    for notification in failed_notifications:
        request = notification.request
        key = ("notification", notification.id, None, None)
        seen.add(key)
        items.append({
            "severity": "error",
            "state": "notification_failed",
            "title": request.title if request else notification.subject,
            "media_type": request.media_type if request else notification.notification_type,
            "request_id": notification.request_id,
            "notification_id": notification.id,
            "detail": notification.error_message,
            "updated_at": notification.created_at.isoformat() if notification.created_at else None,
        })

    trackings = (
        db.query(EpisodeTracking)
        .join(MediaRequest)
        .filter(EpisodeTracking.available_in_plex == True, EpisodeTracking.notified == False)
        .order_by(EpisodeTracking.created_at.desc())
        .limit(50)
        .all()
    )
    notifications_by_request = _build_notification_index(db, sorted({row.request_id for row in trackings}))
    for row in trackings:
        if _has_delivery(db, row.request_id, "episode", row.season_number, row.episode_number):
            continue
        request = row.request
        episode_code = f"S{int(row.season_number):02d}E{int(row.episode_number):02d}"
        matching = [
            notification for notification in notifications_by_request.get(row.request_id, [])
            if notification.notification_type == "episode" and episode_code in (notification.subject or "")
        ]
        notification = matching[0] if matching else None
        severity, state, detail = _notification_state(notification, now)
        if state == "sent":
            continue
        key = ("episode", row.request_id, row.season_number, row.episode_number)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "severity": severity,
            "state": state,
            "title": f"{request.title if request else 'TV'} {episode_code}",
            "media_type": "tv",
            "request_id": row.request_id,
            "notification_id": notification.id if notification else None,
            "detail": detail,
            "updated_at": row.created_at.isoformat() if row.created_at else None,
        })

    movies = (
        db.query(MediaRequest)
        .filter(MediaRequest.media_type == "movie", MediaRequest.status == "available")
        .order_by(MediaRequest.updated_at.desc())
        .limit(50)
        .all()
    )
    notifications_by_request = _build_notification_index(db, [row.id for row in movies])
    for request in movies:
        if _has_delivery(db, request.id, "movie"):
            continue
        movie_notifications = [
            notification for notification in notifications_by_request.get(request.id, [])
            if notification.notification_type == "movie"
        ]
        notification = movie_notifications[0] if movie_notifications else None
        severity, state, detail = _notification_state(notification, now)
        if state == "sent":
            continue
        key = ("movie", request.id, None, None)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "severity": severity,
            "state": state,
            "title": request.title,
            "media_type": "movie",
            "request_id": request.id,
            "notification_id": notification.id if notification else None,
            "detail": detail,
            "updated_at": request.updated_at.isoformat() if request.updated_at else None,
        })

    severity_rank = {"error": 0, "warning": 1, "info": 2, "ok": 3}
    items.sort(key=lambda item: (severity_rank.get(item["severity"], 9), item.get("updated_at") or ""), reverse=False)
    return items[:75]


async def collect_ops_health(db: Session) -> dict[str, Any]:
    specs = _configured_specs()
    service_results = await asyncio.gather(*[_inspect_service(spec) for spec in specs]) if specs else []
    queue_items = [item for service in service_results for item in service["queue"]["items"]]
    storage_rows = [row for service in service_results for row in service["storage"]["rows"]]
    lag_items = _collect_import_lag(db)

    queue_problems = [item for item in queue_items if item["severity"] in {"warning", "error"}]
    low_storage = [row for row in storage_rows if row["status"] in {"warning", "error"}]
    lag_problems = [item for item in lag_items if item["severity"] in {"warning", "error"}]

    return {
        "checked_at": _utcnow().isoformat(),
        "services": service_results,
        "queue": queue_items,
        "storage": storage_rows,
        "lag": lag_items,
        "summary": {
            "configured_services": len(specs),
            "queue_items": len(queue_items),
            "queue_problems": len(queue_problems),
            "import_failures": sum(1 for item in queue_items if item["issue_type"] == "import_failure"),
            "low_storage_paths": len(low_storage),
            "critical_storage_paths": sum(1 for row in storage_rows if row["status"] == "error"),
            "lag_items": len(lag_items),
            "lag_problems": len(lag_problems),
            "service_errors": sum(1 for service in service_results if service["status"] == "error"),
        },
    }
