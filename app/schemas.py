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
