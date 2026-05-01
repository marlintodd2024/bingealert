import json
import subprocess
import os
import logging
from datetime import datetime
from typing import Optional
import tempfile
import zipfile

logger = logging.getLogger(__name__)


class BackupService:
    """Service to backup and restore database and configuration"""
    
    def __init__(self):
        self.backup_dir = "/data/backups"
        os.makedirs(self.backup_dir, exist_ok=True)
    
    def create_backup(self, include_config: bool = True) -> Optional[str]:
        """
        Create a backup of the database and optionally config
        Returns the path to the backup zip file
        """
        try:
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            backup_name = f"bingealert_backup_{timestamp}"
            temp_dir = tempfile.mkdtemp()
            
            # Backup database using pg_dump
            db_backup_file = os.path.join(temp_dir, "database.sql")
            logger.info("Creating database backup...")
            
            result = subprocess.run([
                'pg_dump',
                '-h', os.getenv('DB_HOST', 'postgres'),
                '-U', os.getenv('DB_USER', 'notifyuser'),
                '-d', os.getenv('DB_NAME', 'notifications'),
                '-f', db_backup_file
            ], env={**os.environ, 'PGPASSWORD': os.getenv('DB_PASSWORD')}, 
            capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Database backup failed: {result.stderr}")
                return None
            
            # Create metadata file
            metadata = {
                'backup_date': datetime.utcnow().isoformat(),
                'version': '1.0.0',
                'includes_config': include_config,
                'database_name': os.getenv('DB_NAME', 'notifications'),
                'database_user': os.getenv('DB_USER', 'notifyuser')
            }
            
            metadata_file = os.path.join(temp_dir, "metadata.json")
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Optionally include sanitized config
            if include_config:
                config_data = {
                    'jellyseerr_url': os.getenv('JELLYSEERR_URL'),
                    'sonarr_url': os.getenv('SONARR_URL'),
                    'radarr_url': os.getenv('RADARR_URL'),
                    'plex_url': os.getenv('PLEX_URL'),
                    'smtp_host': os.getenv('SMTP_HOST'),
                    'smtp_port': os.getenv('SMTP_PORT'),
                    'smtp_from': os.getenv('SMTP_FROM'),
                    # Note: API keys and passwords are NOT included for security
                    'note': 'API keys and passwords must be manually configured after restore'
                }
                
                config_file = os.path.join(temp_dir, "config.json")
                with open(config_file, 'w') as f:
                    json.dump(config_data, f, indent=2)
            
            # Create zip file
            backup_zip = os.path.join(self.backup_dir, f"{backup_name}.zip")
            with zipfile.ZipFile(backup_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(db_backup_file, "database.sql")
                zipf.write(metadata_file, "metadata.json")
                if include_config:
                    zipf.write(config_file, "config.json")
            
            # Cleanup temp files
            os.remove(db_backup_file)
            os.remove(metadata_file)
            if include_config:
                os.remove(config_file)
            os.rmdir(temp_dir)
            
            logger.info(f"Backup created successfully: {backup_zip}")
            return backup_zip
            
        except Exception as e:
            logger.error(f"Failed to create backup: {e}", exc_info=True)
            return None
    
    def restore_backup(self, backup_file: str) -> bool:
        """
        Restore from a backup zip file
        Returns True if successful
        """
        try:
            if not os.path.exists(backup_file):
                logger.error(f"Backup file not found: {backup_file}")
                return False
            
            temp_dir = tempfile.mkdtemp()
            
            # Extract backup
            logger.info(f"Extracting backup from {backup_file}...")
            with zipfile.ZipFile(backup_file, 'r') as zipf:
                zipf.extractall(temp_dir)
            
            # Read metadata
            metadata_file = os.path.join(temp_dir, "metadata.json")
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            
            logger.info(f"Restoring backup from {metadata['backup_date']}")
            
            # Restore database
            db_backup_file = os.path.join(temp_dir, "database.sql")
            logger.info("Restoring database...")
            
            # Drop and recreate database (PostgreSQL)
            result = subprocess.run([
                'psql',
                '-h', os.getenv('DB_HOST', 'postgres'),
                '-U', os.getenv('DB_USER', 'notifyuser'),
                '-d', 'postgres',
                '-c', f"DROP DATABASE IF EXISTS {os.getenv('DB_NAME', 'notifications')};"
            ], env={**os.environ, 'PGPASSWORD': os.getenv('DB_PASSWORD')},
            capture_output=True, text=True)
            
            result = subprocess.run([
                'psql',
                '-h', os.getenv('DB_HOST', 'postgres'),
                '-U', os.getenv('DB_USER', 'notifyuser'),
                '-d', 'postgres',
                '-c', f"CREATE DATABASE {os.getenv('DB_NAME', 'notifications')};"
            ], env={**os.environ, 'PGPASSWORD': os.getenv('DB_PASSWORD')},
            capture_output=True, text=True)
            
            # Restore data
            result = subprocess.run([
                'psql',
                '-h', os.getenv('DB_HOST', 'postgres'),
                '-U', os.getenv('DB_USER', 'notifyuser'),
                '-d', os.getenv('DB_NAME', 'notifications'),
                '-f', db_backup_file
            ], env={**os.environ, 'PGPASSWORD': os.getenv('DB_PASSWORD')},
            capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Database restore failed: {result.stderr}")
                return False
            
            # Cleanup
            os.remove(db_backup_file)
            os.remove(metadata_file)
            if os.path.exists(os.path.join(temp_dir, "config.json")):
                os.remove(os.path.join(temp_dir, "config.json"))
            os.rmdir(temp_dir)
            
            logger.info("Backup restored successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore backup: {e}", exc_info=True)
            return False
    
    def list_backups(self):
        """List all available backups"""
        try:
            backups = []
            for filename in os.listdir(self.backup_dir):
                if filename.endswith('.zip'):
                    filepath = os.path.join(self.backup_dir, filename)
                    size = os.path.getsize(filepath)
                    mtime = os.path.getmtime(filepath)
                    
                    backups.append({
                        'filename': filename,
                        'filepath': filepath,
                        'size': size,
                        'created': datetime.fromtimestamp(mtime).isoformat()
                    })
            
            return sorted(backups, key=lambda x: x['created'], reverse=True)
        except Exception as e:
            logger.error(f"Failed to list backups: {e}")
            return []
    
    def delete_backup(self, filename: str) -> bool:
        """Delete a backup file"""
        try:
            # Validate filename against actual directory listing (no user input in path construction)
            backup_dir = os.path.realpath(self.backup_dir)
            for entry in os.listdir(backup_dir):
                full_path = os.path.join(backup_dir, entry)
                if os.path.isfile(full_path) and entry == filename:
                    os.remove(full_path)
                    logger.info(f"Deleted backup: {entry}")
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete backup: {e}")
            return False
