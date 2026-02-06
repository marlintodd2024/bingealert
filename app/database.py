from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

from app.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency for database sessions"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    jellyseerr_id = Column(Integer, unique=True, nullable=False, index=True)
    email = Column(String, nullable=False)
    username = Column(String, nullable=False)
    plex_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    requests = relationship("MediaRequest", back_populates="user")
    notifications = relationship("Notification", back_populates="user")


class MediaRequest(Base):
    __tablename__ = "media_requests"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    jellyseerr_request_id = Column(Integer, unique=True, nullable=False, index=True)
    media_type = Column(String, nullable=False)  # 'movie' or 'tv'
    tmdb_id = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    status = Column(String, nullable=False)  # 'pending', 'approved', 'available'
    
    # For TV shows
    season_count = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="requests")
    episodes = relationship("EpisodeTracking", back_populates="request")
    notifications = relationship("Notification", back_populates="request")
    shared_with = relationship("SharedRequest", back_populates="request", cascade="all, delete-orphan")


class SharedRequest(Base):
    __tablename__ = "shared_requests"
    
    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("media_requests.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # Who added this user
    
    # Relationships
    request = relationship("MediaRequest", back_populates="shared_with")
    user = relationship("User", foreign_keys=[user_id])
    added_by_user = relationship("User", foreign_keys=[added_by])
    
    __table_args__ = (
        UniqueConstraint('request_id', 'user_id', name='_request_user_uc'),
    )


class EpisodeTracking(Base):
    __tablename__ = "episode_tracking"
    
    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("media_requests.id"), nullable=False)
    series_id = Column(Integer, nullable=False)  # Sonarr series ID
    season_number = Column(Integer, nullable=False)
    episode_number = Column(Integer, nullable=False)
    episode_title = Column(String, nullable=True)
    air_date = Column(DateTime, nullable=True)
    notified = Column(Boolean, default=False)
    available_in_plex = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    request = relationship("MediaRequest", back_populates="episodes")
    
    __table_args__ = (
        UniqueConstraint('request_id', 'series_id', 'season_number', 'episode_number', name='_request_series_season_episode_uc'),
    )


class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    request_id = Column(Integer, ForeignKey("media_requests.id"), nullable=False)
    notification_type = Column(String, nullable=False)  # 'episode', 'movie', 'season_complete'
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)
    send_after = Column(DateTime, nullable=True)  # Delay sending until this time (for Plex indexing)
    series_id = Column(Integer, nullable=True)  # Sonarr series ID (for batching TV episodes)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="notifications")
    request = relationship("MediaRequest", back_populates="notifications")
