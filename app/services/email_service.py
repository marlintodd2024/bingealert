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
    
    def render_episode_notification(self, series_title: str, episodes: List[dict]) -> str:
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
        
        return template.render(series_title=series_title, episodes=episodes)
    
    def render_movie_notification(self, movie_title: str, year: int = None) -> str:
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
        
        return template.render(movie_title=movie_title, year=year)
    
    async def process_pending_notifications(self, db):
        """Process all pending notifications in the queue"""
        pending = db.query(Notification).filter(Notification.sent == False).all()
        
        logger.info(f"Processing {len(pending)} pending notifications")
        
        for notification in pending:
            success = await self.send_email(
                to_email=notification.user.email,
                subject=notification.subject,
                html_body=notification.body
            )
            
            if success:
                notification.sent = True
                notification.sent_at = datetime.utcnow()
            else:
                notification.error_message = "SMTP send failed"
            
            db.commit()
        
        logger.info(f"Processed {len(pending)} notifications")
