from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str
    
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
    smtp_user: str
    smtp_password: str
    smtp_from: str
    
    # Application
    app_secret_key: str
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
