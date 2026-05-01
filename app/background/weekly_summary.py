"""
Weekly summary email service
Sends admin a summary of all notifications sent during the week
"""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import SessionLocal, Notification, User
from app.services.email_service import EmailService
from app.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def generate_weekly_summary(db: Session):
    """Generate weekly summary of notifications sent"""
    logger.info("Generating weekly summary...")
    
    # Get date range (last 7 days)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=7)
    
    # Get all sent notifications in the last week
    sent_notifications = db.query(Notification).filter(
        Notification.sent == True,
        Notification.sent_at >= start_date,
        Notification.sent_at <= end_date
    ).order_by(Notification.sent_at.desc()).all()
    
    if not sent_notifications:
        logger.info("No notifications sent this week")
        return None
    
    # Group by user
    user_stats = {}
    for notif in sent_notifications:
        user_email = notif.user.email
        if user_email not in user_stats:
            user_stats[user_email] = {
                'email': user_email,
                'username': notif.user.username,
                'total': 0,
                'episodes': 0,
                'movies': 0,
                'notifications': []
            }
        
        user_stats[user_email]['total'] += 1
        if notif.notification_type == 'episode':
            user_stats[user_email]['episodes'] += 1
        elif notif.notification_type == 'movie':
            user_stats[user_email]['movies'] += 1
        
        user_stats[user_email]['notifications'].append({
            'subject': notif.subject,
            'type': notif.notification_type,
            'sent_at': notif.sent_at
        })
    
    # Generate HTML summary
    html = generate_summary_html(user_stats, start_date, end_date, len(sent_notifications))
    
    return html


def generate_summary_html(user_stats, start_date, end_date, total_count):
    """Generate HTML for weekly summary email"""
    
    # Sort users by total notifications sent
    sorted_users = sorted(user_stats.values(), key=lambda x: x['total'], reverse=True)
    
    user_rows = []
    for user in sorted_users:
        # Generate notification list
        notif_list = '<ul style="margin: 5px 0; padding-left: 20px; font-size: 13px; color: #666;">'
        for notif in user['notifications'][:10]:  # Show first 10
            icon = '📺' if notif['type'] == 'episode' else '🎬'
            notif_list += f'<li>{icon} {notif["subject"]}</li>'
        
        if len(user['notifications']) > 10:
            notif_list += f'<li style="color: #999;">... and {len(user["notifications"]) - 10} more</li>'
        notif_list += '</ul>'
        
        user_rows.append(f'''
            <tr>
                <td style="padding: 15px; border-bottom: 1px solid #eee;">
                    <div style="font-weight: bold; color: #333;">{user['username']}</div>
                    <div style="font-size: 13px; color: #999;">{user['email']}</div>
                </td>
                <td style="padding: 15px; border-bottom: 1px solid #eee; text-align: center;">
                    <div style="font-size: 24px; font-weight: bold; color: #e5a00d;">{user['total']}</div>
                </td>
                <td style="padding: 15px; border-bottom: 1px solid #eee; text-align: center;">
                    <div>📺 {user['episodes']}</div>
                    <div>🎬 {user['movies']}</div>
                </td>
                <td style="padding: 15px; border-bottom: 1px solid #eee;">
                    {notif_list}
                </td>
            </tr>
        ''')
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; margin: 0; padding: 20px; }}
            .container {{ max-width: 900px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #e5a00d 0%, #f4b41a 100%); color: white; padding: 30px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
            .stats {{ display: flex; justify-content: space-around; padding: 30px; background: #f9f9f9; border-bottom: 1px solid #eee; }}
            .stat {{ text-align: center; }}
            .stat-number {{ font-size: 36px; font-weight: bold; color: #e5a00d; }}
            .stat-label {{ font-size: 14px; color: #666; margin-top: 5px; }}
            .content {{ padding: 20px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th {{ background: #f9f9f9; padding: 15px; text-align: left; font-weight: bold; color: #333; border-bottom: 2px solid #e5a00d; }}
            .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #999; background: #f9f9f9; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📊 Weekly Notification Summary</h1>
                <p>{start_date.strftime('%B %d')} - {end_date.strftime('%B %d, %Y')}</p>
            </div>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-number">{total_count}</div>
                    <div class="stat-label">Total Notifications</div>
                </div>
                <div class="stat">
                    <div class="stat-number">{len(user_stats)}</div>
                    <div class="stat-label">Users Notified</div>
                </div>
                <div class="stat">
                    <div class="stat-number">{sum(u['episodes'] for u in user_stats.values())}</div>
                    <div class="stat-label">TV Episodes</div>
                </div>
                <div class="stat">
                    <div class="stat-number">{sum(u['movies'] for u in user_stats.values())}</div>
                    <div class="stat-label">Movies</div>
                </div>
            </div>
            
            <div class="content">
                <h2 style="margin-top: 0; color: #333;">Notifications by User</h2>
                <table>
                    <thead>
                        <tr>
                            <th>User</th>
                            <th style="text-align: center;">Total</th>
                            <th style="text-align: center;">Breakdown</th>
                            <th>Recent Notifications</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(user_rows)}
                    </tbody>
                </table>
            </div>
            
            <div class="footer">
                <p>This is an automated weekly summary from your BingeAlert</p>
                <p>Generated on {datetime.utcnow().strftime('%B %d, %Y at %I:%M %p')} UTC</p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return html


async def send_weekly_summary():
    """Send weekly summary email to admin"""
    logger.info("=" * 60)
    logger.info("Starting weekly summary email...")
    logger.info("=" * 60)
    
    db = SessionLocal()
    try:
        # Generate summary
        html = await generate_weekly_summary(db)
        
        if not html:
            logger.info("No notifications to summarize")
            return
        
        # Send email
        email_service = EmailService()
        
        # Send to admin email (from settings, or fallback to smtp_from)
        admin_email = settings.admin_email or settings.smtp_from
        
        await email_service.send_email(
            to_email=admin_email,
            subject="📊 Weekly Notification Summary - Plex Portal",
            html_body=html
        )
        
        logger.info(f"✅ Weekly summary sent to {admin_email}")
        
    except Exception as e:
        logger.error(f"Weekly summary failed: {e}")
    finally:
        db.close()


async def weekly_summary_worker():
    """Background worker that sends weekly summary every Sunday at 9 AM"""
    from app.background.utils import is_maintenance_active
    
    logger.info("📊 Weekly summary worker started - will run every Sunday at 9 AM UTC")
    
    while True:
        try:
            # Calculate seconds until next Sunday 9 AM UTC
            now = datetime.utcnow()
            
            # Find next Sunday
            days_until_sunday = (6 - now.weekday()) % 7
            if days_until_sunday == 0 and now.hour >= 9:
                days_until_sunday = 7  # If it's Sunday after 9 AM, wait until next Sunday
            
            next_sunday = now + timedelta(days=days_until_sunday)
            next_run = next_sunday.replace(hour=9, minute=0, second=0, microsecond=0)
            
            # Calculate sleep time
            sleep_seconds = (next_run - now).total_seconds()
            
            logger.info(f"Next weekly summary: {next_run.strftime('%A, %B %d at %I:%M %p')} UTC ({sleep_seconds/3600:.1f} hours)")
            
            # Sleep until next Sunday
            await asyncio.sleep(sleep_seconds)
            
            # Check maintenance before sending
            if is_maintenance_active():
                logger.info("🔧 Maintenance active — skipping weekly summary")
                continue
            
            # Send summary
            await send_weekly_summary()
            
        except Exception as e:
            logger.error(f"Weekly summary worker error: {e}")
            # Sleep 1 hour on error
            await asyncio.sleep(3600)
