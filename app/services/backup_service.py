import json
import logging
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import __version__
from app.config import CONFIG_FILE, settings


logger = logging.getLogger(__name__)


class BackupService:
    """SQLite backup and restore service."""

    def __init__(self):
        self.data_dir = Path(settings.data_dir)
        self.db_path = self.data_dir / settings.sqlite_filename
        self.backup_dir = str(self.data_dir / "backups")
        os.makedirs(self.backup_dir, exist_ok=True)

    def _sanitized_config(self) -> dict:
        return {
            "jellyseerr_url": settings.jellyseerr_url,
            "sonarr_url": settings.sonarr_url,
            "sonarr_anime_url": settings.sonarr_anime_url,
            "radarr_url": settings.radarr_url,
            "plex_url": settings.plex_url,
            "smtp_host": settings.smtp_host,
            "smtp_port": settings.smtp_port,
            "smtp_security": settings.smtp_security,
            "smtp_from": settings.smtp_from,
            "admin_email": settings.admin_email,
            "public_base_url": settings.public_base_url,
            "note": "API keys, passwords, tokens, and app secrets are not included.",
        }

    def _copy_sqlite_database(self, destination: Path) -> None:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"SQLite database not found: {self.db_path}")

        source_uri = f"file:{self.db_path}?mode=ro"
        source = sqlite3.connect(source_uri, uri=True)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def create_backup(self, include_config: bool = True) -> Optional[str]:
        """Create a zipped SQLite snapshot and optional sanitized config."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_name = f"bingealert_backup_{timestamp}"

            with tempfile.TemporaryDirectory() as temp_dir_raw:
                temp_dir = Path(temp_dir_raw)
                db_backup_file = temp_dir / "bingealert.db"
                metadata_file = temp_dir / "metadata.json"
                config_file = temp_dir / "config.json"

                logger.info("Creating SQLite backup from %s", self.db_path)
                self._copy_sqlite_database(db_backup_file)

                metadata = {
                    "backup_date": datetime.utcnow().isoformat(),
                    "version": __version__,
                    "format": "sqlite",
                    "includes_config": include_config,
                    "sqlite_filename": settings.sqlite_filename,
                }
                metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

                if include_config:
                    config_file.write_text(
                        json.dumps(self._sanitized_config(), indent=2),
                        encoding="utf-8",
                    )

                backup_zip = Path(self.backup_dir) / f"{backup_name}.zip"
                with zipfile.ZipFile(backup_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
                    zipf.write(db_backup_file, "bingealert.db")
                    zipf.write(metadata_file, "metadata.json")
                    if include_config:
                        zipf.write(config_file, "config.json")

            logger.info("Backup created successfully: %s", backup_zip)
            return str(backup_zip)
        except Exception as e:
            logger.error("Failed to create backup: %s", e, exc_info=True)
            return None

    def _validate_sqlite_file(self, db_file: Path) -> None:
        conn = sqlite3.connect(db_file)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {"users", "media_requests", "notifications"}
            missing = sorted(required - tables)
            if missing:
                raise ValueError(f"Backup database missing table(s): {', '.join(missing)}")
        finally:
            conn.close()

    def restore_backup(self, backup_file: str) -> bool:
        """Restore a SQLite backup zip. A container restart is required after."""
        try:
            backup_path = Path(backup_file)
            if not backup_path.exists():
                logger.error("Backup file not found: %s", backup_file)
                return False

            with tempfile.TemporaryDirectory() as temp_dir_raw:
                temp_dir = Path(temp_dir_raw)
                restored_db = temp_dir / "bingealert.db"

                with zipfile.ZipFile(backup_path, "r") as zipf:
                    names = set(zipf.namelist())
                    if "metadata.json" not in names or "bingealert.db" not in names:
                        logger.error("Invalid SQLite backup: missing metadata.json or bingealert.db")
                        return False
                    with zipf.open("bingealert.db") as src, restored_db.open("wb") as dst:
                        dst.write(src.read())

                self._validate_sqlite_file(restored_db)

                self.data_dir.mkdir(parents=True, exist_ok=True)
                from app.database import engine

                engine.dispose()

                if self.db_path.exists():
                    pre_restore = self.data_dir / (
                        f"{self.db_path.stem}.pre-restore-"
                        f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
                    )
                    self._copy_sqlite_database(pre_restore)
                    logger.info("Saved pre-restore database copy: %s", pre_restore)

                for suffix in ("", "-wal", "-shm"):
                    path = Path(str(self.db_path) + suffix)
                    if path.exists():
                        path.unlink()

                os.replace(restored_db, self.db_path)

                if CONFIG_FILE.is_file():
                    logger.info("Restore kept existing config.json in place")

            logger.info("Backup restored successfully from %s", backup_file)
            return True
        except Exception as e:
            logger.error("Failed to restore backup: %s", e, exc_info=True)
            return False

    def list_backups(self):
        """List all available backups."""
        try:
            backups = []
            for filename in os.listdir(self.backup_dir):
                if filename.endswith(".zip"):
                    filepath = os.path.join(self.backup_dir, filename)
                    size = os.path.getsize(filepath)
                    mtime = os.path.getmtime(filepath)

                    backups.append({
                        "filename": filename,
                        "filepath": filepath,
                        "size": size,
                        "created": datetime.fromtimestamp(mtime).isoformat(),
                    })

            return sorted(backups, key=lambda x: x["created"], reverse=True)
        except Exception as e:
            logger.error("Failed to list backups: %s", e)
            return []

    def delete_backup(self, filename: str) -> bool:
        """Delete a backup file by exact filename."""
        try:
            backup_dir = os.path.realpath(self.backup_dir)
            for entry in os.listdir(backup_dir):
                full_path = os.path.join(backup_dir, entry)
                if os.path.isfile(full_path) and entry == filename:
                    os.remove(full_path)
                    logger.info("Deleted backup: %s", entry)
                    return True
            return False
        except Exception as e:
            logger.error("Failed to delete backup: %s", e)
            return False
