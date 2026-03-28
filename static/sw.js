/**
 * sw.js — Service Worker for yt2mp3 audio caching (v4.13.0)
 *
 * Strategy:
 *   - Cache-first for /player/stream/* requests (MP3 audio)
 *   - All other requests pass through to the network untouched
 *
 * Communication (via postMessage from CacheManager):
 *   { type: 'EVICT', jobIds: ['id1', ...] }       — remove specific entries
 *   { type: 'CACHE_SIZE_CHECK' }                   — respond with total cache size
 *   { type: 'PRECACHE', url: '/player/stream/id' } — fetch and cache a URL
 */

const CACHE_NAME = 'yt2mp3-audio-v1';
const STREAM_PATH = '/player/stream/';

// ── Install / Activate ───────────────────────────────────────────────────────

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Claim all open clients immediately so the SW starts intercepting fetches
  event.waitUntil(
    caches.keys().then(names =>
      Promise.all(
        names
          .filter(n => n.startsWith('yt2mp3-audio-') && n !== CACHE_NAME)
          .map(n => caches.delete(n))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch (cache-first for audio streams) ────────────────────────────────────

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Only intercept audio stream requests
  if (!url.pathname.startsWith(STREAM_PATH)) return;

  // Only cache GET requests (not Range sub-requests from <audio> seeking)
  // Actually, we cache ALL GET requests including those with Range headers.
  // The Cache API stores the full response; for Range requests the server
  // returns 206 which we cache as-is. However, for cache-first to work
  // reliably with <audio> seeking, we need the full response cached.
  // Strategy: try cache first (only for non-Range requests), then network.
  // For Range requests, always go to network (the browser handles byte-range
  // seeking and we can't serve partial content from the Cache API).
  if (event.request.headers.get('Range')) {
    // Range request — let it go to network (audio seeking)
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(cache =>
      cache.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          // Only cache successful full responses (200 OK)
          if (response.ok && response.status === 200) {
            cache.put(event.request, response.clone());
          }
          return response;
        });
      })
    )
  );
});

// ── Messages from CacheManager ───────────────────────────────────────────────

self.addEventListener('message', (event) => {
  const { type, jobIds, url } = event.data || {};

  if (type === 'EVICT' && Array.isArray(jobIds)) {
    event.waitUntil(
      caches.open(CACHE_NAME).then(cache =>
        Promise.all(
          jobIds.map(id => cache.delete(new Request(`${STREAM_PATH}${id}`)))
        )
      )
    );
  }

  if (type === 'CACHE_SIZE_CHECK') {
    event.waitUntil(
      caches.open(CACHE_NAME)
        .then(cache => cache.keys())
        .then(keys =>
          Promise.all(keys.map(req =>
            caches.open(CACHE_NAME)
              .then(c => c.match(req))
              .then(res => {
                if (!res) return { url: req.url, size: 0 };
                return res.clone().blob().then(blob => ({ url: req.url, size: blob.size }));
              })
          ))
        )
        .then(entries => {
          const totalSize = entries.reduce((sum, e) => sum + e.size, 0);
          const jobIds = entries.map(e => {
            const parts = new URL(e.url).pathname.split('/');
            return parts[parts.length - 1];
          });
          event.source.postMessage({
            type: 'CACHE_SIZE_RESULT',
            totalSize,
            cachedJobIds: jobIds,
            count: entries.length,
          });
        })
    );
  }

  if (type === 'PRECACHE' && url) {
    event.waitUntil(
      caches.open(CACHE_NAME).then(cache =>
        cache.match(url).then(existing => {
          if (existing) return; // already cached
          return fetch(url).then(response => {
            if (response.ok && response.status === 200) {
              cache.put(url, response);
            }
          }).catch(() => {}); // silent fail on precache
        })
      )
    );
  }
});
