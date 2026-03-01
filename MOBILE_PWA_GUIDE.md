# BingeAlert Mobile + PWA Integration Guide

## Overview
This converts BingeAlert's admin dashboard into a mobile-friendly Progressive Web App (PWA) that can be bookmarked to the home screen on iOS and Android, looking and feeling like a native app.

## What's Included

| File | Purpose |
|------|---------|
| `app/static/manifest.json` | PWA manifest — enables "Add to Home Screen" |
| `app/static/service-worker.js` | Offline caching + app-like behavior |
| `app/static/mobile.css` | Complete responsive CSS overhaul |
| `app/static/icons/icon-192.png` | Android home screen icon |
| `app/static/icons/icon-512.png` | Android splash screen icon |
| `app/static/icons/apple-touch-icon.png` | iOS home screen icon |
| `app/static/icons/favicon-32.png` | Browser tab favicon |

---

## Step 1: Add Files to Your Project

Copy all the files from this package into your `app/static/` directory:

```
app/static/
├── manifest.json          (NEW)
├── service-worker.js      (NEW)
├── mobile.css             (NEW)
├── icons/                 (NEW directory)
│   ├── icon-192.png
│   ├── icon-512.png
│   ├── apple-touch-icon.png
│   └── favicon-32.png
├── admin.html             (existing - modify)
├── setup.html             (existing - modify)
└── login.html             (existing - modify)
```

---

## Step 2: Add Meta Tags to `<head>` in admin.html

Add these lines inside the `<head>` tag of `admin.html`, `setup.html`, and `login.html`:

```html
<!-- PWA & Mobile Meta Tags - ADD THESE -->
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, maximum-scale=1.0, user-scalable=no">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BingeAlert">
<meta name="theme-color" content="#0a0a0f">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/static/icons/favicon-32.png">

<!-- Mobile CSS -->
<link rel="stylesheet" href="/static/mobile.css">
```

> **IMPORTANT:** If your existing `<head>` already has a `<meta name="viewport">` tag, REPLACE it with the one above. Don't have two viewport tags.

---

## Step 3: Add Service Worker Registration

Add this script at the bottom of `admin.html`, right before `</body>`:

```html
<!-- PWA Service Worker Registration -->
<script>
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/service-worker.js')
            .then(reg => console.log('SW registered:', reg.scope))
            .catch(err => console.log('SW registration failed:', err));
    });
}
</script>
```

---

## Step 4: Serve manifest.json with correct MIME type

In `app/main.py`, make sure your static file mounting serves `.json` files correctly. FastAPI's `StaticFiles` handles this automatically, but verify:

```python
# This should already exist in your main.py
app.mount("/static", StaticFiles(directory="app/static"), name="static")
```

Also add a route for the service worker (it needs to be served from the root scope):

```python
from fastapi.responses import FileResponse

@app.get("/service-worker.js")
async def service_worker():
    return FileResponse(
        "app/static/service-worker.js",
        media_type="application/javascript"
    )
```

---

## Step 5: Add `data-label` Attributes to Table Cells (Optional but Recommended)

For the mobile card layout to show labels, add `data-label` attributes to `<td>` elements. This is the biggest manual step but makes the mobile experience much better.

Example — in the Users table:
```html
<!-- Before -->
<td>${user.username}</td>
<td>${user.email}</td>
<td>${user.requests}</td>

<!-- After -->
<td data-label="User">${user.username}</td>
<td data-label="Email">${user.email}</td>
<td data-label="Requests">${user.requests}</td>
```

And add the class `mobile-cards` to tables you want to convert:
```html
<table class="mobile-cards">
```

For columns you want to hide on mobile (like IDs), add:
```html
<td class="hide-mobile" data-label="ID">${user.id}</td>
```

---

## Step 6: Test It

### Desktop Browser (Responsive Mode)
1. Open Chrome DevTools → Toggle Device Toolbar (Ctrl+Shift+M)
2. Select "iPhone 14 Pro" or "Pixel 7"
3. Verify: bottom tab bar, stacked cards, full-screen modals

### iOS (Safari)
1. Navigate to `http://your-server:8000`
2. Tap Share → "Add to Home Screen"
3. Open from home screen — should launch full-screen (no Safari chrome)
4. Verify: tabs at bottom, touch-friendly buttons, no horizontal scroll

### Android (Chrome)
1. Navigate to `http://your-server:8000`
2. Chrome should show "Install" banner or: Menu → "Add to Home Screen"
3. Open from home screen — standalone app experience
4. Verify: same as iOS checks

---

## What the Mobile CSS Does

### Bottom Tab Bar
- Desktop tabs move to the bottom on mobile (like a native app)
- Horizontally scrollable if too many tabs
- Active tab highlighted in gold
- Safe area padding for iPhone notch/home indicator

### Card-Based Tables
- Tables with `.mobile-cards` class become stacked cards
- Each row is a card with labeled fields
- Hide less important columns with `.hide-mobile`

### Full-Screen Modals
- Modals slide up from the bottom (iOS sheet style)
- 90vh max height with scroll
- Rounded top corners

### Touch Optimizations
- All buttons minimum 44px tap target (Apple HIG)
- Input fields use 16px font (prevents iOS auto-zoom)
- Active states for touch feedback
- Momentum scrolling everywhere

### PWA Features
- Add to Home Screen with custom icon
- Standalone display (no browser chrome)
- Theme color matches your dark UI
- Safe area support for notch/Dynamic Island
- Basic offline caching for static assets

---

## Customization Notes

### Changing the App Icon
Replace the PNG files in `app/static/icons/`. Sizes needed:
- `icon-192.png` — 192×192 (Android)
- `icon-512.png` — 512×512 (Android splash)
- `apple-touch-icon.png` — 180×180 (iOS)
- `favicon-32.png` — 32×32 (browser tab)

### Adjusting Breakpoints
The CSS uses three breakpoints:
- `768px` — Tablet (2-column grids, smaller text)
- `480px` — Phone (bottom nav, card tables, stacked layouts)
- `360px` — Small phone (iPhone SE, tighter spacing)

### Theme Colors
The CSS uses your existing BingeAlert colors:
- Background: `#0a0a0f`
- Gold accent: `#e5a00d` / `rgba(229, 160, 13, ...)`
- Card backgrounds: `rgba(255, 255, 255, 0.03-0.05)`

---

## Troubleshooting

**Bottom tabs not showing?**
- Make sure your tab buttons have the `.tab` class or are direct children of `.tabs`
- Check that `mobile.css` is loaded after your main styles

**iOS not showing "Add to Home Screen"?**
- Must be served over HTTPS (or localhost)
- `manifest.json` must be accessible at `/static/manifest.json`
- Apple meta tags must be in `<head>`

**Input fields zooming on iOS?**
- All input/select/textarea must have `font-size: 16px` minimum
- The mobile CSS handles this, but check for inline styles overriding it

**Tables not converting to cards?**
- Add `class="mobile-cards"` to the `<table>` element
- Add `data-label="..."` to each `<td>` for labels

**Service worker not registering?**
- Check browser console for errors
- Service worker must be served from the site root or `/static/` path
- Must be HTTPS in production (localhost is exempt)
