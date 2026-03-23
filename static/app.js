(function () {
  'use strict';

  // ── Auth zone (runs once when shell loads) ────────────────────────────────

  async function initAuthZone() {
    const zoneLoggedIn  = document.getElementById('auth-loggedin');
    const zoneLoggedOut = document.getElementById('auth-loggedout');
    if (!zoneLoggedIn || !zoneLoggedOut) return;

    try {
      const resp = await fetch('/auth/me');
      if (!resp.ok) throw new Error('non-ok');
      const data = await resp.json();

      if (data && data.email) {
        const avatar = document.getElementById('auth-avatar');
        const name   = document.getElementById('auth-name');
        if (avatar && data.picture) avatar.src = data.picture;
        if (name)   name.textContent = (data.name || data.email) + ' \u266a';
        zoneLoggedIn.classList.remove('hidden');
        zoneLoggedIn.style.display = 'flex';
        document.body.classList.add('has-session');
        if (window.Player && window.Player.setSession) window.Player.setSession(true);
        // Clear saved playback state on logout
        const logoutLink = document.querySelector('a[href="/auth/logout"]');
        if (logoutLink) {
          logoutLink.addEventListener('click', () => {
            if (window.Player && window.Player.clearSavedState) window.Player.clearSavedState();
          });
        }
      } else {
        // Set ?next= to current SPA path so login returns to where the user was
        const loginLink = zoneLoggedOut.querySelector('a.auth-btn.login');
        if (loginLink) {
          loginLink.href = '/auth/login?next=' + encodeURIComponent(window.location.pathname);
          // Update on every click so it reflects the current SPA path at that moment
          loginLink.addEventListener('click', () => {
            loginLink.href = '/auth/login?next=' + encodeURIComponent(window.location.pathname);
          });
        }
        zoneLoggedOut.classList.remove('hidden');
        zoneLoggedOut.style.display = 'block';
      }
    } catch (_) {
      const zlo = document.getElementById('auth-loggedout');
      if (zlo) { zlo.classList.remove('hidden'); zlo.style.display = 'block'; }
    }
  }

  // ── Download form (called each time the home fragment is mounted) ─────────

  function initDownloadForm() {
    const form          = document.getElementById('form');
    if (!form) return;   // not on the home page — bail out

    const urlInput      = document.getElementById('url-input');
    const btnMagic      = document.getElementById('btn-magic');
    const progressArea  = document.getElementById('progress-area');
    const progressFill  = document.getElementById('progress-bar-fill');
    const progressLabel = document.getElementById('progress-label');
    const resultArea    = document.getElementById('result-area');
    const downloadLink  = document.getElementById('download-link');
    const downloadLabel = document.getElementById('download-label');
    const errorArea     = document.getElementById('error-area');
    const errorMsg      = document.getElementById('error-msg');
    const btnReset      = document.getElementById('btn-reset');
    const btnResetErr   = document.getElementById('btn-reset-err');

    if (btnReset)    btnReset.addEventListener('click', reset);
    if (btnResetErr) btnResetErr.addEventListener('click', reset);

    function reset() {
      if (resultArea)   resultArea.classList.add('hidden');
      if (errorArea)    errorArea.classList.add('hidden');
      if (progressArea) progressArea.classList.add('hidden');
      form.classList.remove('hidden');
      if (urlInput)  urlInput.disabled  = false;
      if (btnMagic)  btnMagic.disabled  = false;
      if (urlInput)  urlInput.value     = '';
      if (urlInput)  urlInput.focus();
      setProgress(0, window.I18n ? window.I18n.t('home.preparing') : 'preparando...');
    }

    function setProgress(pct, label) {
      if (progressFill)  progressFill.style.width    = pct + '%';
      if (label && progressLabel) progressLabel.textContent = label;
    }

    function showError(msg) {
      form.classList.remove('hidden');
      if (progressArea) progressArea.classList.add('hidden');
      if (resultArea)   resultArea.classList.add('hidden');
      if (errorArea)    errorArea.classList.remove('hidden');
      if (errorMsg)     errorMsg.textContent = msg;
      if (urlInput)  urlInput.disabled  = false;
      if (btnMagic)  btnMagic.disabled  = false;
    }

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const url = urlInput ? urlInput.value.trim() : '';
      if (!url) { if (urlInput) urlInput.focus(); return; }

      if (urlInput) urlInput.disabled = true;
      if (btnMagic) btnMagic.disabled = true;
      if (errorArea)  errorArea.classList.add('hidden');
      if (resultArea) resultArea.classList.add('hidden');

      submitDownload(url);
    });

    async function submitDownload(url) {
      if (progressArea) progressArea.classList.remove('hidden');
      const en = window.I18n && window.I18n.getLang() === 'en';
      setProgress(5, en ? 'sending...' : 'enviando...');

      const fpData = window._fpData || {};

      let jobId;
      try {
        const resp = await fetch('/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, fingerprint: JSON.stringify(fpData) }),
        });
        const data = await resp.json();
        if (!resp.ok) { showError(data.error || (en ? 'Unknown error' : 'Error desconocido')); return; }
        jobId = data.job_id || (data.job_ids && data.job_ids[0]);
      } catch (err) {
        showError(en ? 'Connection error. Are you online?' : 'Error de conexión. ¿Estás conectado?');
        return;
      }

      if (!jobId) { showError(en ? 'Server returned no task.' : 'El servidor no devolvió ninguna tarea.'); return; }

      setProgress(10, en ? 'downloading...' : 'descargando...');
      await pollSingle(jobId);
    }

    async function pollSingle(jobId) {
      const POLL_MS   = 1500;
      const MAX_POLLS = 200;
      let polls = 0;

      return new Promise((resolve) => {
        const interval = setInterval(async () => {
          polls++;
          if (polls > MAX_POLLS) {
            clearInterval(interval);
            const en = window.I18n && window.I18n.getLang() === 'en';
            showError(en ? 'Timed out. Please try again.' : 'Tiempo de espera agotado. Inténtalo de nuevo.');
            resolve();
            return;
          }

          let data;
          try {
            const resp = await fetch(`/status/${jobId}`);
            data = await resp.json();
          } catch (_) { return; }

          const pct    = data.progress || 0;
          const status = data.status;
          const en = window.I18n && window.I18n.getLang() === 'en';

          if (status === 'pending' || status === 'downloading') {
            const label = en
              ? (pct < 20 ? 'analyzing...'  : pct < 60 ? 'downloading...' : pct < 90 ? 'converting to mp3...' : 'almost done...')
              : (pct < 20 ? 'analizando...' : pct < 60 ? 'descargando...' : pct < 90 ? 'convirtiendo a mp3...' : 'casi listo...');
            setProgress(Math.max(pct, 10), label);

          } else if (status === 'done') {
            clearInterval(interval);
            setProgress(100, en ? 'done!' : '¡listo!');
            setTimeout(() => {
              if (progressArea) progressArea.classList.add('hidden');
              if (resultArea)   resultArea.classList.remove('hidden');
              if (downloadLink) {
                downloadLink.style.display = '';
                downloadLink.href = `/files/${jobId}`;
              }
              const title = data.title || 'audio';
              let label = `↓ ${truncate(title, 40)}.mp3`;
              if (data.file_size) {
                const mb = (data.file_size / 1048576).toFixed(1);
                label += ` · ${mb} MB`;
              }
              if (downloadLabel) downloadLabel.textContent = label;
            }, 400);
            resolve();

          } else if (status === 'error') {
            clearInterval(interval);
            const en2 = window.I18n && window.I18n.getLang() === 'en';
            showError(data.error_message || (en2 ? 'Error processing the video.' : 'Error procesando el vídeo.'));
            resolve();
          }
        }, POLL_MS);
      });
    }
  }

  function truncate(str, max) {
    return str.length > max ? str.slice(0, max) + '\u2026' : str;
  }

  // Expose so home fragment can re-init after SPA swap
  window._initDownloadForm = initDownloadForm;

  // Run once on shell load
  initAuthZone();
  initDownloadForm();   // no-op if form not present on initial page
})();
