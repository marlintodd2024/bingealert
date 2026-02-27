from pydantic_settings import BaseSettings
from pydantic import computed_field
from typing import Optional


class Settings(BaseSettings):
    # Database
    db_password: str
    db_user: str = "notifyuser"
    db_name: str = "notifications"
    db_host: str = "postgres"
    db_port: int = 5432
    
    # Jellyseerr
    jellyseerr_url: str
    jellyseerr_api_key: str
    
    # Sonarr
    sonarr_url: str
    sonarr_api_key: str
    
    # Radarr
    radarr_url: str
    radarr_api_key: str
    
    # Plex (Optional)
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    
    # SMTP
    smtp_host: str
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: str
    
    # Admin (for weekly summaries and alerts)
    admin_email: Optional[str] = None  # If not set, uses smtp_from
    
    # Feature Toggles
    quality_monitor_enabled: bool = True  # Monitor for unreleased content and quality mismatches
    quality_monitor_interval_hours: int = 24  # How often to check (in hours)
    quality_waiting_delay_seconds: int = 300  # Delay before sending quality waiting emails (allows cancellation)
    
    # Issue Auto-fix: 'manual', 'auto', 'auto_notify'
    issue_autofix_mode: str = "manual"  # manual = admin reviews, auto = auto blacklist+research, auto_notify = auto + email admin
    
    # Application
    app_secret_key: str
    
    @computed_field
    @property
    def database_url(self) -> str:
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra fields to handle both old and new .env formats


settings = Settings()
