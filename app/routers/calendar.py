"""Per-user .ics calendar feed.

Public endpoint: GET /calendar/{token}.ics

The token is a random secret stored on users.calendar_token (added in
migration 0002). Anyone with the URL can read the user's upcoming-episode
list, so the URL is treated as bearer credential — no session login needed.
The token width (24 url-safe bytes ≈ 192 bits) makes brute force infeasible.

Scope: this user's own MediaRequests where media_type=='tv', intersected
with the next 60 days of Sonarr / Sonarr-Anime calendar episodes. Shared
requests are deliberately excluded — the answer to "whose calendar is
this?" is "yours alone" so households with one feed-per-person don't
double-list the same episode.

Output: RFC 5545 iCalendar with one VEVENT per upcoming episode.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import __version__
from app.database import MediaRequest, User, get_db


logger = logging.getLogger(__name__)
router = APIRouter()


# Window controls how far forward the .ics looks. Matches the dashboard's
# default and Sonarr's typical calendar reach. Self-hosters who want a
# different horizon can change this constant — not surfaced as a setting
# because it's almost never tuned.
WINDOW_DAYS = 60

# Calendar apps poll periodically (Apple Calendar: hourly default, Google:
# every few hours). 15 minutes balances freshness against load on the
# Sonarr instances we'd otherwise hit per-poll.
CACHE_MAX_AGE_SECONDS = 15 * 60


# ---------------------------------------------------------------------------
# RFC 5545 helpers
# ---------------------------------------------------------------------------


def _ics_escape(value: str) -> str:
    """Escape per RFC 5545 §3.3.11 for TEXT-typed properties."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fold(line: str) -> str:
    """Fold a content line at 75 octets per RFC 5545 §3.1."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks: list[bytes] = []
    while len(encoded) > 75:
        # Don't split inside a UTF-8 multibyte sequence: back off until the
        # next byte at idx is a valid leading byte (top bits != 10).
        idx = 75
        while idx > 0 and (encoded[idx] & 0xC0) == 0x80:
            idx -= 1
        chunks.append(encoded[:idx])
        encoded = encoded[idx:]
    chunks.append(encoded)
    return "\r\n ".join(c.decode("utf-8") for c in chunks)


def _fmt_dt(dt: datetime) -> str:
    """Format a UTC datetime for ICS DTSTART/DTEND/DTSTAMP (UTC form)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _parse_air_date(value: str) -> datetime | None:
    """Parse Sonarr's airDateUtc strings. Tolerates trailing Z and missing TZ."""
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except (TypeError, ValueError):
        return None


def _build_uid(user_id: int, series_id: int, season: int, episode: int) -> str:
    """Stable UID per user × episode. Calendar apps key off this for updates."""
    raw = f"{user_id}:{series_id}:S{season:02d}E{episode:02d}"
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{digest}-bingealert@local"


def _render_ics(events: Iterable[dict], user: User) -> str:
    """Assemble the full VCALENDAR body. CRLF line endings per spec."""
    now = _fmt_dt(datetime.now(timezone.utc))
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//BingeAlert//{__version__}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold("X-WR-CALNAME:" + _ics_escape(f"BingeAlert — {user.username}")),
        _fold(
            "X-WR-CALDESC:"
            + _ics_escape("Upcoming episodes for series you've requested via BingeAlert.")
        ),
    ]
    for ev in events:
        start = ev["start"]
        end = ev["end"]
        uid = ev["uid"]
        summary = ev["summary"]
        status = ev["status"]
        lines.append("BEGIN:VEVENT")
        lines.append(_fold(f"UID:{uid}"))
        lines.append(f"DTSTAMP:{now}")
        lines.append(f"DTSTART:{_fmt_dt(start)}")
        lines.append(f"DTEND:{_fmt_dt(end)}")
        lines.append(_fold("SUMMARY:" + _ics_escape(summary)))
        if ev.get("description"):
            lines.append(_fold("DESCRIPTION:" + _ics_escape(ev["description"])))
        if ev.get("url"):
            lines.append(_fold("URL:" + ev["url"]))
        lines.append(f"STATUS:{status}")
        lines.append("TRANSP:TRANSPARENT")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# Sonarr fetch — narrow version of /admin/upcoming-episodes for one user.
# ---------------------------------------------------------------------------


async def _fetch_user_episodes(user: User, db: Session) -> list[dict]:
    """Return ICS-event dicts for `user`'s tracked series in the next WINDOW_DAYS."""
    from app.services.sonarr_service import get_all_sonarr_instances

    tv_requests = (
        db.query(MediaRequest)
        .filter(MediaRequest.user_id == user.id, MediaRequest.media_type == "tv")
        .all()
    )
    if not tv_requests:
        return []

    tmdb_to_request = {r.tmdb_id: r for r in tv_requests if r.tmdb_id}
    title_to_request = {r.title.lower().strip(): r for r in tv_requests}

    start_date = datetime.utcnow().strftime("%Y-%m-%d")
    end_date = (datetime.utcnow() + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

    instances = get_all_sonarr_instances()
    series_maps: list[dict] = []
    calendar_episodes: list[dict] = []
    for idx, sonarr in enumerate(instances):
        try:
            series_list = await sonarr._get("/series")
            series_maps.append({s.get("id"): s for s in series_list if s.get("id")})
        except Exception as e:
            logger.warning("calendar feed: series load from %s failed: %s", sonarr.instance_name, e)
            series_maps.append({})
        try:
            episodes = await sonarr.get_calendar(start_date, end_date) or []
        except Exception as e:
            logger.warning("calendar feed: get_calendar from %s failed: %s", sonarr.instance_name, e)
            episodes = []
        for ep in episodes:
            ep["_instance_idx"] = idx
        calendar_episodes.extend(episodes)

    events: list[dict] = []
    for ep in calendar_episodes:
        instance_idx = ep.get("_instance_idx", 0)
        smap = series_maps[instance_idx] if instance_idx < len(series_maps) else {}
        series = smap.get(ep.get("seriesId"))
        if not series:
            continue
        # Match by TMDB first, then by normalized title.
        request = tmdb_to_request.get(series.get("tmdbId")) or title_to_request.get(
            series.get("title", "").lower().strip()
        )
        if not request:
            continue

        air_dt = _parse_air_date(ep.get("airDateUtc") or ep.get("airDate") or "")
        if not air_dt:
            continue

        runtime_min = ep.get("runtime") or series.get("runtime") or 60
        season = ep.get("seasonNumber") or 0
        episode = ep.get("episodeNumber") or 0
        ep_title = ep.get("title") or ""
        series_title = series.get("title") or request.title

        summary = f"{series_title} S{season:02d}E{episode:02d}"
        if ep_title:
            summary = f"{summary} — {ep_title}"

        events.append(
            {
                "uid": _build_uid(user.id, ep.get("seriesId") or 0, season, episode),
                "start": air_dt,
                "end": air_dt + timedelta(minutes=int(runtime_min)),
                "summary": summary,
                "description": series.get("overview") or "",
                "status": "CONFIRMED" if ep.get("hasFile") else "TENTATIVE",
            }
        )

    events.sort(key=lambda e: e["start"])
    return events


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/calendar/{token}.ics")
async def get_user_calendar(token: str, db: Session = Depends(get_db)):
    # Token shape sanity check before we hit the DB. token_urlsafe(24)
    # produces 32 chars; allow a wide range to be tolerant of future widths.
    if not (8 <= len(token) <= 128) or not all(
        c.isalnum() or c in "-_" for c in token
    ):
        raise HTTPException(status_code=404, detail="Not found")

    user = db.query(User).filter(User.calendar_token == token).first()
    if not user or user.is_active is False:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        events = await _fetch_user_episodes(user, db)
    except Exception as e:
        logger.error("calendar feed render failed for user %s: %s", user.id, e, exc_info=True)
        # Still return a syntactically-valid empty calendar so the
        # subscription doesn't break in the user's calendar app on a
        # transient Sonarr outage.
        events = []

    body = _render_ics(events, user)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Cache-Control": f"public, max-age={CACHE_MAX_AGE_SECONDS}",
            "Content-Disposition": f'inline; filename="bingealert-{user.username}.ics"',
        },
    )
