/**
 * lyrics.js — v4.5.0
 * Shared lyrics panel engine. Loaded once in shell.html.
 * Works with any fragment that includes the lyrics panel DOM:
 *   <div class="lyrics-panel hidden" id="lyrics-panel"> ... </div>
 *
 * Exposes window.Lyrics and convenience globals:
 *   window.openLyrics(jobId)
 *   window.closeLyrics()
 *   window.growLyrics()
 *   window.shrinkLyrics()
 */
window.Lyrics = (function () {
  'use strict';

  // ── Private state ────────────────────────────────────────────────────────────
  let _enabled      = false;
  let _lyricsJob    = null;
  let _lrcLines     = [];        // [{time, text}] for synced lyrics
  let _fontSize     = 1.00;      // rem, adjustable with grow/shrink
  const _FONT_MIN   = 0.70;
  const _FONT_MAX   = 1.50;
  const _FONT_STEP  = 0.08;

  // ── HTML escaping ─────────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── LRC parsing ──────────────────────────────────────────────────────────────
  function _parseLrc(lrc) {
    const lines = [];
    for (const line of lrc.split('\n')) {
      const m = line.match(/^\[(\d+):(\d+\.\d+)\](.*)/);
      if (m) {
        const time = parseInt(m[1]) * 60 + parseFloat(m[2]);
        lines.push({ time, text: m[3].trim() });
      }
    }
    return lines;
  }

  // ── LRC rendering ────────────────────────────────────────────────────────────
  function _renderLrcLines() {
    const body = document.getElementById('lyrics-body');
    if (!body) return;
    body.innerHTML = _lrcLines.map((l, i) =>
      `<span class="lyrics-line" id="lrc-${i}" style="font-size:${_fontSize}rem">${_esc(l.text) || '&nbsp;'}</span>`
    ).join('');
  }

  function _applyFontSize() {
    document.querySelectorAll('.lyrics-line').forEach(el => {
      el.style.fontSize = _fontSize + 'rem';
    });
    const plain = document.querySelector('.lyrics-plain');
    if (plain) plain.style.fontSize = _fontSize + 'rem';
  }

  // ── LRC sync (timeupdate) ────────────────────────────────────────────────────
  function _syncLrc() {
    const audio = document.getElementById('audio');
    if (!audio) return;
    const t = audio.currentTime;
    let active = -1;
    for (let i = 0; i < _lrcLines.length; i++) {
      if (_lrcLines[i].time <= t) active = i;
      else break;
    }
    document.querySelectorAll('.lyrics-line').forEach((el, i) => {
      el.classList.toggle('active', i === active);
    });
    if (active >= 0) {
      const el = document.getElementById(`lrc-${active}`);
      if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  function _startLrcSync() {
    _stopLrcSync();
    const audio = document.getElementById('audio');
    if (audio) audio.addEventListener('timeupdate', _syncLrc);
  }

  function _stopLrcSync() {
    const audio = document.getElementById('audio');
    if (audio) audio.removeEventListener('timeupdate', _syncLrc);
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  function setEnabled(val) {
    _enabled = Boolean(val);
  }

  function isEnabled() {
    return _enabled;
  }

  async function open(jobId) {
    if (!_enabled) return;

    const panel = document.getElementById('lyrics-panel');
    if (!panel) return;  // fragment without lyrics panel — ignore

    // If already open for same track, close
    if (_lyricsJob === jobId && !panel.classList.contains('hidden')) {
      close();
      return;
    }

    _lyricsJob = jobId;
    const body  = document.getElementById('lyrics-body');
    const title = document.getElementById('lyrics-panel-title');

    // Mark per-row button active
    document.querySelectorAll('.lyrics-btn').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(`lyrbtn-${jobId}`);
    if (btn) btn.classList.add('active');
    window._setLyricsBtnActive?.(true);

    const I = window.I18n;
    title.textContent = I.t('player.lyrics');
    body.innerHTML = `<div class="lyrics-loading">${I.t('player.lyrics_loading')}</div>`;
    panel.classList.remove('hidden', 'open');
    // Mobile slide-in: small delay so transition fires
    setTimeout(() => panel.classList.add('open'), 10);

    try {
      const res = await fetch(`/player/api/lyrics/${jobId}`);
      if (!res.ok) {
        body.innerHTML = `<div class="lyrics-not-found">${I.t('player.lyrics_not_found')}</div>`;
        return;
      }
      const data = await res.json();

      // Update header with synced status
      title.textContent = I.t(data.synced ? 'player.lyrics_synced' : 'player.lyrics_not_synced');

      if (data.synced && data.content) {
        _lrcLines = _parseLrc(data.content);
        _renderLrcLines();
        _startLrcSync();
      } else {
        _lrcLines = [];
        _stopLrcSync();
        body.innerHTML = `<pre class="lyrics-plain">${_esc(data.plain || data.content || '')}</pre>`;
      }
    } catch (_) {
      body.innerHTML = `<div class="lyrics-not-found">${I.t('player.lyrics_not_found')}</div>`;
    }
  }

  function close() {
    _lyricsJob = null;
    _lrcLines  = [];
    _fontSize  = 1.00;
    _stopLrcSync();
    document.querySelectorAll('.lyrics-btn').forEach(b => b.classList.remove('active'));
    window._setLyricsBtnActive?.(false);
    const panel = document.getElementById('lyrics-panel');
    if (!panel) return;
    panel.classList.remove('open');
    setTimeout(() => panel.classList.add('hidden'), 240);
  }

  function grow() {
    _fontSize = Math.min(_FONT_MAX, +(_fontSize + _FONT_STEP).toFixed(2));
    _applyFontSize();
  }

  function shrink() {
    _fontSize = Math.max(_FONT_MIN, +(_fontSize - _FONT_STEP).toFixed(2));
    _applyFontSize();
  }

  /** Returns the jobId currently open, or null */
  function currentJob() {
    return _lyricsJob;
  }

  /** Called by shell._playerToggleLyrics and player bar button */
  function toggle(jobId) {
    if (!jobId) return;
    const panel = document.getElementById('lyrics-panel');
    const isOpen = panel && !panel.classList.contains('hidden');
    if (isOpen && _lyricsJob === jobId) {
      close();
    } else {
      open(jobId);
    }
  }

  return { setEnabled, isEnabled, open, close, grow, shrink, toggle, currentJob };
})();

// ── Convenience globals for fragment inline onclick attrs ─────────────────────
window.openLyrics   = (id) => window.Lyrics.open(id);
window.closeLyrics  = ()   => window.Lyrics.close();
window.growLyrics   = ()   => window.Lyrics.grow();
window.shrinkLyrics = ()   => window.Lyrics.shrink();
