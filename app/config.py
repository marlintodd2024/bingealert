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
