from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional, List


# Sonarr Webhook Schemas
class SonarrEpisode(BaseModel):
    id: int
    episodeNumber: int
    seasonNumber: int
    title: str
    airDate: Optional[str] = None
    airDateUtc: Optional[str] = None


class SonarrSeries(BaseModel):
    id: int
    title: str
    tvdbId: int
    tmdbId: Optional[int] = None


class SonarrWebhook(BaseModel):
    eventType: str
    series: SonarrSeries
    episodes: Optional[List[SonarrEpisode]] = None
    episodeFile: Optional[dict] = None


# Radarr Webhook Schemas
class RadarrMovie(BaseModel):
    id: int
    title: str
    tmdbId: int
    imdbId: Optional[str] = None


class RadarrWebhook(BaseModel):
    eventType: str
    movie: RadarrMovie
    movieFile: Optional[dict] = None


# Jellyseerr API Schemas
class JellyseerrUser(BaseModel):
    id: int
    email: str
    username: str
    plexId: Optional[int] = None


# Jellyseerr Webhook Schemas
class JellyseerrWebhookMedia(BaseModel):
    media_type: str
    tmdbId: int
    tvdbId: Optional[int] = None
    status: Optional[int] = None
    status4k: Optional[int] = None


class JellyseerrWebhookRequest(BaseModel):
    request_id: int
    requestedBy_email: Optional[str] = None
    requestedBy_username: Optional[str] = None
    requestedBy_avatar: Optional[str] = None


class JellyseerrWebhook(BaseModel):
    notification_type: str  # "MEDIA_PENDING", "MEDIA_APPROVED", "MEDIA_AVAILABLE", etc.
    event: Optional[str] = None
    subject: str
    message: Optional[str] = None
    image: Optional[str] = None
    media: Optional[JellyseerrWebhookMedia] = None
    request: Optional[JellyseerrWebhookRequest] = None
    extra: Optional[List[dict]] = None


class JellyseerrMediaInfo(BaseModel):
    tmdbId: int
    tvdbId: Optional[int] = None


class JellyseerrRequest(BaseModel):
    id: int
    status: int
    media: JellyseerrMediaInfo
    requestedBy: JellyseerrUser
    type: str  # 'movie' or 'tv'
    seasons: Optional[List[dict]] = None


# Internal Schemas
class UserCreate(BaseModel):
    jellyseerr_id: int
    email: EmailStr
    username: str
    plex_id: Optional[int] = None


class UserResponse(BaseModel):
    id: int
    jellyseerr_id: int
    email: str
    username: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class MediaRequestCreate(BaseModel):
    user_id: int
    jellyseerr_request_id: int
    media_type: str
    tmdb_id: int
    title: str
    status: str
    season_count: Optional[int] = None


class NotificationCreate(BaseModel):
    user_id: int
    request_id: int
    notification_type: str
    subject: str
    body: str


class WebhookResponse(BaseModel):
    success: bool
    message: str
    processed_items: Optional[int] = None


# SECURITY FIX [MED-2]: Seerr webhook validation model
class SeerrWebhook(BaseModel):
    """Validates incoming Seerr webhook payloads"""
    notification_type: str
    media: Optional[dict] = None
    request: Optional[dict] = None
    subject: Optional[str] = None
    extra: Optional[list] = None
    message: Optional[str] = None
    image: Optional[str] = None

    class Config:
        extra = "allow"
