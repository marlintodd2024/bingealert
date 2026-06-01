import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment
import logging
from typing import List, Optional
from datetime import datetime

from app.config import normalize_smtp_security, settings
from app.database import Notification, User

logger = logging.getLogger(__name__)


# All notification templates render through this single Environment so HTML
# autoescape applies uniformly. Variables like series_title, episode title,
# issue_message, and the maintenance description are user-supplied (or
# upstream-supplied) strings; without autoescape, anything containing < > "
# was injected verbatim into the resulting email -- stored XSS for any HTML
# email client that renders scripts.
#
# We use autoescape=True (not select_autoescape(["html","xml"])) because
# select_autoescape decides based on the template's source filename
# extension. Every template here is constructed via from_string(), so it
# has no filename -- the heuristic falls back to default_for_string which
# is True in Jinja2 3.x but the config is fragile and confusing to
# scanners. autoescape=True is unambiguous: every render escapes.
_template_env = Environment(autoescape=True)


def _safe_url(url: Optional[str]) -> Optional[str]:
    """Return url only if it's a sane http(s) link. Used for poster URLs to
    prevent javascript:/data: schemes slipping into <img src="...">."""
    if not url:
        return None
    u = url.strip()
    return u if u.startswith(("http://", "https://")) else None


def Template(source):  # noqa: N802 -- preserve the existing call sites
    # Drop-in shim: previous code did `from jinja2 import Template; Template(...)`.
    # Routing through the autoescape-enabled Environment without rewriting every
    # call site. Same callable shape, escapes by default.
    return _template_env.from_string(source)


class EmailService:
    def __init__(self):
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        try:
            self.smtp_security = normalize_smtp_security(settings.smtp_security)
        except ValueError:
            logger.warning(
                "Invalid smtp_security %r; falling back to STARTTLS",
                settings.smtp_security,
            )
            self.smtp_security = "starttls"
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password
        self.smtp_from = settings.smtp_from
        # Check if authentication is needed
        self.use_auth = bool(self.smtp_user and self.smtp_password and 
                            self.smtp_user.lower() not in ['none', ''])
    
    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        user: Optional[User] = None,
    ) -> bool:
        """Send an email via SMTP. When `user` is supplied AND
        settings.public_base_url is configured AND the user has a
        calendar_token, a footer with their per-user .ics subscription URL is
        injected just before </body>. Skipping any of those three quietly
        omits the footer — the email still goes out."""
        try:
            html_body = self._inject_calendar_footer(html_body, user)
            message = MIMEMultipart("alternative")
            message["From"] = self.smtp_from
            message["To"] = to_email
            message["Subject"] = subject

            # Add HTML body
            html_part = MIMEText(html_body, "html")
            message.attach(html_part)

            tls_kwargs = {
                "use_tls": self.smtp_security == "ssl",
                "start_tls": self.smtp_security == "starttls",
            }
            
            # Send email with or without authentication
            if self.use_auth:
                await aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    username=self.smtp_user,
                    password=self.smtp_password,
                    **tls_kwargs,
                )
            else:
                # No authentication
                await aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    **tls_kwargs,
                )
            
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False
    
    def _inject_calendar_footer(self, html_body: str, user: Optional[User]) -> str:
        """Append a 'Subscribe to your upcoming-episodes calendar' footer.

        Returns html_body unchanged if any prerequisite is missing (no user,
        no public_base_url configured, no calendar_token on the row, no
        </body> close tag in the rendered HTML). All three substitutions are
        server-controlled values, so we build the URL with f-strings and
        rely on the secret token being a urlsafe random string.
        """
        if not user or not user.calendar_token:
            return html_body
        base = (settings.public_base_url or "").strip().rstrip("/")
        # Defensive scheme check: the value flows into an <a href> in the
        # recipient's mailbox, so a non-http(s) scheme (e.g. javascript:) would
        # render as a clickable XSS vector. The /admin/config saver validates
        # this on write, but skip again here so a hand-edited config.json
        # can't bypass it.
        if not base or not base.startswith(("http://", "https://")):
            return html_body
        if "</body>" not in html_body:
            return html_body

        url_https = f"{base}/calendar/{user.calendar_token}.ics"
        # webcal:// triggers the OS calendar-subscribe handler on most
        # platforms; the https URL is the literal fallback users can paste
        # into clients that don't honor the scheme rewrite.
        url_webcal = url_https
        for scheme in ("https://", "http://"):
            if url_webcal.startswith(scheme):
                url_webcal = "webcal://" + url_webcal[len(scheme):]
                break

        footer = (
            '<div style="max-width:600px;margin:24px auto 0;padding:16px 20px;'
            "border-top:1px solid #e5e5e5;font-family:-apple-system,Segoe UI,"
            'Roboto,sans-serif;font-size:13px;color:#555;">'
            "<p style=\"margin:0 0 6px;\">📅 <strong>Subscribe to your "
            "upcoming-episodes calendar.</strong></p>"
            f'<p style="margin:0 0 6px;"><a href="{url_webcal}" '
            'style="color:#e5a00d;text-decoration:none;">Add to your calendar app</a></p>'
            '<p style="margin:0;font-size:11px;color:#888;">Or paste this URL '
            f'into your calendar app: <code>{url_https}</code></p>'
            "</div>\n"
        )
        return html_body.replace("</body>", footer + "</body>", 1)

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
                    <p>This is an automated notification from BingeAlert</p>
                </div>
            </div>
        </body>
        </html>
        """)
        
        return template.render(series_title=series_title, episodes=episodes, poster_url=_safe_url(poster_url))
    
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
                    <p>This is an automated notification from BingeAlert</p>
                </div>
            </div>
        </body>
        </html>
        """)
        
        return template.render(movie_title=movie_title, year=year, poster_url=_safe_url(poster_url))
    
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
        
        # Filter out notifications for inactive users
        active_notifications = []
        skipped_inactive = 0
        for n in ready_notifications:
            if hasattr(n.user, 'is_active') and not n.user.is_active:
                # Mark as sent to clear the queue (don't keep retrying for inactive users)
                n.sent = True
                n.error_message = "Skipped — user deactivated"
                skipped_inactive += 1
            else:
                active_notifications.append(n)
        
        if skipped_inactive:
            db.commit()
            logger.info(f"Skipped {skipped_inactive} notification(s) for inactive users")
        
        ready_notifications = active_notifications
        if not ready_notifications:
            return
        
        logger.info(f"Found {len(ready_notifications)} notifications ready to process")
        
        # Smart batching: Group TV episodes by user + series
        # Check Sonarr queue to see if more episodes are coming
        from app.services.sonarr_service import SonarrService, get_all_sonarr_instances
        sonarr_instances = get_all_sonarr_instances()
        
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
            # Check if more episodes are in any Sonarr instance's queue
            queue_episodes = []
            for sonarr in sonarr_instances:
                qe = await sonarr.get_series_episodes_in_queue(notif.series_id)
                if qe:
                    queue_episodes.extend(qe)
                    break  # Found in this instance, no need to check others
            
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
                    html_body=html_body,
                    user=notif.user,
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
                    html_body=notif.body,
                    user=notif.user,
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
                html_body=notif.body,
                user=notif.user,
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
                html_body=notif.body,
                user=notif.user,
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
                🎬 BingeAlert
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
            poster_url=_safe_url(poster_url)
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
                🎬 BingeAlert
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
            poster_url=_safe_url(poster_url)
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
                🎬 BingeAlert
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
            poster_url=_safe_url(poster_url)
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
                🎬 BingeAlert — Admin Alert
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

    def render_maintenance_announcement(self, title: str, description: str, start_time: str, end_time: str, duration: str) -> str:
        """Render maintenance window announcement email"""
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
        <div style="background: linear-gradient(135deg, rgba(255, 152, 0, 0.25) 0%, rgba(255, 152, 0, 0.08) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(255, 152, 0, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">🔧</div>
            <h1 style="margin: 0; color: #ff9800; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">Scheduled Maintenance</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">Plex will be temporarily unavailable</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #ff9800; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <h2 style="margin: 0 0 10px 0; color: #ffffff; font-size: 22px; font-weight: 600;">{{ title }}</h2>
                {% if description %}
                <p style="margin: 0; color: rgba(255,255,255,0.7); font-size: 15px; line-height: 1.6;">{{ description }}</p>
                {% endif %}
            </div>
            
            <div style="display: flex; gap: 15px; margin-bottom: 25px;">
                <div style="flex: 1; background: linear-gradient(135deg, rgba(255, 152, 0, 0.15) 0%, rgba(255, 152, 0, 0.05) 100%); border-radius: 12px; padding: 20px; text-align: center;">
                    <div style="font-size: 12px; color: rgba(255,255,255,0.6); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">Starts</div>
                    <div style="font-size: 18px; color: #ff9800; font-weight: 700;">{{ start_time }}</div>
                </div>
                <div style="flex: 1; background: linear-gradient(135deg, rgba(255, 152, 0, 0.15) 0%, rgba(255, 152, 0, 0.05) 100%); border-radius: 12px; padding: 20px; text-align: center;">
                    <div style="font-size: 12px; color: rgba(255,255,255,0.6); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">Ends</div>
                    <div style="font-size: 18px; color: #ff9800; font-weight: 700;">{{ end_time }}</div>
                </div>
            </div>
            
            <div style="background: rgba(255, 152, 0, 0.1); border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 25px;">
                <div style="font-size: 14px; color: rgba(255,255,255,0.6); margin-bottom: 5px;">Estimated Duration</div>
                <div style="font-size: 24px; color: #ff9800; font-weight: 700;">{{ duration }}</div>
            </div>
            
            <div style="background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; text-align: center;">
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6;">
                    Plex and related services may be unavailable during this time. We'll send you another email when everything is back up!
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">🎬 BingeAlert</p>
            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.3); font-size: 11px;">We'll have things back to normal as soon as possible!</p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(
            title=title,
            description=description,
            start_time=start_time,
            end_time=end_time,
            duration=duration
        )

    def render_maintenance_reminder(self, title: str, description: str, start_time: str, end_time: str, duration: str, minutes_until: int) -> str:
        """Render maintenance window reminder email (sent ~1 hour before)"""
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
        <div style="background: linear-gradient(135deg, rgba(255, 87, 34, 0.25) 0%, rgba(255, 87, 34, 0.08) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(255, 87, 34, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">⏰</div>
            <h1 style="margin: 0; color: #ff5722; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">Maintenance Starting Soon!</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">Plex will be going down in approximately {{ minutes_until }} minutes</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #ff5722; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <h2 style="margin: 0 0 10px 0; color: #ffffff; font-size: 22px; font-weight: 600;">{{ title }}</h2>
                {% if description %}
                <p style="margin: 0; color: rgba(255,255,255,0.7); font-size: 15px; line-height: 1.6;">{{ description }}</p>
                {% endif %}
            </div>
            
            <div style="background: linear-gradient(135deg, rgba(255, 87, 34, 0.2) 0%, rgba(255, 87, 34, 0.05) 100%); border-radius: 12px; padding: 25px; text-align: center; margin-bottom: 25px;">
                <div style="font-size: 48px; margin-bottom: 10px;">⚠️</div>
                <div style="font-size: 20px; color: #ff5722; font-weight: 700;">Starting in ~{{ minutes_until }} minutes</div>
                <div style="font-size: 14px; color: rgba(255,255,255,0.6); margin-top: 8px;">{{ start_time }} — {{ end_time }}</div>
                <div style="font-size: 13px; color: rgba(255,255,255,0.5); margin-top: 5px;">Estimated duration: {{ duration }}</div>
            </div>
            
            <div style="background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; text-align: center;">
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6;">
                    If you're currently watching something, now is a good time to finish up. We'll email you again when everything is back online!
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">🎬 BingeAlert</p>
            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.3); font-size: 11px;">Heads up! Maintenance starts soon.</p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(
            title=title,
            description=description,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            minutes_until=minutes_until
        )

    def render_maintenance_complete(self, title: str, description: str = None) -> str:
        """Render maintenance complete / we're back email"""
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
        <div style="background: linear-gradient(135deg, rgba(76, 175, 80, 0.25) 0%, rgba(76, 175, 80, 0.08) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(76, 175, 80, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">✅</div>
            <h1 style="margin: 0; color: #4caf50; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">We're Back!</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">Maintenance is complete — Plex is back online</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #4caf50; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <h2 style="margin: 0 0 10px 0; color: #ffffff; font-size: 22px; font-weight: 600;">{{ title }}</h2>
                {% if description %}
                <p style="margin: 0; color: rgba(255,255,255,0.7); font-size: 15px; line-height: 1.6;">{{ description }}</p>
                {% endif %}
            </div>
            
            <div style="background: linear-gradient(135deg, rgba(76, 175, 80, 0.15) 0%, rgba(76, 175, 80, 0.05) 100%); border-radius: 12px; padding: 30px; text-align: center; margin-bottom: 25px;">
                <div style="font-size: 64px; margin-bottom: 15px;">🎉</div>
                <div style="font-size: 22px; color: #4caf50; font-weight: 700;">All Systems Operational</div>
                <div style="font-size: 14px; color: rgba(255,255,255,0.6); margin-top: 10px;">Everything is back to normal. Happy streaming!</div>
            </div>
            
            <div style="background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; text-align: center;">
                <p style="margin: 0; color: rgba(255,255,255,0.8); font-size: 15px; line-height: 1.6;">
                    Plex and all related services are now fully operational. If you experience any issues, please let your admin know.
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">🎬 BingeAlert</p>
            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.3); font-size: 11px;">Thanks for your patience! Enjoy your shows. 🍿</p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(
            title=title,
            description=description
        )

    def render_maintenance_cancelled(self, title: str) -> str:
        """Render maintenance cancelled email"""
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
        <div style="background: linear-gradient(135deg, rgba(33, 150, 243, 0.25) 0%, rgba(33, 150, 243, 0.08) 100%); padding: 30px; text-align: center; border-bottom: 2px solid rgba(33, 150, 243, 0.3);">
            <div style="font-size: 48px; margin-bottom: 10px;">ℹ️</div>
            <h1 style="margin: 0; color: #2196f3; font-size: 28px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">Maintenance Cancelled</h1>
            <p style="margin: 10px 0 0 0; color: rgba(255,255,255,0.7); font-size: 14px;">Good news — no downtime!</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 30px;">
            <div style="background: rgba(255,255,255,0.05); border-left: 4px solid #2196f3; padding: 25px; border-radius: 12px; margin-bottom: 25px;">
                <h2 style="margin: 0 0 10px 0; color: #ffffff; font-size: 22px; font-weight: 600;">{{ title }}</h2>
                <p style="margin: 0; color: rgba(255,255,255,0.7); font-size: 15px; line-height: 1.6;">
                    The previously scheduled maintenance has been cancelled. No downtime will occur — continue enjoying Plex as usual!
                </p>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: rgba(0,0,0,0.2); padding: 25px 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
            <p style="margin: 0; color: rgba(255,255,255,0.5); font-size: 12px;">🎬 BingeAlert</p>
            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.3); font-size: 11px;">Nothing to see here — carry on streaming! 🍿</p>
        </div>
    </div>
</body>
</html>
        """)
        
        return template.render(title=title)

    async def send_maintenance_email_to_all_users(self, db, email_type: str, window) -> dict:
        """Send a maintenance email to all users. Returns dict with sent/failed counts."""
        from app.database import User
        
        users = db.query(User).filter(User.is_active == True).all()
        if not users:
            logger.warning("No active users found to send maintenance email")
            return {"sent": 0, "failed": 0, "total": 0}
        
        # Format times for display
        start_str = window.start_time.strftime("%B %d, %Y at %I:%M %p UTC")
        end_str = window.end_time.strftime("%B %d, %Y at %I:%M %p UTC")
        
        # Calculate duration
        delta = window.end_time - window.start_time
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes >= 60:
            hours = total_minutes // 60
            mins = total_minutes % 60
            duration = f"{hours}h {mins}m" if mins else f"{hours} hour{'s' if hours > 1 else ''}"
        else:
            duration = f"{total_minutes} minutes"
        
        # Render appropriate template
        if email_type == "announcement":
            subject = f"🔧 Scheduled Maintenance: {window.title}"
            html_body = self.render_maintenance_announcement(
                title=window.title,
                description=window.description or "",
                start_time=start_str,
                end_time=end_str,
                duration=duration
            )
        elif email_type == "reminder":
            minutes_until = max(1, int((window.start_time - datetime.utcnow()).total_seconds() / 60))
            subject = f"⏰ Maintenance Starting Soon: {window.title}"
            html_body = self.render_maintenance_reminder(
                title=window.title,
                description=window.description or "",
                start_time=start_str,
                end_time=end_str,
                duration=duration,
                minutes_until=minutes_until
            )
        elif email_type == "complete":
            subject = f"✅ Maintenance Complete: {window.title}"
            html_body = self.render_maintenance_complete(
                title=window.title,
                description=window.description
            )
        elif email_type == "cancelled":
            subject = f"ℹ️ Maintenance Cancelled: {window.title}"
            html_body = self.render_maintenance_cancelled(title=window.title)
        else:
            logger.error(f"Unknown maintenance email type: {email_type}")
            return {"sent": 0, "failed": 0, "total": len(users)}
        
        sent = 0
        failed = 0
        for user in users:
            try:
                success = await self.send_email(user.email, subject, html_body)
                if success:
                    sent += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Failed to send maintenance email to {user.email}: {e}")
                failed += 1
        
        logger.info(f"Maintenance {email_type} email: sent={sent}, failed={failed}, total={len(users)}")
        return {"sent": sent, "failed": failed, "total": len(users)}
