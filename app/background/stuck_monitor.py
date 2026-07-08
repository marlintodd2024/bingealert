"""
Stuck Download Monitor
Monitors Sonarr/Radarr activity queues and alerts when downloads are stuck
"""
import asyncio
from datetime import datetime, timedelta
from app.services.sonarr_service import SonarrService
from app.services.radarr_service import RadarrService
from app.services.email_service import EmailService
from app.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Track items we've already alerted about
alerted_items = set()


def _is_import_failure(messages):
    """Check if status messages indicate an import failure that needs auto-remediation.
    Covers: no eligible files, already imported, manual import required, matched by ID, etc."""
    import_failure_patterns = [
        'no files found are eligible for import',
        'not eligible for import',
        'has already been imported',
        'manual import required',
        'matched to movie by id',
        'matched to series by id',
        'unable to import automatically',
    ]
    for msg in messages:
        msg_lower = msg.lower()
        if any(pattern in msg_lower for pattern in import_failure_patterns):
            return True
    return False


async def check_sonarr_queue(sonarr=None):
    """Check Sonarr queue for stuck downloads and auto-fix TBA titles"""
    if sonarr is None:
        sonarr = SonarrService()
    
    instance = getattr(sonarr, 'instance_name', 'Sonarr')
    logger.info(f"Checking {instance} queue for stuck downloads...")
    
    stuck_items = []
    fixed_items = []
    
    try:
        # Get queue from Sonarr
        queue = await sonarr._get("/queue")
        
        if not queue or 'records' not in queue:
            logger.info("Sonarr queue is empty")
            return [], []
        
        now = datetime.utcnow()
        
        for item in queue['records']:
            item_id = item.get('id')
            title = item.get('title', 'Unknown')
            status = item.get('status', '').lower()
            series_id = item.get('seriesId')
            
            # Get status messages
            status_messages = item.get('statusMessages', [])
            messages = []
            for msg in status_messages:
                if msg.get('messages'):
                    messages.extend(msg.get('messages'))
            
            # Check for TBA title issue
            has_tba_issue = any('TBA' in msg or 'episode title' in msg.lower() for msg in messages)
            
            # Check for import failure using shared detection
            has_import_failure = _is_import_failure(messages)
            
            if has_import_failure and item_id:
                alert_key = f"sonarr_{item_id}_import_fix"
                if alert_key not in alerted_items:
                    logger.warning(f"🔧 Found import failure in Sonarr: {title}")
                    
                    try:
                        # Remove from queue with blocklist and trigger new search
                        await sonarr._delete(f"/queue/{item_id}", params={
                            "removeFromClient": "true",
                            "blocklist": "true"
                        })
                        logger.info(f"✅ Removed from queue and blocklisted: {title}")
                        
                        # Trigger new search if we have a series ID
                        if series_id:
                            await sonarr._post("/command", {"name": "SeriesSearch", "seriesId": series_id})
                            logger.info(f"✅ Triggered new search for series ID {series_id}")
                        
                        # Get series name for the report
                        series_title = title
                        if series_id:
                            try:
                                series = await sonarr._get(f"/series/{series_id}")
                                series_title = series.get('title', title)
                            except Exception:
                                pass
                        
                        fixed_items.append({
                            'service': 'Sonarr',
                            'series_title': series_title,
                            'episode_title': title,
                            'action': 'Blocklist & Re-search',
                            'reason': 'Import failure — ' + (messages[0] if messages else 'No eligible files')
                        })
                        
                        alerted_items.add(alert_key)
                        logger.info(f"✅ Auto-fixed import failure for: {title}")
                        continue
                        
                    except Exception as e:
                        logger.error(f"Failed to auto-fix import failure: {e}")
            
            if has_tba_issue and series_id:
                logger.warning(f"🔧 Found TBA title issue in Sonarr: {title}")
                
                try:
                    # Trigger Series Refresh & Scan
                    logger.info(f"Triggering Refresh & Scan for series ID {series_id}...")
                    
                    # Command to refresh series
                    refresh_command = {
                        "name": "RefreshSeries",
                        "seriesId": series_id
                    }
                    await sonarr._post("/command", refresh_command)
                    logger.info(f"✅ Refresh command sent for series ID {series_id}")
                    
                    # Wait a moment for refresh to start
                    await asyncio.sleep(2)
                    
                    # Command to rescan series
                    rescan_command = {
                        "name": "RescanSeries",
                        "seriesId": series_id
                    }
                    await sonarr._post("/command", rescan_command)
                    logger.info(f"✅ Rescan command sent for series ID {series_id}")
                    
                    # Get series name
                    series = await sonarr._get(f"/series/{series_id}")
                    series_title = series.get('title', 'Unknown Series')
                    
                    fixed_items.append({
                        'service': 'Sonarr',
                        'series_title': series_title,
                        'episode_title': title,
                        'action': 'Refresh & Scan',
                        'reason': 'TBA title blocking import'
                    })
                    
                    # Mark as alerted so we don't keep trying
                    alert_key = f"sonarr_{item_id}_tba_fixed"
                    alerted_items.add(alert_key)
                    
                    logger.info(f"✅ Auto-fixed TBA issue for: {series_title} - {title}")
                    
                    # Don't add to stuck_items since we're fixing it
                    continue
                    
                except Exception as e:
                    logger.error(f"Failed to auto-fix TBA issue: {e}")
                    # Fall through to stuck_items if fix failed
            
            # Check if stalled
            is_stalled = status in ['warning', 'stalled', 'failed']
            
            # Check how long it's been in queue
            added_str = item.get('added')
            if added_str:
                try:
                    added = datetime.fromisoformat(added_str.replace('Z', '+00:00'))
                    time_in_queue = (now - added.replace(tzinfo=None)).total_seconds() / 3600  # hours
                    
                    # Consider stuck if:
                    # 1. Status is warning/stalled/failed, OR
                    # 2. Been in queue for more than 4 hours with no progress
                    if is_stalled or (time_in_queue > 4 and item.get('size', 0) > 0):
                        # Only alert if we haven't already alerted for this item
                        alert_key = f"sonarr_{item_id}"
                        if alert_key not in alerted_items:
                            stuck_items.append({
                                'service': 'Sonarr',
                                'title': title,
                                'status': status,
                                'time_in_queue': f"{time_in_queue:.1f} hours",
                                'messages': messages,
                                'protocol': item.get('protocol', 'Unknown'),
                                'download_client': item.get('downloadClient', 'Unknown')
                            })
                            alerted_items.add(alert_key)
                            logger.warning(f"Found stuck item in Sonarr: {title} ({status}, {time_in_queue:.1f}h in queue)")
                
                except Exception as e:
                    logger.error(f"Error parsing Sonarr item time: {e}")
        
        return stuck_items, fixed_items
        
    except Exception as e:
        logger.error(f"Failed to check Sonarr queue: {e}")
        return [], []


async def check_radarr_queue():
    """Check Radarr queue for stuck downloads and auto-fix import failures"""
    logger.info("Checking Radarr queue for stuck downloads...")
    
    radarr = RadarrService()
    stuck_items = []
    fixed_items = []
    
    try:
        # Get queue from Radarr
        queue = await radarr._get("/queue")
        
        if not queue or 'records' not in queue:
            logger.info("Radarr queue is empty")
            return [], []
        
        now = datetime.utcnow()
        
        for item in queue['records']:
            item_id = item.get('id')
            title = item.get('title', 'Unknown')
            status = item.get('status', '').lower()
            movie_id = item.get('movieId')
            
            # Get status messages
            status_messages = item.get('statusMessages', [])
            messages = []
            for msg in status_messages:
                if msg.get('messages'):
                    messages.extend(msg.get('messages'))
            
            # Also check the trackedDownloadStatus and trackedDownloadState fields
            # Radarr uses these for "Downloaded - Unable to Import Automatically"
            tracked_status = item.get('trackedDownloadStatus', '').lower()
            tracked_state = item.get('trackedDownloadState', '').lower()
            
            # "importpending" state with "warning" status = Unable to Import Automatically
            has_import_pending_warning = (
                tracked_state == 'importpending' and 
                tracked_status == 'warning'
            )
            
            # Check for import failure using shared detection
            has_import_failure = _is_import_failure(messages) or has_import_pending_warning
            
            if has_import_failure and item_id:
                alert_key = f"radarr_{item_id}_import_fix"
                if alert_key not in alerted_items:
                    reason_msg = messages[0] if messages else 'Unable to import automatically'
                    logger.warning(f"🔧 Found import failure in Radarr: {title} — {reason_msg}")
                    
                    try:
                        # Remove from queue with blocklist and trigger new search
                        await radarr._delete(f"/queue/{item_id}", params={
                            "removeFromClient": "true",
                            "blocklist": "true"
                        })
                        logger.info(f"✅ Removed from queue and blocklisted: {title}")
                        
                        # Trigger new search if we have a movie ID
                        if movie_id:
                            await radarr._post("/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
                            logger.info(f"✅ Triggered new search for movie ID {movie_id}")
                        
                        # Get movie name for the report
                        movie_title = title
                        if movie_id:
                            try:
                                movie = await radarr._get(f"/movie/{movie_id}")
                                movie_title = movie.get('title', title)
                            except Exception:
                                pass
                        
                        fixed_items.append({
                            'service': 'Radarr',
                            'series_title': movie_title,
                            'episode_title': title,
                            'action': 'Blocklist & Re-search',
                            'reason': f'Import failure — {reason_msg}'
                        })
                        
                        alerted_items.add(alert_key)
                        logger.info(f"✅ Auto-fixed import failure for: {movie_title}")
                        continue
                        
                    except Exception as e:
                        logger.error(f"Failed to auto-fix import failure: {e}")
            
            # Check if stalled
            is_stalled = status in ['warning', 'stalled', 'failed']
            
            # Check how long it's been in queue
            added_str = item.get('added')
            if added_str:
                try:
                    added = datetime.fromisoformat(added_str.replace('Z', '+00:00'))
                    time_in_queue = (now - added.replace(tzinfo=None)).total_seconds() / 3600  # hours
                    
                    # Consider stuck if:
                    # 1. Status is warning/stalled/failed, OR
                    # 2. Been in queue for more than 4 hours with no progress
                    if is_stalled or (time_in_queue > 4 and item.get('size', 0) > 0):
                        # Only alert if we haven't already alerted for this item
                        alert_key = f"radarr_{item_id}"
                        if alert_key not in alerted_items:
                            stuck_items.append({
                                'service': 'Radarr',
                                'title': title,
                                'status': status,
                                'time_in_queue': f"{time_in_queue:.1f} hours",
                                'messages': [msg.get('messages', []) for msg in status_messages if msg.get('messages')],
                                'protocol': item.get('protocol', 'Unknown'),
                                'download_client': item.get('downloadClient', 'Unknown')
                            })
                            alerted_items.add(alert_key)
                            logger.warning(f"Found stuck item in Radarr: {title} ({status}, {time_in_queue:.1f}h in queue)")
                
                except Exception as e:
                    logger.error(f"Error parsing Radarr item time: {e}")
        
        return stuck_items, fixed_items
        
    except Exception as e:
        logger.error(f"Failed to check Radarr queue: {e}")
        return [], []


def generate_stuck_alert_email(stuck_items):
    """Generate HTML email for stuck download alerts"""
    
    items_html = []
    for item in stuck_items:
        messages_html = ''
        if item['messages']:
            flat_messages = [msg for sublist in item['messages'] for msg in sublist]
            if flat_messages:
                messages_html = '<ul style="margin: 5px 0; padding-left: 20px; font-size: 13px;">'
                for msg in flat_messages[:3]:  # Show first 3 messages
                    messages_html += f'<li style="color: #f44336;">{msg}</li>'
                messages_html += '</ul>'
        
        items_html.append(f'''
            <div style="background: white; padding: 15px; margin-bottom: 15px; border-left: 4px solid #f44336; border-radius: 4px;">
                <div style="font-weight: bold; color: #333; margin-bottom: 5px;">
                    [{item['service']}] {item['title']}
                </div>
                <div style="font-size: 13px; color: #666;">
                    <strong>Status:</strong> {item['status'].upper()} | 
                    <strong>Time in Queue:</strong> {item['time_in_queue']} | 
                    <strong>Client:</strong> {item['download_client']}
                </div>
                {messages_html}
            </div>
        ''')
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; margin: 0; padding: 20px; }}
            .container {{ max-width: 700px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #f44336 0%, #d32f2f 100%); color: white; padding: 30px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
            .content {{ padding: 30px; }}
            .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #999; background: #f9f9f9; }}
            .alert-icon {{ font-size: 48px; margin-bottom: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="alert-icon">⚠️</div>
                <h1>Stuck Downloads Alert</h1>
                <p>{len(stuck_items)} item{'s' if len(stuck_items) != 1 else ''} stuck in download queue</p>
            </div>
            
            <div class="content">
                <p>The following downloads appear to be stuck and may need your attention:</p>
                
                {''.join(items_html)}
                
                <p style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; font-size: 14px; color: #666;">
                    <strong>What to do:</strong><br>
                    • Check your download client for errors<br>
                    • Verify the download hasn't stalled<br>
                    • Consider manually searching for a different release<br>
                    • Remove and re-add if necessary
                </p>
            </div>
            
            <div class="footer">
                <p>This is an automated alert from your BingeAlert</p>
                <p>Generated on {datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')} UTC</p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return html


async def check_and_alert_stuck_downloads():
    """Check both services, auto-fix TBA issues, and send alerts"""
    logger.info("=" * 60)
    logger.info("Checking for stuck downloads...")
    logger.info("=" * 60)
    from app.background.system_health import (
        record_worker_failure,
        record_worker_started,
        record_worker_success,
    )

    started_at = record_worker_started(
        "stuck_download_monitor",
        "Stuck download monitor",
        next_run_at=datetime.utcnow() + timedelta(minutes=30),
    )
    
    try:
        # Check all Sonarr instances (primary + anime if configured)
        from app.services.sonarr_service import get_all_sonarr_instances
        sonarr_stuck = []
        sonarr_fixed = []
        for sonarr_instance in get_all_sonarr_instances():
            s, f = await check_sonarr_queue(sonarr_instance)
            sonarr_stuck.extend(s)
            sonarr_fixed.extend(f)
        
        radarr_stuck, radarr_fixed = await check_radarr_queue()
        
        all_stuck = sonarr_stuck + radarr_stuck
        all_fixed = sonarr_fixed + radarr_fixed
        
        email_service = EmailService()
        admin_email = settings.admin_email or settings.smtp_from
        
        # Send auto-fix success email if we fixed anything
        if all_fixed:
            logger.info(f"✅ Auto-fixed {len(all_fixed)} stuck import issues!")
            
            html = generate_auto_fix_email(all_fixed)
            await email_service.send_email(
                to_email=admin_email,
                subject=f"✅ Auto-Fixed {len(all_fixed)} Stuck Import{'s' if len(all_fixed) != 1 else ''}",
                html_body=html
            )
            logger.info(f"✅ Auto-fix notification sent to {admin_email}")
        
        # Send stuck downloads alert if any remain stuck
        if all_stuck:
            logger.warning(f"⚠️ Found {len(all_stuck)} stuck downloads!")
            
            html = generate_stuck_alert_email(all_stuck)
            await email_service.send_email(
                to_email=admin_email,
                subject=f"⚠️ {len(all_stuck)} Stuck Download{'s' if len(all_stuck) != 1 else ''} Alert",
                html_body=html
            )
            logger.info(f"✅ Alert email sent to {admin_email}")
        
        if not all_stuck and not all_fixed:
            logger.info("✅ No stuck downloads found")
        record_worker_success(
            "stuck_download_monitor",
            "Stuck download monitor",
            started_at=started_at,
            next_run_at=datetime.utcnow() + timedelta(minutes=30),
        )
        
    except Exception as e:
        logger.error(f"Failed to check stuck downloads: {e}")
        record_worker_failure(
            "stuck_download_monitor",
            "Stuck download monitor",
            e,
            started_at=started_at,
            next_run_at=datetime.utcnow() + timedelta(minutes=30),
        )


def generate_auto_fix_email(fixed_items):
    """Generate HTML email for auto-fixed stuck imports"""
    
    items_html = []
    for item in fixed_items:
        items_html.append(f'''
            <div style="background: white; padding: 15px; margin-bottom: 15px; border-left: 4px solid #4caf50; border-radius: 4px;">
                <div style="font-weight: bold; color: #333; margin-bottom: 5px;">
                    [{item['service']}] {item['series_title']}
                </div>
                <div style="font-size: 13px; color: #666; margin-bottom: 5px;">
                    <strong>Release:</strong> {item['episode_title']}
                </div>
                <div style="font-size: 13px; color: #4caf50;">
                    ✅ <strong>Action Taken:</strong> {item['action']} — {item['reason']}
                </div>
            </div>
        ''')
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; margin: 0; padding: 20px; }}
            .container {{ max-width: 700px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #4caf50 0%, #45a049 100%); color: white; padding: 30px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
            .content {{ padding: 30px; }}
            .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #999; background: #f9f9f9; }}
            .success-icon {{ font-size: 48px; margin-bottom: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="success-icon">✅</div>
                <h1>Stuck Imports Auto-Fixed</h1>
                <p>{len(fixed_items)} item{'s' if len(fixed_items) != 1 else ''} automatically remediated</p>
            </div>
            
            <div class="content">
                <p>The following downloads were stuck due to import failures. BingeAlert automatically <strong>removed the stuck item, blocklisted the release, and triggered a new search</strong> for a different version:</p>
                
                {''.join(items_html)}
                
                <p style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; font-size: 14px; color: #666;">
                    <strong>What happened:</strong><br>
                    • Detected stuck imports that couldn't be processed automatically<br>
                    • Removed the problematic download from the queue<br>
                    • Blocklisted the release so it won't be grabbed again<br>
                    • Triggered a new search for a different release ✅
                </p>
            </div>
            
            <div class="footer">
                <p>This is an automated fix from your BingeAlert</p>
                <p>Generated on {datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')} UTC</p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return html


async def stuck_download_monitor():
    """Background worker that checks for stuck downloads every 30 minutes"""
    from app.background.utils import is_maintenance_active
    
    logger.info("⚠️ Stuck download monitor started - will check every 30 minutes")
    
    # Clear alerted items every 24 hours so we can re-alert
    last_clear = datetime.utcnow()
    
    while True:
        try:
            if is_maintenance_active():
                logger.info("🔧 Maintenance active — skipping stuck download check")
            else:
                # Check for stuck downloads
                await check_and_alert_stuck_downloads()
            
            # Clear alert cache every 24 hours
            now = datetime.utcnow()
            if (now - last_clear).total_seconds() > 86400:  # 24 hours
                logger.info("Clearing alert cache (24h reset)")
                alerted_items.clear()
                last_clear = now
            
        except Exception as e:
            logger.error(f"Stuck download monitor error: {e}")
        
        # Wait 30 minutes
        logger.info("💤 Sleeping for 30 minutes until next check...")
        await asyncio.sleep(30 * 60)  # 30 minutes
