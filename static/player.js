/**
 * player.js — v4.0.0
 * Global persistent player module. Loaded once in shell.html, never reloaded.
 * Owns the <audio id="audio"> element and all player-bar controls.
 * Exposes window.Player for fragments to call.
 */
window.Player = (function () {
  'use strict';

  // ── State ───────────────────────────────────────────────────────────────────
  const state = {
    tracks:      [],      // full track list (loaded by active fragment)
    queue:       [],      // job_id[] for current playback sequence
    queueIndex:  0,
    shuffle:     false,
    repeat:      'none',  // 'none' | 'one' | 'all'
    currentJob:  null,
    _hasSession: false,   // set by app.js via Player.setSession()
  };

  const _STORAGE_KEY = 'yt2mp3.player';
  let _lastSave = 0;

  // Callbacks registered by fragments (e.g. to highlight playing row)
  const _listeners = { trackChange: [] };

  // ── DOM refs (always present in shell) ─────────────────────────────────────
  const audio        = document.getElementById('audio');
  const elTitle      = document.getElementById('player-title');
  const elIconPlay   = document.getElementById('icon-play');
  const elIconPause  = document.getElementById('icon-pause');
  const elIconVol    = document.getElementById('icon-vol');
  const elIconMute   = document.getElementById('icon-mute');
  const elTimeCur    = document.getElementById('time-current');
  const elTimeTotal  = document.getElementById('time-total');
  const elFill       = document.getElementById('progress-fill');
  const elWrap       = document.getElementById('progress-wrap');
  const elShuffle    = document.getElementById('btn-shuffle');
  const elRepeat     = document.getElementById('btn-repeat');
  const elVolSlider  = document.getElementById('vol-slider');

  // ── Helpers ─────────────────────────────────────────────────────────────────
  function fmtTime(s) {
    if (!isFinite(s)) return '0:00';
    const m   = Math.floor(s / 60);
    const sec = String(Math.floor(s % 60)).padStart(2, '0');
    return `${m}:${sec}`;
  }

  function _emit(event, payload) {
    (_listeners[event] || []).forEach(cb => { try { cb(payload); } catch (_) {} });
  }

  // ── Audio events ────────────────────────────────────────────────────────────
  audio.addEventListener('play',  () => {
    if (elIconPlay)  elIconPlay.style.display  = 'none';
    if (elIconPause) elIconPause.style.display = 'block';
    document.body.classList.add('is-playing');
    if ('mediaSession' in navigator)
      navigator.mediaSession.playbackState = 'playing';
  });
  audio.addEventListener('pause', () => {
    if (elIconPlay)  elIconPlay.style.display  = 'block';
    if (elIconPause) elIconPause.style.display = 'none';
    document.body.classList.remove('is-playing');
    if ('mediaSession' in navigator)
      navigator.mediaSession.playbackState = 'paused';
  });

  audio.addEventListener('ended', () => {
    if (state.repeat === 'one') {
      audio.play();
    } else {
      nextTrack();
    }
  });

  audio.addEventListener('timeupdate', () => {
    const cur = audio.currentTime;
    const dur = audio.duration;
    if (elTimeCur)   elTimeCur.textContent  = fmtTime(cur);
    if (elTimeTotal) elTimeTotal.textContent = fmtTime(dur);
    const pct = dur ? (cur / dur * 100) : 0;
    if (elFill) elFill.style.width = pct + '%';
    // Persist position every 5 s
    const now = Date.now();
    if (state.currentJob && now - _lastSave >= 5000) {
      _lastSave = now;
      _saveState();
    }
    _updatePositionState();
  });

  function _updatePositionState() {
    if (!('mediaSession' in navigator)) return;
    if (!('setPositionState' in navigator.mediaSession)) return;
    if (!audio.duration || !isFinite(audio.duration)) return;
    try {
      navigator.mediaSession.setPositionState({
        duration:     audio.duration,
        position:     audio.currentTime,
        playbackRate: audio.playbackRate || 1,
      });
    } catch (_) {}
  }

  // ── Player bar controls ─────────────────────────────────────────────────────
  if (elWrap) {
    elWrap.addEventListener('click', function (e) {
      if (!audio.duration) return;
      const rect = elWrap.getBoundingClientRect();
      audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
    });
  }

  if (elVolSlider) {
    elVolSlider.addEventListener('input', function () {
      audio.volume = parseFloat(this.value);
    });
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  function playTrack(jobId, title) {
    const idx = state.queue.indexOf(jobId);
    state.currentJob  = jobId;
    state.queueIndex  = idx >= 0 ? idx : 0;
    audio.src = `/player/stream/${jobId}`;
    audio.play().catch(() => {});
    _updateTitle(jobId, title);
    _emit('trackChange', jobId);
    _saveState();
    // Notify cache manager that this track was played (for eviction policy)
    if (window.CacheManager) window.CacheManager.trackPlayed(jobId);
  }

  function togglePlay() {
    if (state.currentJob) {
      if (audio.paused) audio.play().catch(() => {});
      else              audio.pause();
      return;
    }
    // Nothing playing yet — lazy-load tracks if needed
    if (!state.tracks.length) {
      if (!state._hasSession) return;
      fetch('/player/api/tracks')
        .then(r => r.ok ? r.json() : [])
        .then(tracks => {
          if (!tracks.length) return;
          loadTracks(tracks);
          const fav = tracks.find(t => t.is_favorite);
          playTrack((fav || tracks[0]).job_id);
        })
        .catch(() => {});
      return;
    }
    // Tracks already loaded — start from first favorite or first track
    const fav = state.tracks.find(t => t.is_favorite);
    playTrack((fav || state.tracks[0]).job_id);
  }

  function prevTrack() {
    if (!state.queue.length) return;
    let idx = state.queueIndex - 1;
    if (idx < 0) idx = state.repeat === 'all' ? state.queue.length - 1 : 0;
    const jobId = state.queue[idx];
    if (jobId) playTrack(jobId);
  }

  function nextTrack() {
    if (!state.queue.length) return;
    let idx;
    if (state.shuffle) {
      do { idx = Math.floor(Math.random() * state.queue.length); }
      while (idx === state.queueIndex && state.queue.length > 1);
    } else {
      idx = state.queueIndex + 1;
      if (idx >= state.queue.length) {
        if (state.repeat === 'all') idx = 0;
        else return;
      }
    }
    const jobId = state.queue[idx];
    if (jobId) playTrack(jobId);
  }

  function toggleShuffle() {
    state.shuffle = !state.shuffle;
    if (elShuffle) elShuffle.classList.toggle('active', state.shuffle);
  }

  function cycleRepeat() {
    const modes = ['none', 'one', 'all'];
    state.repeat = modes[(modes.indexOf(state.repeat) + 1) % modes.length];
    if (elRepeat) {
      elRepeat.classList.toggle('active', state.repeat !== 'none');
      elRepeat.title = state.repeat === 'none' ? 'Repetir'
                     : state.repeat === 'one'  ? 'Repetir canción'
                     : 'Repetir todo';
    }
  }

  function toggleMute() {
    audio.muted = !audio.muted;
    if (elIconVol)  elIconVol.style.display  = audio.muted ? 'none'  : 'block';
    if (elIconMute) elIconMute.style.display = audio.muted ? 'block' : 'none';
  }

  /** Called by app.js once /auth/me confirms a logged-in session. */
  function setSession(hasSession) {
    state._hasSession = !!hasSession;
    if (hasSession) _restoreState();
  }

  /**
   * Called by a fragment when it loads its track list.
   * Replaces state.tracks and rebuilds the queue from the new list.
   */
  function loadTracks(tracks) {
    state.tracks = tracks;
    state.queue  = tracks.map(t => t.job_id);
    // Keep queueIndex pointing at the same job if still present
    const idx = state.queue.indexOf(state.currentJob);
    state.queueIndex = idx >= 0 ? idx : 0;
    // Update cache manager with metadata + recently downloaded list
    if (window.CacheManager) {
      window.CacheManager.updateMetadata(tracks);
      window.CacheManager.updateDownloaded(tracks);
      window.CacheManager.evict();
    }
  }

  /**
   * Set the playback queue explicitly (e.g. filtered/sorted list from fragment).
   */
  function setQueue(jobIds, startIdx) {
    state.queue      = jobIds;
    state.queueIndex = startIdx || 0;
  }

  /**
   * Register a callback fired whenever the playing track changes.
   * Fragment uses this to highlight the active row.
   */
  function onTrackChange(cb) {
    _listeners.trackChange.push(cb);
  }

  /** Remove all trackChange listeners (called by fragment on unload). */
  function offTrackChange() {
    _listeners.trackChange = [];
  }

  function getState() {
    return {
      currentJob: state.currentJob,
      playing:    !audio.paused,
      shuffle:    state.shuffle,
      repeat:     state.repeat,
      queue:      state.queue,
      queueIndex: state.queueIndex,
    };
  }

  // ── Internal ────────────────────────────────────────────────────────────────

  function _saveState() {
    try {
      localStorage.setItem(_STORAGE_KEY, JSON.stringify({
        job:     state.currentJob,
        time:    audio.currentTime,
        title:   elTitle ? elTitle.textContent : '',
        shuffle: state.shuffle,
        repeat:  state.repeat,
      }));
    } catch (_) {}
  }

  function _restoreState() {
    let s;
    try { s = JSON.parse(localStorage.getItem(_STORAGE_KEY)); } catch (_) {}
    if (!s || !s.job) return;

    // Restore shuffle/repeat UI
    if (s.shuffle && !state.shuffle) toggleShuffle();
    if (s.repeat && s.repeat !== 'none') {
      let guard = 0;
      while (state.repeat !== s.repeat && guard++ < 3) cycleRepeat();
    }

    // Restore title in player bar immediately (no playback yet)
    if (s.title) _updateTitle(s.job, s.title);

    // Load audio src + seek, then attempt autoplay
    state.currentJob = s.job;
    audio.src = `/player/stream/${s.job}`;
    audio.addEventListener('loadedmetadata', () => {
      audio.currentTime = s.time || 0;
      audio.play().catch(() => {});  // browsers may block without user gesture
    }, { once: true });

    // Self-sufficient metadata fetch: if no fragment has loaded tracks yet
    // (e.g. user is on / or /mis-descargas), fetch them so artwork, media
    // session, and lyrics button work from any page.
    if (!state.tracks.length) {
      Promise.all([
        fetch('/player/api/tracks').then(r => r.ok ? r.json() : []),
        fetch('/player/api/me/features').then(r => r.ok ? r.json() : {}),
      ]).then(([tracks, features]) => {
        // Only populate if a fragment hasn't loaded tracks in the meantime
        if (!state.tracks.length && tracks.length) {
          state.tracks = tracks;
          state.queue  = tracks.map(t => t.job_id);
          const idx    = state.queue.indexOf(state.currentJob);
          state.queueIndex = idx >= 0 ? idx : 0;
        }
        // Update cache manager with fresh metadata + downloaded list
        if (tracks.length && window.CacheManager) {
          window.CacheManager.updateMetadata(tracks);
          window.CacheManager.updateDownloaded(tracks);
          window.CacheManager.evict();
        }
        // Re-run _updateTitle now that state.tracks is populated
        if (state.currentJob) {
          const track = state.tracks.find(t => t.job_id === state.currentJob);
          _updateTitle(state.currentJob, track ? track.title : s.title);
        }
        // Enable lyrics if the user has the feature
        if (features.lyrics_enabled) {
          if (window.Lyrics)        window.Lyrics.setEnabled(true);
          if (window._showLyricsBtn) window._showLyricsBtn(true);
        }
      }).catch(() => {});
    }
  }

  function clearSavedState() {
    try { localStorage.removeItem(_STORAGE_KEY); } catch (_) {}
  }

  function _updateTitle(jobId, title) {
    if (!elTitle) return;
    const track   = state.tracks.find(t => t.job_id === jobId);
    if (!title) {
      title = track ? (track.title || jobId) : jobId;
    }
    elTitle.classList.remove('player-title-empty');
    elTitle.textContent = title;
    document.title = title + ' · yt2mp3';

    const videoId    = track ? (track.video_id   || null) : null;
    const artworkUrl = track ? (track.artwork_url || null) : null;

    _updateArtworkImg(jobId, artworkUrl, videoId, title);
    _updateMediaSession(title, videoId, artworkUrl);

    // Lazy-fetch artwork from backend if not yet cached
    if (!artworkUrl && track) {
      fetch(`/player/api/artwork/${jobId}`)
        .then(r => r.ok ? r.json() : {})
        .then(({ artwork_url }) => {
          if (!artwork_url) return;
          // Cache in memory so subsequent calls are instant
          if (track) track.artwork_url = artwork_url;
          // Only update UI if this is still the active track
          if (state.currentJob === jobId) {
            _updateArtworkImg(jobId, artwork_url, videoId, title);
            _updateMediaSession(title, videoId, artwork_url);
          }
        })
        .catch(() => {});
    }
  }

  // ── Artwork image in player bar ──────────────────────────────────────────────

  /**
   * Build initials from a track title.
   * "Tengo ganas de verte - Valerie Luh" → "T-VL"
   * "Song Title"                         → "ST"
   */
  function _trackInitials(title) {
    if (!title) return '?';
    const idx = title.indexOf(' - ');
    let song, artist;
    if (idx !== -1) {
      song   = title.slice(0, idx).trim();
      artist = title.slice(idx + 3).trim();
    } else {
      // No artist separator — use first letters of words
      const words = title.split(/\s+/).filter(Boolean);
      return words.slice(0, 3).map(w => w.charAt(0).toUpperCase()).join('');
    }
    const sInit = song   ? song.charAt(0).toUpperCase() : '';
    const aInit = artist ? artist.split(/\s+/).filter(Boolean).map(w => w.charAt(0).toUpperCase()).join('') : '';
    return aInit ? sInit + '-' + aInit : sInit;
  }

  /** Deterministic hue from a string (same algo as avatarHue in app.js). */
  function _titleHue(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = str.charCodeAt(i) + ((h << 5) - h);
    return ((h % 360) + 360) % 360;
  }

  function _updateArtworkImg(jobId, artworkUrl, videoId, title) {
    const img      = document.getElementById('player-artwork');
    const initDiv  = document.getElementById('player-artwork-initials');
    if (!img) return;

    // Priority: DB artwork → YouTube thumbnail → initials fallback
    const src = artworkUrl
      || (videoId ? `https://img.youtube.com/vi/${videoId}/mqdefault.jpg` : null);

    if (src) {
      // Hide initials, show image
      if (initDiv) { initDiv.classList.remove('visible'); initDiv.textContent = ''; }
      if (img.src === src) return;
      img.classList.remove('loaded');
      img.onload  = () => img.classList.add('loaded');
      img.onerror = () => {
        // Image failed — show initials instead
        img.classList.remove('loaded');
        img.src = '';
        _showArtworkInitials(title);
      };
      img.src = src;
    } else {
      // No external artwork — show initials fallback
      img.classList.remove('loaded');
      img.src = '';
      _showArtworkInitials(title);
    }
  }

  function _showArtworkInitials(title) {
    const initDiv = document.getElementById('player-artwork-initials');
    if (!initDiv) return;
    const initials = _trackInitials(title);
    const hue = _titleHue(title || '?');
    initDiv.textContent = initials;
    initDiv.style.background = `hsl(${hue}, 45%, 32%)`;
    initDiv.classList.add('visible');
  }

  // ── Media Session API (lock screen / headphone controls) ────────────────────

  function _parseTitle(fullTitle) {
    const idx = (fullTitle || '').indexOf(' - ');
    if (idx === -1) return { title: fullTitle || '', artist: '', album: '' };
    return { artist: fullTitle.slice(0, idx), title: fullTitle.slice(idx + 3), album: '' };
  }

  function _updateMediaSession(title, videoId, artworkUrl) {
    if (!('mediaSession' in navigator)) return;
    const parsed  = _parseTitle(title);
    // Artwork priority: DB cover art → YouTube thumbnail → app icon
    const artwork = artworkUrl
      ? [{ src: artworkUrl, sizes: '600x600', type: 'image/jpeg' }]
      : videoId
        ? [
            { src: `https://img.youtube.com/vi/${videoId}/mqdefault.jpg`,     sizes: '320x180',  type: 'image/jpeg' },
            { src: `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,     sizes: '480x360',  type: 'image/jpeg' },
            { src: `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`, sizes: '1280x720', type: 'image/jpeg' },
          ]
        : [
            { src: '/static/icon-192.png', sizes: '192x192', type: 'image/png' },
            { src: '/static/icon-512.png', sizes: '512x512', type: 'image/png' },
          ];
    navigator.mediaSession.metadata = new MediaMetadata({
      title:   parsed.title  || title || '',
      artist:  parsed.artist || '',
      album:   '',
      artwork,
    });
  }

  function _setupMediaSessionHandlers() {
    if (!('mediaSession' in navigator)) return;
    const safe = (action, handler) => {
      try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) {}
    };
    safe('play',          () => audio.play().catch(() => {}));
    safe('pause',         () => audio.pause());
    safe('stop',          () => { audio.pause(); audio.currentTime = 0; });
    safe('previoustrack', () => prevTrack());
    safe('nexttrack',     () => nextTrack());
    safe('seekbackward',  null);   // disable ±10 s buttons → iPhone shows prev/next instead
    safe('seekforward',   null);
  }

  _setupMediaSessionHandlers();

  // Expose toggleShuffle / cycleRepeat / toggleMute / togglePlay / prevTrack /
  // nextTrack as global functions so shell.html inline onclick attrs work.
  window._playerToggleShuffle = toggleShuffle;
  window._playerCycleRepeat   = cycleRepeat;
  window._playerToggleMute    = toggleMute;
  window._playerTogglePlay    = togglePlay;
  window._playerPrev          = prevTrack;
  window._playerNext          = nextTrack;

  // ── Keyboard shortcuts ───────────────────────────────────────────────────────
  document.addEventListener('keydown', function (e) {
    // Ignore when focus is inside a text input, textarea or contenteditable
    const tag = (e.target.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target.isContentEditable) return;
    // Ignore if any modifier key is held (except Shift for +/-)
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    switch (e.key) {
      case ' ':
        e.preventDefault();
        togglePlay();
        break;
      case 'm':
      case 'M':
        toggleMute();
        // Keep slider in sync
        if (elVolSlider) elVolSlider.value = audio.muted ? 0 : audio.volume;
        break;
      case '+':
      case '=': // = is the unshifted + on most keyboards
        audio.volume = Math.min(1, +(audio.volume + 0.1).toFixed(2));
        audio.muted  = false;
        if (elVolSlider) elVolSlider.value = audio.volume;
        if (elIconVol)  elIconVol.style.display  = 'block';
        if (elIconMute) elIconMute.style.display = 'none';
        break;
      case '-':
        audio.volume = Math.max(0, +(audio.volume - 0.1).toFixed(2));
        if (elVolSlider) elVolSlider.value = audio.volume;
        break;
      case 'n':
      case 'N':
        nextTrack();
        break;
      case 'p':
      case 'P':
        prevTrack();
        break;
      case 'r':
      case 'R':
        toggleShuffle();
        break;
      case 'l':
      case 'L':
        window._playerToggleLyrics?.();
        break;
    }
  });

  return {
    playTrack,
    togglePlay,
    prevTrack,
    nextTrack,
    toggleShuffle,
    cycleRepeat,
    toggleMute,
    loadTracks,
    setQueue,
    onTrackChange,
    offTrackChange,
    getState,
    setSession,
    hasSession: () => state._hasSession,
    clearSavedState,
  };
})();
