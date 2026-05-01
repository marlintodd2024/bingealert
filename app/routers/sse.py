"""
Server-Sent Events for real-time dashboard updates
"""
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
import asyncio
import json
from datetime import datetime

from app.database import get_db, User, MediaRequest, Notification, EpisodeTracking

router = APIRouter(prefix="/sse", tags=["sse"])


async def event_generator(db: Session):
    """Generate SSE events with stats updates"""
    try:
        while True:
            # Get current stats
            stats = {
                "users": db.query(func.count(User.id)).scalar(),
                "requests": {
                    "total": db.query(func.count(MediaRequest.id)).scalar(),
                    "movies": db.query(func.count(MediaRequest.id)).filter(MediaRequest.media_type == "movie").scalar(),
                    "tv_shows": db.query(func.count(MediaRequest.id)).filter(MediaRequest.media_type == "tv").scalar(),
                    "tracking": db.query(func.count(MediaRequest.id)).filter(MediaRequest.status != "available").scalar(),
                },
                "episodes_tracked": db.query(func.count(EpisodeTracking.id)).scalar(),
                "notifications": {
                    "total": db.query(func.count(Notification.id)).scalar(),
                    "sent": db.query(func.count(Notification.id)).filter(Notification.sent == True).scalar(),
                    "pending": db.query(func.count(Notification.id)).filter(Notification.sent == False).scalar(),
                },
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Send as SSE event
            yield f"data: {json.dumps(stats)}\n\n"
            
            # Update every 5 seconds
            await asyncio.sleep(5)
            
    except asyncio.CancelledError:
        # Client disconnected
        pass


@router.get("/stats")
async def stream_stats(db: Session = Depends(get_db)):
    """Stream real-time stats updates via SSE"""
    return StreamingResponse(
        event_generator(db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
