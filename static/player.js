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
  });
  audio.addEventListener('pause', () => {
    if (elIconPlay)  elIconPlay.style.display  = 'block';
    if (elIconPause) elIconPause.style.display = 'none';
    document.body.classList.remove('is-playing');
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
  });

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

  function playTrack(jobId) {
    const idx = state.queue.indexOf(jobId);
    state.currentJob  = jobId;
    state.queueIndex  = idx >= 0 ? idx : 0;
    audio.src = `/player/stream/${jobId}`;
    audio.play().catch(() => {});
    _updateTitle(jobId);
    _emit('trackChange', jobId);
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
      idx = Math.floor(Math.random() * state.queue.length);
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
  function _updateTitle(jobId) {
    if (!elTitle) return;
    const track = state.tracks.find(t => t.job_id === jobId);
    elTitle.classList.remove('player-title-empty');
    elTitle.textContent = track ? (track.title || jobId) : jobId;
  }

  // Expose toggleShuffle / cycleRepeat / toggleMute / togglePlay / prevTrack /
  // nextTrack as global functions so shell.html inline onclick attrs work.
  window._playerToggleShuffle = toggleShuffle;
  window._playerCycleRepeat   = cycleRepeat;
  window._playerToggleMute    = toggleMute;
  window._playerTogglePlay    = togglePlay;
  window._playerPrev          = prevTrack;
  window._playerNext          = nextTrack;

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
  };
})();
