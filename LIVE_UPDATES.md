# Adding Live Updates to Dashboard

## What This Enables:

Real-time updates without page refresh:
- ✅ Stat tiles update every 5 seconds
- ✅ Pending notifications count updates live
- ✅ No manual refresh needed
- ✅ See changes as they happen

## Implementation Options:

### Option 1: Server-Sent Events (SSE) - Recommended

**Pros:**
- Simple to implement
- One-way server → client (perfect for stats)
- Auto-reconnects on disconnect
- Works over HTTP (no special config)

**Cons:**
- One-way only (can't send commands from client)
- Not ideal for two-way chat

### Option 2: WebSockets

**Pros:**
- Two-way communication
- More powerful for complex interactions
- Industry standard

**Cons:**
- More complex setup
- Requires WebSocket support in proxy
- Overkill for simple stat updates

## SSE Implementation (Recommended):

### 1. Add SSE Router to Main App

Edit `app/main.py`:

```python
# Add import
from app.routers import sse

# Register router
app.include_router(sse.router)
```

### 2. Update Frontend JavaScript

Add to `admin.html` in the `<script>` section:

```javascript
// Real-time stats via SSE
let statsEventSource = null;

function startLiveUpdates() {
    // Close existing connection
    if (statsEventSource) {
        statsEventSource.close();
    }
    
    // Connect to SSE endpoint
    statsEventSource = new EventSource(`${API_BASE}/sse/stats`);
    
    statsEventSource.onmessage = function(event) {
        const stats = JSON.parse(event.data);
        
        // Update stat tiles
        document.getElementById('totalUsers').textContent = stats.users || 0;
        document.getElementById('totalRequests').textContent = stats.requests.total || 0;
        document.getElementById('trackingRequests').textContent = stats.requests.tracking || 0;
        document.getElementById('episodesTracked').textContent = stats.episodes_tracked || 0;
        document.getElementById('notificationsSent').textContent = stats.notifications.sent || 0;
        document.getElementById('notificationsPending').textContent = stats.notifications.pending || 0;
        
        // Update last sync time
        const lastSync = document.querySelector('.header p');
        if (lastSync) {
            const now = new Date(stats.timestamp);
            lastSync.textContent = `Last sync: ${now.toLocaleString()}`;
        }
    };
    
    statsEventSource.onerror = function(error) {
        console.error('SSE connection error:', error);
        statsEventSource.close();
        
        // Retry connection after 5 seconds
        setTimeout(startLiveUpdates, 5000);
    };
}

// Add toggle button
function toggleLiveUpdates() {
    const btn = document.getElementById('liveUpdateBtn');
    if (statsEventSource && statsEventSource.readyState !== EventSource.CLOSED) {
        // Disable live updates
        statsEventSource.close();
        btn.textContent = '▶️ Enable Live Updates';
        btn.style.background = '#4caf50';
    } else {
        // Enable live updates
        startLiveUpdates();
        btn.textContent = '⏸️ Disable Live Updates';
        btn.style.background = '#f44336';
    }
}

// Auto-start live updates on page load
document.addEventListener('DOMContentLoaded', function() {
    startLiveUpdates();
});
```

### 3. Add Toggle Button to UI

Add button to admin dashboard header:

```html
<button onclick="toggleLiveUpdates()" id="liveUpdateBtn" 
        style="background: #f44336; color: white; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer;">
    ⏸️ Disable Live Updates
</button>
```

## What You Get:

**Before:**
- Stats update only when you refresh or click buttons
- Miss real-time changes
- Manual polling required

**After:**
- Stats update automatically every 5 seconds
- See notifications pile up in real-time
- See pending count change as batches send
- Toggle on/off as needed

## Performance:

- **Bandwidth**: ~200 bytes per update = ~40 bytes/sec
- **Server Load**: Minimal (simple DB queries every 5s)
- **Client Load**: Negligible (just updating DOM elements)

## Advanced: Add More Live Updates

Extend to other data:

```javascript
// Add recent notifications stream
eventSource.addEventListener('notification', function(event) {
    const notification = JSON.parse(event.data);
    // Add to top of notifications table
    prependNotificationRow(notification);
});

// Add log stream
eventSource.addEventListener('log', function(event) {
    const log = JSON.parse(event.data);
    // Append to logs viewer
    appendLogLine(log);
});
```

## Testing:

1. Deploy updated code
2. Open admin dashboard
3. In another window, trigger some actions (sync users, import episodes)
4. Watch the stats update automatically! 🎉

## Troubleshooting:

**Stats not updating?**
- Check browser console for SSE errors
- Verify `/sse/stats` endpoint works: `curl http://localhost:8000/sse/stats`
- Check nginx/proxy doesn't buffer SSE

**Connection keeps dropping?**
- Some proxies timeout SSE connections
- Add keepalive pings every 30s to prevent timeout

**Want faster updates?**
- Change `await asyncio.sleep(5)` to smaller value (e.g., 2 seconds)
- Balance between real-time and server load
