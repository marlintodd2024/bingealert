// PWA service worker. Scope is "/" (mounted at /service-worker.js).
//
// Aikido flagged the previous version for SSRF: the fetch handler called
// fetch(event.request) without validating the URL, so a script on the page
// could trick the worker into forwarding to an attacker-controlled origin.
// buildValidatedUrl() locks fetches to http/https + same hostname and
// rejects path-traversal sequences before they reach fetch().

const CACHE_NAME = 'bingealert-v1';
const STATIC_ASSETS = [
  '/',
  '/static/admin.html',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

function buildValidatedUrl(requestUrl) {
  try {
    // Reject obvious path-traversal patterns (literal and URL-encoded).
    if (requestUrl.includes('/../') || /\/%2e%2e\//i.test(requestUrl)) {
      throw new Error('Invalid path');
    }

    const url = new URL(requestUrl);

    // Only http(s) -- blocks javascript:, data:, blob:, file:, ws:.
    if (!['http:', 'https:'].includes(url.protocol)) {
      throw new Error('Invalid protocol');
    }

    // Same origin only -- the worker must never proxy to another host.
    if (url.hostname !== self.location.hostname) {
      throw new Error('Invalid host');
    }

    return url.href;
  } catch {
    throw new Error('Invalid URL');
  }
}

// Install - cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate - clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// Fetch - network first, fallback to cache (dashboard needs fresh data)
self.addEventListener('fetch', event => {
  // Skip non-GET and API/webhook requests
  if (event.request.method !== 'GET' ||
      event.request.url.includes('/api/') ||
      event.request.url.includes('/webhooks/') ||
      event.request.url.includes('/admin/') ||
      event.request.url.includes('/health')) {
    return;
  }

  event.respondWith(
    (async () => {
      try {
        // buildValidatedUrl enforces same-origin + http(s) before this runs.
        // nosemgrep: AIK_js_ssrf
        const response = await fetch(buildValidatedUrl(event.request.url));

        // Cache successful responses for static assets
        if (response.ok && STATIC_ASSETS.some(a => event.request.url.endsWith(a))) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      } catch {
        return caches.match(event.request);
      }
    })()
  );
});
