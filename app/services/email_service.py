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
        
        # Separate TV episodes from movies
        tv_notifications = [n for n in ready_notifications if n.notification_type == "episode" and n.series_id]
        movie_notifications = [n for n in ready_notifications if n.notification_type == "movie"]
        
        # Process TV episodes with smart batching
        processed_tv = set()
        for notif in tv_notifications:
            if notif.id in processed_tv:
                continue
            
            # Check if more episodes are in Sonarr queue
            queue_episodes = await sonarr.get_series_episodes_in_queue(notif.series_id)
            
            # Calculate how old this notification is
            age_minutes = (now - notif.created_at).total_seconds() / 60
            max_wait_minutes = 15  # Maximum 15 minutes total wait
            
            if queue_episodes and age_minutes < max_wait_minutes:
                # More episodes downloading! Extend delay
                extend_by = min(3, max_wait_minutes - age_minutes)  # Extend by 3 min or remaining time
                new_send_after = now + timedelta(minutes=extend_by)
                notif.send_after = new_send_after
                db.commit()
                logger.info(f"Extended delay for {notif.subject} - {len(queue_episodes)} episodes still in queue (waiting {extend_by} more minutes)")
                processed_tv.add(notif.id)
                continue
            
            # No more episodes coming OR hit max wait time - batch and send!
            # Find all notifications for same user + series that are ready
            batch = db.query(Notification).filter(
                Notification.sent == False,
                Notification.user_id == notif.user_id,
                Notification.series_id == notif.series_id,
                Notification.notification_type == "episode",
                (Notification.send_after == None) | (Notification.send_after <= now)
            ).all()
            
            if len(batch) > 1:
                # Multiple episodes - send combined email
                logger.info(f"Batching {len(batch)} episode notifications for user {notif.user.email}")
                
                # Parse episodes from existing notifications
                episodes = []
                series_title = None
                for b in batch:
                    # Extract episode info from subject (e.g., "New Episode: Series S01E05")
                    import re
                    match = re.search(r'S(\d+)E(\d+)', b.subject)
                    if match:
                        episodes.append({
                            'season': int(match.group(1)),
                            'episode': int(match.group(2)),
                            'title': ''  # We don't have title in subject, that's ok
                        })
                    # Extract series title from first notification
                    if not series_title:
                        series_title = b.subject.split(':')[1].split('S')[0].strip() if ':' in b.subject else "Series"
                
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
        
        logger.info(f"Processed {len(processed_tv)} TV notifications, {len(movie_notifications)} movie notifications")
