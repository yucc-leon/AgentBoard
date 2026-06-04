/* Agent Session Workboard — Service Worker (PWA)

Enables:
  - Offline caching
  - "Add to Home Screen"
  - Background sync (future)
*/

const CACHE_NAME = 'agentboard-v0.2.0';
const STATIC_ASSETS = [
    '/',
    '/static/style.css',
    '/static/session_live.js',
    '/static/manifest.json',
    '/sessions',
    '/worktrees',
];

// Install: pre-cache static assets
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        }).then(() => {
            return self.skipWaiting();
        })
    );
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            );
        }).then(() => {
            return self.clients.claim();
        })
    );
});

// Fetch: network-first strategy for dynamic content, cache-first for static
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Don't cache WebSocket or API calls
    if (url.pathname.startsWith('/ws') || url.pathname.startsWith('/api')) {
        return;
    }

    // Static assets: cache-first
    if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                return cached || fetch(event.request).then((response) => {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, clone);
                    });
                    return response;
                });
            })
        );
        return;
    }

    // Dynamic pages: network-first
    event.respondWith(
        fetch(event.request).then((response) => {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
                cache.put(event.request, clone);
            });
            return response;
        }).catch(() => {
            return caches.match(event.request);
        })
    );
});

// Push notification (future)
self.addEventListener('push', (event) => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'AgentBoard';
    const options = {
        body: data.body || 'Session update',
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        data: data.url || '/',
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data)
    );
});
