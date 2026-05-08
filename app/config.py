"""Settings for BingeAlert v2.

Config loads from three sources, in priority order:
  1. /data/config.json   (written by the setup wizard; primary source in production)
  2. process environment (legacy / power-user fallback; e.g. compose env_file)
  3. field defaults

If neither config.json nor the minimum required env vars are present, the app
boots into setup mode (see app.main / setup middleware) and the wizard collects
the values, then writes config.json on completion.

All fields are Optional so the Settings object can be instantiated with nothing
configured -- setup mode needs to import settings before any user input exists.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolved once at import. Override with DATA_DIR env var if you need a
# non-standard path (e.g. /var/lib/bingealert).
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"


def _read_config_file() -> dict[str, Any]:
    """Load /data/config.json if it exists, else return {}."""
    if not CONFIG_FILE.is_file():
        return {}
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- Storage -----
    data_dir: str = str(DATA_DIR)
    sqlite_filename: str = "bingealert.db"

    # ----- Integrations -----
    jellyseerr_url: Optional[str] = None
    jellyseerr_api_key: Optional[str] = None

    sonarr_url: Optional[str] = None
    sonarr_api_key: Optional[str] = None

    sonarr_anime_url: Optional[str] = None
    sonarr_anime_api_key: Optional[str] = None

    radarr_url: Optional[str] = None
    radarr_api_key: Optional[str] = None

    plex_url: Optional[str] = None
    plex_token: Optional[str] = None

    # ----- SMTP -----
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None

    # ----- Admin / notifications -----
    admin_email: Optional[str] = None  # falls back to smtp_from at use site

    # ----- Feature toggles (carried forward from v1.5.x) -----
    quality_monitor_enabled: bool = True
    quality_monitor_interval_hours: int = 24
    quality_waiting_delay_seconds: int = 300

    # 'manual' | 'auto' | 'auto_notify'
    issue_autofix_mode: str = "manual"

    # Seerr anime overrides (request-on-behalf routing to anime Sonarr)
    seerr_anime_server_id: Optional[int] = None
    seerr_anime_profile_id: Optional[int] = None
    seerr_anime_root_folder: Optional[str] = None

    # ----- Auth (v2) -----
    auth_required: bool = True
    admin_password_hash: Optional[str] = None  # bcrypt hash, set by wizard
    session_max_age_seconds: int = 7 * 24 * 60 * 60  # 7 days

    # Comma-separated CIDRs that bypass auth. Defaults cover the most common
    # home LAN ranges; the wizard lets you narrow this.
    local_network_cidrs: str = "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8"

    # Comma-separated CIDRs / IPs allowed to POST to /webhooks/*. Empty = allow all.
    webhook_allowed_ips: str = ""

    # Optional Cloudflare Turnstile (CAPTCHA on login)
    turnstile_site_key: Optional[str] = None
    turnstile_secret_key: Optional[str] = None

    # ----- Application -----
    app_secret_key: Optional[str] = None  # HMAC for session cookies; wizard generates
    environment: str = "production"

    # External-facing base URL (e.g. "https://bingealert.example.com"). Used to
    # build absolute links inside notification emails (per-user calendar feed,
    # future password-reset links). When unset, calendar footers are omitted —
    # email send paths still work, just without the absolute link.
    public_base_url: Optional[str] = None

    # ------------------------------------------------------------------
    # Derived
    # ------------------------------------------------------------------
    @computed_field
    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir.rstrip('/')}/{self.sqlite_filename}"

    @computed_field
    @property
    def config_file_path(self) -> str:
        return str(Path(self.data_dir) / "config.json")

    # ------------------------------------------------------------------
    # Setup-mode helpers
    # ------------------------------------------------------------------
    def is_minimally_configured(self) -> bool:
        """True iff the app has enough to actually run (vs. needs the wizard).

        Required: SMTP host, Seerr URL+key, Sonarr URL+key, Radarr URL+key,
        an app_secret_key, and -- if auth_required -- an admin_password_hash.
        Plex is optional. Anime Sonarr is optional.
        """
        required = [
            self.smtp_host,
            self.smtp_from,
            self.jellyseerr_url,
            self.jellyseerr_api_key,
            self.sonarr_url,
            self.sonarr_api_key,
            self.radarr_url,
            self.radarr_api_key,
            self.app_secret_key,
        ]
        if not all(required):
            return False
        if self.auth_required and not self.admin_password_hash:
            return False
        return True

    def write_to_disk(self, updates: dict[str, Any]) -> None:
        """Merge `updates` into /data/config.json atomically.

        Used by the setup wizard and the admin settings page. Writes to a
        temp file then renames so a crash mid-write can't leave a corrupt
        config.json.
        """
        path = Path(self.config_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = _read_config_file()
        existing.update(updates)

        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, sort_keys=True)
        tmp.replace(path)


def _build_settings() -> Settings:
    """Construct Settings with /data/config.json overlaid on env+defaults.

    Pydantic-settings doesn't ship a JSON file source in 2.1.0 that's as
    simple to wire as we want, so we read the JSON manually and pass the
    values as init kwargs -- which take highest precedence in the standard
    Settings source chain. Env vars and .env still work for any keys the
    JSON file doesn't set.
    """
    file_values = _read_config_file()
    return Settings(**file_values)


settings = _build_settings()


def reload_from_disk() -> None:
    """Re-read /data/config.json and update the module-level `settings` in place.

    Used after the admin Settings page or the wizard writes config.json so
    subsequent reads (middleware, /admin/config GET, background workers) see
    the new values without a container restart. Note: connection-time state
    (e.g. an already-open SQLAlchemy engine, in-flight HTTP clients) does
    NOT pick up new URLs/tokens until restart -- only the read paths that
    consult `settings.X` per-request benefit.
    """
    fresh = _build_settings()
    for field_name in Settings.model_fields:
        try:
            object.__setattr__(settings, field_name, getattr(fresh, field_name))
        except Exception:
            pass
