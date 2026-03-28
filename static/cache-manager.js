/**
 * cache-manager.js — v4.13.0
 * Two-tier browser cache: audio (Service Worker + Cache API) + metadata (localStorage).
 *
 * Audio caching is passive (cache-on-play): the SW intercepts /player/stream/* and
 * caches the full response. This module tracks *which* songs should stay cached and
 * tells the SW to evict the rest.
 *
 * Metadata cache stores lightweight track info (title, artwork_url, video_id, etc.)
 * in localStorage for instant offline-first UI rendering.
 *
 * Exposed as window.CacheManager.
 */
window.CacheManager = (function () {
  'use strict';

  // ── Config ──────────────────────────────────────────────────────────────────
  const CACHE_RECENT_PLAYED    = 10;
  const CACHE_RECENT_DOWNLOADED = 10;
  const CACHE_MAX_BYTES        = 250 * 1024 * 1024;  // 250 MB
  const METADATA_TTL_MS        = 24 * 60 * 60 * 1000; // 24 hours

  // ── localStorage keys ──────────────────────────────────────────────────────
  const KEY_META       = 'yt2mp3.cache.meta';
  const KEY_PLAYED     = 'yt2mp3.cache.played';
  const KEY_DOWNLOADED = 'yt2mp3.cache.downloaded';

  // ── Internal state ─────────────────────────────────────────────────────────
  let _swReady = false;
  let _swReg   = null;

  // ── localStorage helpers ───────────────────────────────────────────────────

  function _getJSON(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function _setJSON(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {}
  }

  // ── Metadata cache (24h TTL) ───────────────────────────────────────────────

  function _getMeta() {
    return _getJSON(KEY_META, { tracks: {}, version: 1 });
  }

  function _setMeta(meta) {
    _setJSON(KEY_META, meta);
  }

  /**
   * Store metadata for a list of tracks. Updates existing entries and adds new ones.
   * Entries older than METADATA_TTL_MS are pruned.
   */
  function updateMetadata(tracks) {
    if (!Array.isArray(tracks) || !tracks.length) return;
    const meta = _getMeta();
    const now = Date.now();

    for (const t of tracks) {
      if (!t.job_id) continue;
      meta.tracks[t.job_id] = {
        title:       t.title || t.job_id,
        artwork_url: t.artwork_url || null,
        video_id:    t.video_id || null,
        is_favorite: Boolean(t.is_favorite),
        file_size:   t.file_size || 0,
        created_at:  t.created_at || null,
        cached_at:   now,
      };
    }

    // Prune expired entries
    const cutoff = now - METADATA_TTL_MS;
    for (const id of Object.keys(meta.tracks)) {
      if (meta.tracks[id].cached_at < cutoff) {
        delete meta.tracks[id];
      }
    }

    _setMeta(meta);
  }

  /**
   * Return cached metadata for all tracks, or null if cache is empty/expired.
   * Returns array sorted by created_at DESC (same as API).
   */
  function getCachedTracks() {
    const meta = _getMeta();
    const entries = Object.entries(meta.tracks);
    if (!entries.length) return null;

    const now = Date.now();
    const cutoff = now - METADATA_TTL_MS;
    const valid = entries
      .filter(([, v]) => v.cached_at >= cutoff)
      .map(([jobId, v]) => ({
        job_id:      jobId,
        title:       v.title,
        artwork_url: v.artwork_url,
        video_id:    v.video_id,
        is_favorite: v.is_favorite,
        file_size:   v.file_size,
        created_at:  v.created_at,
        _fromCache:  true,
      }));

    if (!valid.length) return null;

    // Sort by created_at DESC
    valid.sort((a, b) => {
      if (!a.created_at && !b.created_at) return 0;
      if (!a.created_at) return 1;
      if (!b.created_at) return -1;
      return new Date(b.created_at) - new Date(a.created_at);
    });

    return valid;
  }

  /**
   * Update favorite status in metadata cache.
   */
  function updateFavorite(jobId, isFavorite) {
    const meta = _getMeta();
    if (meta.tracks[jobId]) {
      meta.tracks[jobId].is_favorite = Boolean(isFavorite);
      _setMeta(meta);
    }
  }

  // ── Played / Downloaded tracking ───────────────────────────────────────────

  function _getPlayed() {
    return _getJSON(KEY_PLAYED, []);
  }

  function _getDownloaded() {
    return _getJSON(KEY_DOWNLOADED, []);
  }

  /**
   * Record that a track was played (most recent first, max N).
   */
  function trackPlayed(jobId) {
    if (!jobId) return;
    let played = _getPlayed();
    played = played.filter(id => id !== jobId);
    played.unshift(jobId);
    if (played.length > CACHE_RECENT_PLAYED) played.length = CACHE_RECENT_PLAYED;
    _setJSON(KEY_PLAYED, played);
    _scheduleEviction();
  }

  /**
   * Update the "recently downloaded" list from the full track list (most recent N).
   */
  function updateDownloaded(tracks) {
    if (!Array.isArray(tracks) || !tracks.length) return;
    // tracks are already sorted by created_at DESC from the API
    const downloaded = tracks.slice(0, CACHE_RECENT_DOWNLOADED).map(t => t.job_id);
    _setJSON(KEY_DOWNLOADED, downloaded);
  }

  // ── Keep set & eviction ────────────────────────────────────────────────────

  /**
   * Compute the set of job IDs that should remain cached.
   * keep = union(recently played, recently downloaded, all favorites)
   */
  function _computeKeepSet() {
    const played     = _getPlayed();
    const downloaded = _getDownloaded();

    // Favorites from metadata cache
    const meta = _getMeta();
    const favorites = Object.entries(meta.tracks)
      .filter(([, v]) => v.is_favorite)
      .map(([id]) => id);

    const keepSet = new Set([...played, ...downloaded, ...favorites]);
    return keepSet;
  }

  let _evictionPending = false;

  function _scheduleEviction() {
    if (_evictionPending || !_swReady) return;
    _evictionPending = true;
    // Debounce: run eviction after 2s of inactivity
    setTimeout(() => {
      _evictionPending = false;
      _runEviction();
    }, 2000);
  }

  function _runEviction() {
    if (!_swReady || !_swReg || !_swReg.active) return;
    const keepSet = _computeKeepSet();

    // Ask SW for current cache contents
    _swReg.active.postMessage({ type: 'CACHE_SIZE_CHECK' });

    // Response handled in the message listener below
    _pendingKeepSet = keepSet;
  }

  let _pendingKeepSet = null;

  function _handleSizeResult(data) {
    const { totalSize, cachedJobIds } = data;
    const keepSet = _pendingKeepSet || _computeKeepSet();
    _pendingKeepSet = null;

    // Find entries to evict (not in keep set)
    const toEvict = (cachedJobIds || []).filter(id => !keepSet.has(id));

    if (toEvict.length > 0) {
      _postToSW({ type: 'EVICT', jobIds: toEvict });
    }

    // If still over size limit after evicting non-keep entries,
    // evict oldest played/downloaded (but not favorites)
    if (totalSize > CACHE_MAX_BYTES && toEvict.length === 0) {
      // Evict from played list (oldest first = end of array)
      const played = _getPlayed();
      const meta = _getMeta();
      const expendable = played
        .slice()
        .reverse()
        .filter(id => {
          const m = meta.tracks[id];
          return !m || !m.is_favorite; // don't evict favorites
        });

      if (expendable.length > 0) {
        // Evict the oldest 3 played tracks
        const batch = expendable.slice(0, 3);
        _postToSW({ type: 'EVICT', jobIds: batch });
        // Also remove from played list
        let updatedPlayed = played.filter(id => !batch.includes(id));
        _setJSON(KEY_PLAYED, updatedPlayed);
      }
    }
  }

  // ── Service Worker communication ───────────────────────────────────────────

  function _postToSW(msg) {
    if (_swReg && _swReg.active) {
      _swReg.active.postMessage(msg);
    }
  }

  // ── Initialization ─────────────────────────────────────────────────────────

  function init() {
    if (!('serviceWorker' in navigator)) {
      console.log('[CacheManager] Service Workers not supported');
      return;
    }

    navigator.serviceWorker.register('/static/sw.js', { scope: '/' })
      .then(reg => {
        _swReg = reg;
        // Wait for the SW to activate
        const sw = reg.active || reg.installing || reg.waiting;
        if (sw && sw.state === 'activated') {
          _swReady = true;
        } else if (sw) {
          sw.addEventListener('statechange', () => {
            if (sw.state === 'activated') _swReady = true;
          });
        }
        // Also handle updates
        reg.addEventListener('updatefound', () => {
          const newSW = reg.installing;
          if (newSW) {
            newSW.addEventListener('statechange', () => {
              if (newSW.state === 'activated') {
                _swReg = reg;
                _swReady = true;
              }
            });
          }
        });
      })
      .catch(err => {
        console.warn('[CacheManager] SW registration failed:', err);
      });

    // Listen for messages from SW
    navigator.serviceWorker.addEventListener('message', (event) => {
      const { type } = event.data || {};
      if (type === 'CACHE_SIZE_RESULT') {
        _handleSizeResult(event.data);
      }
    });
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  return {
    init,
    trackPlayed,
    updateDownloaded,
    updateMetadata,
    updateFavorite,
    getCachedTracks,
    /** Force an eviction check now. */
    evict: _scheduleEviction,
  };
})();
