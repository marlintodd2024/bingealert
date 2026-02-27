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


async def check_sonarr_queue():
    """Check Sonarr queue for stuck downloads and auto-fix TBA titles"""
    logger.info("Checking Sonarr queue for stuck downloads...")
    
    sonarr = SonarrService()
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
    """Check Radarr queue for stuck downloads"""
    logger.info("Checking Radarr queue for stuck downloads...")
    
    radarr = RadarrService()
    stuck_items = []
    
    try:
        # Get queue from Radarr
        queue = await radarr._get("/queue")
        
        if not queue or 'records' not in queue:
            logger.info("Radarr queue is empty")
            return []
        
        now = datetime.utcnow()
        
        for item in queue['records']:
            item_id = item.get('id')
            title = item.get('title', 'Unknown')
            status = item.get('status', '').lower()
            
            # Get status messages
            status_messages = item.get('statusMessages', [])
            has_warning = any(msg.get('messages', []) for msg in status_messages)
            
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
        
        return stuck_items, []  # Radarr doesn't have TBA fix yet
        
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
                <p>This is an automated alert from your Plex Notification Portal</p>
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
    
    try:
        # Check both services (now returns stuck_items and fixed_items)
        sonarr_stuck, sonarr_fixed = await check_sonarr_queue()
        radarr_stuck, radarr_fixed = await check_radarr_queue()
        
        all_stuck = sonarr_stuck + radarr_stuck
        all_fixed = sonarr_fixed + radarr_fixed
        
        email_service = EmailService()
        admin_email = settings.admin_email or settings.smtp_from
        
        # Send auto-fix success email if we fixed anything
        if all_fixed:
            logger.info(f"✅ Auto-fixed {len(all_fixed)} TBA title issues!")
            
            html = generate_auto_fix_email(all_fixed)
            await email_service.send_email(
                to_email=admin_email,
                subject=f"✅ Auto-Fixed {len(all_fixed)} TBA Title Issue{'s' if len(all_fixed) != 1 else ''}",
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
        
    except Exception as e:
        logger.error(f"Failed to check stuck downloads: {e}")


def generate_auto_fix_email(fixed_items):
    """Generate HTML email for auto-fixed TBA issues"""
    
    items_html = []
    for item in fixed_items:
        items_html.append(f'''
            <div style="background: white; padding: 15px; margin-bottom: 15px; border-left: 4px solid #4caf50; border-radius: 4px;">
                <div style="font-weight: bold; color: #333; margin-bottom: 5px;">
                    [{item['service']}] {item['series_title']}
                </div>
                <div style="font-size: 13px; color: #666; margin-bottom: 5px;">
                    <strong>Episode:</strong> {item['episode_title']}
                </div>
                <div style="font-size: 13px; color: #4caf50;">
                    ✅ <strong>Action Taken:</strong> {item['action']} - {item['reason']}
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
                <h1>TBA Titles Auto-Fixed</h1>
                <p>{len(fixed_items)} episode{'s' if len(fixed_items) != 1 else ''} automatically fixed</p>
            </div>
            
            <div class="content">
                <p>The following downloads were stuck due to <strong>TBA (To Be Announced)</strong> episode titles. I automatically triggered a <strong>Series Refresh & Scan</strong> in Sonarr to pull updated metadata from TVDB, which should allow the imports to complete:</p>
                
                {''.join(items_html)}
                
                <p style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; font-size: 14px; color: #666;">
                    <strong>What happened:</strong><br>
                    • Sonarr detected episodes with TBA titles blocking import<br>
                    • Portal triggered <code>RefreshSeries</code> command to update metadata<br>
                    • Portal triggered <code>RescanSeries</code> command to retry import<br>
                    • Episodes should now import successfully ✅
                </p>
            </div>
            
            <div class="footer">
                <p>This is an automated fix from your Plex Notification Portal</p>
                <p>Generated on {datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')} UTC</p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return html


async def stuck_download_monitor():
    """Background worker that checks for stuck downloads every 30 minutes"""
    logger.info("⚠️ Stuck download monitor started - will check every 30 minutes")
    
    # Clear alerted items every 24 hours so we can re-alert
    last_clear = datetime.utcnow()
    
    while True:
        try:
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
