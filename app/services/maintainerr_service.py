"""Helpers for Maintainerr's configurable webhook payloads."""
from __future__ import annotations

import json
import re
from typing import Any


_HANDLED_TYPES = {"16", "MEDIAHANDLED", "MEDIAHANDLEDNOTIFICATION"}
_TEST_TYPES = {"128", "TEST", "TESTNOTIFICATION"}


def is_media_handled_notification(value: Any) -> bool:
    """Accept the numeric, display, and symbolic forms used by Maintainerr builds."""
    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    return normalized in _HANDLED_TYPES


def is_test_notification(value: Any) -> bool:
    """Return whether Maintainerr sent its test-connection notification."""
    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    return normalized in _TEST_TYPES


def _decode_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def extract_media_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return mediaItems from both current and older webhook template shapes.

    Maintainerr flattens its ``extra`` values in current builds and serializes
    ``mediaItems`` as a JSON string. Some existing templates instead preserve
    ``extra`` as an object, so accept both without trusting unrelated fields.
    """
    candidates: list[Any] = [
        payload.get("mediaItems"),
        payload.get("media_items"),
    ]

    extra = _decode_json(payload.get("extra"))
    if isinstance(extra, dict):
        candidates.extend([extra.get("mediaItems"), extra.get("media_items")])
    elif isinstance(extra, list):
        for entry in extra:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("name") or entry.get("key") or "").lower()
            if key in {"mediaitems", "media_items"}:
                candidates.append(entry.get("value"))

    for candidate in candidates:
        decoded = _decode_json(candidate)
        if isinstance(decoded, list):
            return [item for item in decoded if isinstance(item, dict)]
        if isinstance(decoded, dict):
            return [decoded]
    return []


def _positive_ints(value: Any) -> set[int]:
    if not isinstance(value, list):
        value = [value]

    result: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            number = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if number > 0:
            result.add(number)
    return result


def extract_request_keys(payload: dict[str, Any]) -> set[tuple[str, int]]:
    """Extract safe BingeAlert request keys from whole-movie/show items.

    Season and episode provider ids identify those child objects, not their
    parent show. Suppressing a whole TV request for one cleaned season would
    hide legitimate future episodes, so those scopes deliberately fail closed.
    """
    keys: set[tuple[str, int]] = set()
    for item in extract_media_items(payload):
        raw_type = str(item.get("type") or item.get("mediaType") or "").lower()
        if raw_type == "movie":
            media_type = "movie"
        elif raw_type in {"show", "series", "tv"}:
            media_type = "tv"
        else:
            continue

        provider_ids = item.get("providerIds") or item.get("provider_ids") or {}
        if not isinstance(provider_ids, dict):
            continue
        for tmdb_id in _positive_ints(provider_ids.get("tmdb")):
            keys.add((media_type, tmdb_id))
    return keys
