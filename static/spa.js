/**
 * spa.js — v4.0.0
 * SPA navigation engine. Intercepts internal <a> clicks, fetches fragments,
 * swaps #page-content, updates history. Audio in the player bar keeps playing.
 */
(function () {
  'use strict';

  // Paths that must always do a full-page navigation (never SPA-intercepted)
  const FULL_NAV_RE = /^\/(auth|db|files|static)\//;

  // ── Navigation ──────────────────────────────────────────────────────────────

  async function navigate(href, { pushState = true } = {}) {
    let url;
    try { url = new URL(href, location.origin); } catch (_) { return; }

    const sep  = url.search ? '&' : '?';
    const fetchUrl = url.pathname + url.search + sep + 'fragment=1';

    let res;
    try {
      res = await fetch(fetchUrl, { redirect: 'follow' });
    } catch (_) {
      // Network error — fall back to full navigation
      window.location.href = href;
      return;
    }

    // If the server redirected us to /auth/... (user_required redirect),
    // do a real full-page navigation so the login flow works correctly.
    if (res.redirected && res.url.includes('/auth/')) {
      window.location.href = res.url;
      return;
    }

    if (!res.ok) {
      window.location.href = href;
      return;
    }

    const html = await res.text();
    const container = document.getElementById('page-content');
    if (!container) return;

    // Clear trackChange listeners registered by the previous fragment
    if (window.Player) window.Player.offTrackChange();

    // Remember if audio was playing so we can resume if the DOM swap interrupts it
    const _audio = document.getElementById('audio');
    const _wasPlaying = _audio && !_audio.paused;

    container.innerHTML = html;

    if (pushState) history.pushState({ href }, '', href);

    // Re-run <script> tags — innerHTML doesn't execute them
    runScripts(container);

    // Resume playback if the DOM swap caused the browser to pause the audio.
    // Deferred to next tick so the browser finishes processing the DOM mutation
    // before we check paused state (Chrome can pause media asynchronously).
    if (_wasPlaying) {
      setTimeout(() => { if (_audio.paused) _audio.play().catch(() => {}); }, 0);
    }

    updateTopbarActive(url.pathname);

    // Scroll page-content back to top on navigation
    container.scrollTop = 0;
  }

  function runScripts(container) {
    container.querySelectorAll('script').forEach(old => {
      const s = document.createElement('script');
      // Copy all attributes (type, src, etc.)
      Array.from(old.attributes).forEach(attr => s.setAttribute(attr.name, attr.value));
      s.textContent = old.textContent;
      old.parentNode.replaceChild(s, old);
    });
  }

  function updateTopbarActive(pathname) {
    document.querySelectorAll('.topbar-link[data-path]').forEach(a => {
      const match = pathname === a.dataset.path ||
                    (a.dataset.path !== '/' && pathname.startsWith(a.dataset.path));
      a.classList.toggle('topbar-link-active', match);
    });
  }

  // ── Click interception ──────────────────────────────────────────────────────

  document.addEventListener('click', function (e) {
    const a = e.target.closest('a[href]');
    if (!a) return;

    let url;
    try { url = new URL(a.href, location.origin); } catch (_) { return; }

    // External link
    if (url.hostname !== location.hostname) return;
    // Explicit new-tab or download
    if (a.target === '_blank' || a.hasAttribute('download')) return;
    // Auth / admin / file-serve / static — full navigation
    if (FULL_NAV_RE.test(url.pathname)) return;
    // Anchor-only (#hash)
    if (url.pathname === location.pathname && url.hash) return;

    e.preventDefault();
    navigate(a.href);
  });

  // ── Back / Forward ──────────────────────────────────────────────────────────

  window.addEventListener('popstate', function (e) {
    const href = (e.state && e.state.href) || location.href;
    navigate(href, { pushState: false });
  });

  // ── Init: set active topbar link for the initial page load ──────────────────

  updateTopbarActive(location.pathname);

})();
