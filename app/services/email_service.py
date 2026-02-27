import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Template
import logging
from typing import List
from datetime import datetime

from app.config import settings
from app.database import Notification

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password
        self.smtp_from = settings.smtp_from
        # Check if authentication is needed
        self.use_auth = bool(self.smtp_user and self.smtp_password and 
                            self.smtp_user.lower() not in ['none', ''])
    
    async def send_email(self, to_email: str, subject: str, html_body: str) -> bool:
        """Send an email via SMTP"""
        try:
            message = MIMEMultipart("alternative")
            message["From"] = self.smtp_from
            message["To"] = to_email
            message["Subject"] = subject
            
            # Add HTML body
            html_part = MIMEText(html_body, "html")
            message.attach(html_part)
            
            # Send email with or without authentication
            if self.use_auth:
                await aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    username=self.smtp_user,
                    password=self.smtp_password,
                    start_tls=True
                )
            else:
                # No authentication
                await aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port
                )
            
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False
    
    def render_episode_notification(self, series_title: str, episodes: List[dict], poster_url: str = None) -> str:
        """Render HTML email for new episode(s) notification"""
        template = Template("""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
                .container { max-width: 600px; margin: 0 auto; padding: 20px; }
                .header { background-color: #e5a00d; color: white; padding: 20px; text-align: center; }
                .content { background-color: #f9f9f9; padding: 20px; }
                .poster-section { text-align: center; margin-bottom: 20px; }
                .poster { max-width: 300px; width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); }
                .episode { background-color: white; margin: 10px 0; padding: 15px; border-left: 4px solid #e5a00d; }
                .footer { text-align: center; padding: 20px; font-size: 12px; color: #666; }
                .button { background-color: #e5a00d; color: white; padding: 10px 20px; text-decoration: none; display: inline-block; margin-top: 10px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>New Episode{% if episodes|length > 1 %}s{% endif %} Available!</h1>
                </div>
                <div class="content">
                    {% if poster_url %}
                    <div class="poster-section">
                        <img src="{{ poster_url }}" alt="{{ series_title }}" class="poster">
                    </div>
                    {% endif %}
                    <h2>{{ series_title }}</h2>
                    <p>The following episode{% if episodes|length > 1 %}s are{% else %} is{% endif %} now available to watch on Plex:</p>
                    
                    {% for ep in episodes %}
                    <div class="episode">
                        <strong>S{{ "%02d"|format(ep.season) }}E{{ "%02d"|format(ep.episode) }}</strong>
                        {% if ep.title %} - {{ ep.title }}{% endif %}
                        {% if ep.air_date %}<br><small>Aired: {{ ep.air_date }}</small>{% endif %}
                    </div>
                    {% endfor %}
                    
                    <p style="margin-top: 20px;">Head over to Plex to start watching!</p>
                </div>
                <div class="footer">
                    <p>This is an automated notification from your Plex Notification Portal</p>
                </div>
            </div>
        </body>
        </html>
        """)
        
        return template.render(series_title=series_title, episodes=episodes, poster_url=poster_url)
    
    def render_movie_notification(self, movie_title: str, year: int = None, poster_url: str = None) -> str:
        """Render HTML email for new movie notification"""
        template = Template("""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
                .container { max-width: 600px; margin: 0 auto; padding: 20px; }
                .header { background-color: #e5a00d; color: white; padding: 20px; text-align: center; }
                .content { background-color: #f9f9f9; padding: 20px; text-align: center; }
                .poster-section { margin: 20px 0; }
                .poster { max-width: 300px; width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); }
                .footer { text-align: center; padding: 20px; font-size: 12px; color: #666; }
                .movie-title { font-size: 24px; font-weight: bold; margin: 20px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎬 Movie Now Available!</h1>
                </div>
                <div class="content">
                    {% if poster_url %}
                    <div class="poster-section">
                        <img src="{{ poster_url }}" alt="{{ movie_title }}" class="poster">
                    </div>
                    {% endif %}
                    <div class="movie-title">{{ movie_title }}{% if year %} ({{ year }}){% endif %}</div>
                    <p>Your requested movie is now available to watch on Plex!</p>
                    <p style="margin-top: 30px;">Grab some popcorn and enjoy! 🍿</p>
                </div>
                <div class="footer">
                    <p>This is an automated notification from your Plex Notification Portal</p>
                </div>
            </div>
        </body>
        </html>
        """)
        
        return template.render(movie_title=movie_title, year=year, poster_url=poster_url)
    
    async def process_pending_notifications(self, db):
        """Process all pending notifications with smart batching (respects send_after delay)"""
        from datetime import datetime, timedelta
        
        now = datetime.utcnow()
        
        # Get notifications ready to send (send_after is null or in the past)
        ready_notifications = db.query(Notification).filter(
            Notification.sent == False,
            (Notification.send_after == None) | (Notification.send_after <= now)
        ).all()
        
        if not ready_notifications:
            return
        
        logger.info(f"Found {len(ready_notifications)} notifications ready to process")
        
        # Smart batching: Group TV episodes by user + series
        # Check Sonarr queue to see if more episodes are coming
        from app.services.sonarr_service import SonarrService
        sonarr = SonarrService()
        
        # Separate TV episodes from movies and other notification types
        tv_notifications = [n for n in ready_notifications if n.notification_type == "episode" and n.series_id]
        movie_notifications = [n for n in ready_notifications if n.notification_type == "movie"]
        other_notifications = [n for n in ready_notifications if n.notification_type in ("quality_waiting", "coming_soon", "weekly_summary", "issue_resolved", "issue_reported_admin")]
        
        # Process TV episodes with smart batching
        processed_tv = set()
        for notif in tv_notifications:
            if notif.id in processed_tv:
                continue
            
            # Check if more episodes are in Sonarr queue
            queue_episodes = await sonarr.get_series_episodes_in_queue(notif.series_id)
            
            # ALSO check if there are more pending notifications for this series coming soon
            # (episodes that downloaded but haven't reached their send_after time yet)
            future_window = now + timedelta(minutes=10)  # Look 10 minutes ahead
            pending_notifications = db.query(Notification).filter(
                Notification.sent == False,
                Notification.user_id == notif.user_id,
                Notification.series_id == notif.series_id,
                Notification.notification_type == "episode",
                Notification.id != notif.id,  # Not this one
                Notification.send_after > now,  # In the future
                Notification.send_after <= future_window  # But within 10 min
            ).count()
            
            # Calculate how old this notification is
            age_minutes = (now - notif.created_at).total_seconds() / 60
            max_wait_minutes = 20  # Maximum 20 minutes total wait
            
            # Extend if: more episodes in queue OR more notifications pending
            if (queue_episodes or pending_notifications > 0) and age_minutes < max_wait_minutes:
                # More episodes coming! Extend delay
                extend_by = min(3, max_wait_minutes - age_minutes)  # Extend by 3 min or remaining time
                new_send_after = now + timedelta(minutes=extend_by)
                notif.send_after = new_send_after
                db.commit()
                
                reason = []
                if queue_episodes:
                    reason.append(f"{len(queue_episodes)} in Sonarr queue")
                if pending_notifications:
                    reason.append(f"{pending_notifications} pending notifications")
                
                logger.info(f"Extended delay for {notif.subject} - {', '.join(reason)} (waiting {extend_by} more minutes, age: {age_minutes:.1f}m)")
                processed_tv.add(notif.id)
                continue
            
            # No more episodes coming OR hit max wait time - batch and send!
            # Find all notifications for same user + series that are ready OR will be ready soon
            # This ensures we don't split episodes that are just a minute apart
            soon = now + timedelta(minutes=5)  # Include notifications ready within 5 minutes (increased from 2)
            batch = db.query(Notification).filter(
                Notification.sent == False,
                Notification.user_id == notif.user_id,
                Notification.series_id == notif.series_id,
                Notification.notification_type == "episode",
                (Notification.send_after == None) | (Notification.send_after <= soon)
            ).all()
            
            if len(batch) > 1:
                # Multiple episodes - send combined email
                logger.info(f"Batching {len(batch)} episode notifications for user {notif.user.email}")
                
                # Get episode details from tracking table
                from app.database import EpisodeTracking
                episodes = []
                series_title = None
                for b in batch:
                    # Extract episode info from subject (e.g., "New Episode: Series S01E05")
                    import re
                    match = re.search(r'S(\d+)E(\d+)', b.subject)
                    if match:
                        season_num = int(match.group(1))
                        episode_num = int(match.group(2))
                        
                        # Try to get episode title from tracking table
                        tracking = db.query(EpisodeTracking).filter(
                            EpisodeTracking.series_id == b.series_id,
                            EpisodeTracking.season_number == season_num,
                            EpisodeTracking.episode_number == episode_num
                        ).first()
                        
                        episodes.append({
                            'season': season_num,
                            'episode': episode_num,
                            'title': tracking.episode_title if tracking and tracking.episode_title else ''
                        })
                    # Extract series title from first notification
                    if not series_title:
                        series_title = b.subject.split(':')[1].split('S')[0].strip() if ':' in b.subject else "Series"
                
                # Sort episodes by season, then episode number
                episodes.sort(key=lambda x: (x['season'], x['episode']))
                
                # Get poster from one of the notifications (they're all the same series)
                # We'll use the body from the first notification but update episode list
                from app.services.tmdb_service import TMDBService
                from app.config import settings
                tmdb_service = TMDBService(settings.jellyseerr_url, settings.jellyseerr_api_key)
                poster_url = await tmdb_service.get_tv_poster(notif.request.tmdb_id)
                
                # Render batched email
                html_body = self.render_episode_notification(
                    series_title=series_title,
                    episodes=episodes,
                    poster_url=poster_url
                )
                
                subject = f"New Episodes: {series_title} ({len(episodes)} episodes)"
                
                # Send one email
                success = await self.send_email(
                    to_email=notif.user.email,
                    subject=subject,
                    html_body=html_body
                )
                
                # Mark all batched notifications as sent
                for b in batch:
                    if success:
                        b.sent = True
                        b.sent_at = datetime.utcnow()
                    else:
                        b.error_message = "SMTP send failed"
                    processed_tv.add(b.id)
                
            else:
                # Single episode - send as-is
                success = await self.send_email(
                    to_email=notif.user.email,
                    subject=notif.subject,
                    html_body=notif.body
                )
                
                if success:
                    notif.sent = True
                    notif.sent_at = datetime.utcnow()
                else:
                    notif.error_message = "SMTP send failed"
                
                processed_tv.add(notif.id)
            
            db.commit()
        
        # Process movie notifications (no batching needed)
        for notif in movie_notifications:
            success = await self.send_email(
                to_email=notif.user.email,
                subject=notif.subject,
                html_body=notif.body
            )
            
            if success:
                notif.sent = True
                notif.sent_at = datetime.utcnow()
            else:
                notif.error_message = "SMTP send failed"
            
            db.commit()
        
        # Process other notifications (quality_waiting, coming_soon, weekly_summary)
        for notif in other_notifications:
            success = await self.send_email(
                to_email=notif.user.email,
                subject=notif.subject,
                html_body=notif.body
            )
            
            if success:
                notif.sent = True
                notif.sent_at = datetime.utcnow()
            else:
                notif.error_message = "SMTP send failed"
            
            db.commit()
        
        logger.info(f"Processed {len(processed_tv)} TV notifications, {len(movie_notifications)} movie notifications, {len(other_notifications)} other notifications")
    
    def render_coming_soon_notification(self, title: str, media_type: str, premiere_date: str, poster_url: str = None) -> str:
        """Render 'coming soon' email notification with poster"""
        
        media_icon = "📺" if media_type == "tv" else "🎬"
        media_label = "TV Show" if media_type == "tv" else "Movie"
        
        template = Template("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);">
    <div style="max-width: 600px; margin: 40px auto; background: linear-gradient(135deg, #0f3460 0%, #16213e 100%); border-radius: 20px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.5);">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, rgba(229, 160, 13, 0.2) 0%, rgba(229, 160, 13, 0.05) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(229, 160, 13, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">📅</div>
            <h1 style="margin: 0; color: #e5a00d; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">Coming Soon to Plex!</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">Your requested content will be available soon</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            {% if poster_url %}
            <div style="text-align: center; margin-bottom: 30px;">
                <img src="{{ poster_url }}" alt="{{ title }}" style="max-width: 300px; width: 100%; height: auto; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            </div>
            {% endif %}
            
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #e5a00d; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <div style="display: flex; align-items: center; margin-bottom: 15px;">
                    <span style="font-size: 32px; margin-right: 15px;">{{ media_icon }}</span>
                    <div>
                        <h2 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">{{ title }}</h2>
                        <p style="margin: 5px 0 0 0; color: rgba(255,255,255,0.6); font-size: 14px; text-transform: uppercase; letter-spacing: 1px;">{{ media_label }}</p>
                    </div>
                </div>
            </div>
            
            <div style="background: linear-gradient(135deg, rgba(229, 160, 13, 0.15) 0%, rgba(229, 160, 13, 0.05) 100%); border-radius: 12px; padding: 25px; text-align: center; margin-bottom: 25px;">
                <div style="font-size: 14px; color: rgba(255,255,255,0.7); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px;">Premiere Date</div>
                <div style="font-size: 28px; color: #e5a00d; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">{{ premiere_date }}</div>
            </div>
            
            <div style="background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; text-align: center;">
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6;">
                    We'll automatically download and notify you once <strong style="color: #e5a00d;">{{ title }}</strong> becomes available.
                </p>
                <p style="margin: 15px 0 0 0; color: rgba(255,255,255,0.5); font-size: 13px;">
                    ✨ No action needed on your part!
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">
                🎬 Plex Notification Portal
            </p>
            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.3); font-size: 11px;">
                Sit back and relax - we'll let you know when it's ready to watch!
            </p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(
            title=title,
            media_type=media_type,
            media_label=media_label,
            media_icon=media_icon,
            premiere_date=premiere_date,
            poster_url=poster_url
        )
    
    def render_quality_waiting_notification(self, title: str, media_type: str, quality_profile: str, poster_url: str = None) -> str:
        """Render 'waiting for quality' email notification with poster"""
        
        media_icon = "📺" if media_type == "tv" else "🎬"
        media_label = "TV Show" if media_type == "tv" else "Movie"
        
        template = Template("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);">
    <div style="max-width: 600px; margin: 40px auto; background: linear-gradient(135deg, #0f3460 0%, #16213e 100%); border-radius: 20px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.5);">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, rgba(138, 43, 226, 0.2) 0%, rgba(138, 43, 226, 0.05) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(138, 43, 226, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">⏳</div>
            <h1 style="margin: 0; color: #8a2be2; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">Waiting for Better Quality</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">We're holding out for the quality you requested</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            {% if poster_url %}
            <div style="text-align: center; margin-bottom: 30px;">
                <img src="{{ poster_url }}" alt="{{ title }}" style="max-width: 300px; width: 100%; height: auto; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            </div>
            {% endif %}
            
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #8a2be2; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <div style="display: flex; align-items: center; margin-bottom: 15px;">
                    <span style="font-size: 32px; margin-right: 15px;">{{ media_icon }}</span>
                    <div>
                        <h2 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">{{ title }}</h2>
                        <p style="margin: 5px 0 0 0; color: rgba(255,255,255,0.6); font-size: 14px; text-transform: uppercase; letter-spacing: 1px;">{{ media_label }}</p>
                    </div>
                </div>
            </div>
            
            <div style="background: linear-gradient(135deg, rgba(138, 43, 226, 0.15) 0%, rgba(138, 43, 226, 0.05) 100%); border-radius: 12px; padding: 25px; text-align: center; margin-bottom: 25px;">
                <div style="font-size: 14px; color: rgba(255,255,255,0.7); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px;">Waiting For</div>
                <div style="font-size: 28px; color: #8a2be2; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">{{ quality_profile }}</div>
            </div>
            
            <div style="background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; text-align: center;">
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6;">
                    This content is available, but not yet in <strong style="color: #8a2be2;">{{ quality_profile }}</strong> quality.
                </p>
                <p style="margin: 15px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">
                    We're automatically monitoring for the quality you requested. You'll be notified as soon as it's available!
                </p>
                <p style="margin: 15px 0 0 0; color: rgba(255,255,255,0.5); font-size: 13px;">
                    ✨ Worth the wait for the best experience!
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">
                🎬 Plex Notification Portal
            </p>
            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.3); font-size: 11px;">
                Quality matters - we're on it!
            </p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(
            title=title,
            media_type=media_type,
            media_label=media_label,
            media_icon=media_icon,
            quality_profile=quality_profile,
            poster_url=poster_url
        )
    
    def render_issue_resolved_notification(self, title: str, media_type: str, issue_type: str = None, poster_url: str = None) -> str:
        """Render 'issue resolved' email notification sent to the user who reported the issue"""
        
        media_icon = "📺" if media_type == "tv" else "🎬"
        media_label = "TV Show" if media_type == "tv" else "Movie"
        issue_label = issue_type.capitalize() if issue_type else "Reported"
        
        template = Template("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);">
    <div style="max-width: 600px; margin: 40px auto; background: linear-gradient(135deg, #0f3460 0%, #16213e 100%); border-radius: 20px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.5);">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, rgba(76, 175, 80, 0.2) 0%, rgba(76, 175, 80, 0.05) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(76, 175, 80, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">✅</div>
            <h1 style="margin: 0; color: #4caf50; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">Issue Resolved!</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">A new version has been downloaded</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            {% if poster_url %}
            <div style="text-align: center; margin-bottom: 30px;">
                <img src="{{ poster_url }}" alt="{{ title }}" style="max-width: 300px; width: 100%; height: auto; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            </div>
            {% endif %}
            
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #4caf50; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <div style="display: flex; align-items: center; margin-bottom: 15px;">
                    <span style="font-size: 32px; margin-right: 15px;">{{ media_icon }}</span>
                    <div>
                        <h2 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">{{ title }}</h2>
                        <p style="margin: 5px 0 0 0; color: rgba(255,255,255,0.6); font-size: 14px; text-transform: uppercase; letter-spacing: 1px;">{{ media_label }}</p>
                    </div>
                </div>
            </div>
            
            <div style="background: linear-gradient(135deg, rgba(76, 175, 80, 0.15) 0%, rgba(76, 175, 80, 0.05) 100%); border-radius: 12px; padding: 25px; text-align: center; margin-bottom: 25px;">
                <div style="font-size: 14px; color: rgba(255,255,255,0.7); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px;">Issue Type</div>
                <div style="font-size: 28px; color: #4caf50; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">{{ issue_label }}</div>
            </div>
            
            <div style="background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; text-align: center;">
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6;">
                    The {{ issue_label | lower }} issue you reported with <strong style="color: #4caf50;">{{ title }}</strong> has been addressed and a new version has been downloaded.
                </p>
                <p style="margin: 15px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">
                    If you're still experiencing problems, please report the issue again in Seerr.
                </p>
                <p style="margin: 15px 0 0 0; color: rgba(255,255,255,0.5); font-size: 13px;">
                    🍿 Enjoy watching!
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">
                🎬 Plex Notification Portal
            </p>
            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.3); font-size: 11px;">
                We're here to make sure everything works perfectly!
            </p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(
            title=title,
            media_type=media_type,
            media_label=media_label,
            media_icon=media_icon,
            issue_label=issue_label,
            poster_url=poster_url
        )
    
    def render_issue_reported_admin_notification(self, title: str, media_type: str, issue_type: str,
                                                  issue_message: str, reported_by: str, autofix_mode: str) -> str:
        """Render admin notification email when an issue is reported"""
        
        media_icon = "📺" if media_type == "tv" else "🎬"
        media_label = "TV Show" if media_type == "tv" else "Movie"
        issue_label = issue_type.capitalize() if issue_type else "Other"
        
        mode_text = {
            "manual": "⏸️ Manual mode — action required in the admin dashboard.",
            "auto": "🤖 Auto-fix mode — blacklist & re-search has been triggered automatically.",
            "auto_notify": "🤖 Auto-fix mode — blacklist & re-search has been triggered automatically."
        }.get(autofix_mode, "Unknown mode")
        
        template = Template("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);">
    <div style="max-width: 600px; margin: 40px auto; background: linear-gradient(135deg, #0f3460 0%, #16213e 100%); border-radius: 20px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.5);">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, rgba(244, 67, 54, 0.2) 0%, rgba(244, 67, 54, 0.05) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(244, 67, 54, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">🚨</div>
            <h1 style="margin: 0; color: #f44336; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">Issue Reported</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">A user has reported a problem</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #f44336; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <div style="display: flex; align-items: center; margin-bottom: 15px;">
                    <span style="font-size: 32px; margin-right: 15px;">{{ media_icon }}</span>
                    <div>
                        <h2 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">{{ title }}</h2>
                        <p style="margin: 5px 0 0 0; color: rgba(255,255,255,0.6); font-size: 14px; text-transform: uppercase; letter-spacing: 1px;">{{ media_label }}</p>
                    </div>
                </div>
            </div>
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 25px;">
                <div style="background: rgba(244, 67, 54, 0.1); border-radius: 12px; padding: 20px; text-align: center;">
                    <div style="font-size: 12px; color: rgba(255,255,255,0.5); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">Issue Type</div>
                    <div style="font-size: 20px; color: #f44336; font-weight: 600;">{{ issue_label }}</div>
                </div>
                <div style="background: rgba(229, 160, 13, 0.1); border-radius: 12px; padding: 20px; text-align: center;">
                    <div style="font-size: 12px; color: rgba(255,255,255,0.5); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">Reported By</div>
                    <div style="font-size: 20px; color: #e5a00d; font-weight: 600;">{{ reported_by }}</div>
                </div>
            </div>
            
            {% if issue_message %}
            <div style="background: rgba(255,255,255,0.05); border-radius: 12px; padding: 20px; margin-bottom: 25px;">
                <div style="font-size: 12px; color: rgba(255,255,255,0.5); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px;">User Message</div>
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6; font-style: italic;">"{{ issue_message }}"</p>
            </div>
            {% endif %}
            
            <div style="background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; text-align: center; border: 1px solid rgba(255,255,255,0.1);">
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6;">
                    {{ mode_text }}
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">
                🎬 Plex Notification Portal — Admin Alert
            </p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(
            title=title,
            media_type=media_type,
            media_label=media_label,
            media_icon=media_icon,
            issue_label=issue_label,
            issue_message=issue_message,
            reported_by=reported_by,
            mode_text=mode_text
        )

