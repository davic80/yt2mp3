(function () {
  'use strict';

  // ── Avatar helpers ────────────────────────────────────────────────────────

  function avatarHue(name) {
    let h = 0;
    for (let i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
    return ((h % 360) + 360) % 360;
  }

  function avatarInitialHTML(name, cls) {
    const letter = (name || '?').charAt(0).toUpperCase();
    const hue = avatarHue(name || '?');
    const bg = `hsl(${hue}, 55%, 42%)`;
    return `<span class="avatar-initial ${cls}" style="background:${bg}">${letter}</span>`;
  }

  // Expose globally so player.html and users.html can reuse
  window._avatarHue = avatarHue;
  window._avatarInitialHTML = avatarInitialHTML;

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
        const avatarWrap = document.getElementById('auth-avatar-wrap');
        const name       = document.getElementById('auth-name');
        if (avatarWrap) {
          if (data.picture) {
            avatarWrap.innerHTML = `<img class="auth-avatar" src="${data.picture}" alt="avatar" />`;
          } else {
            avatarWrap.innerHTML = avatarInitialHTML(data.name || data.email, 'avatar-initial--topbar');
          }
        }
        if (name)   name.textContent = (data.name || data.email) + ' \u266a';
        zoneLoggedIn.classList.remove('hidden');
        zoneLoggedIn.style.display = 'flex';
        document.body.classList.add('has-session');
        if (window.Player && window.Player.setSession) window.Player.setSession(true);
        // Show admin panel link if user is admin
        if (data.is_admin) {
          const adminLink = document.getElementById('admin-link');
          if (adminLink) { adminLink.classList.remove('hidden'); adminLink.style.display = ''; }
        }
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
          loginLink.href = '/auth/login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
          // Update on every click so it reflects the current SPA path at that moment
          loginLink.addEventListener('click', () => {
            loginLink.href = '/auth/login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
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
      hideAllPlaylist();
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

    // ── Playlist element refs ───────────────────────────────────────────────
    const plConfirm       = document.getElementById('pl-confirm');
    const plConfirmTitle  = document.getElementById('pl-confirm-title');
    const plConfirmCount  = document.getElementById('pl-confirm-count');
    const btnPlConfirm    = document.getElementById('btn-pl-confirm');
    const btnPlCancel     = document.getElementById('btn-pl-cancel');
    const plProgress      = document.getElementById('pl-progress');
    const plProgressLabel = document.getElementById('pl-progress-label');
    const plProgressFill  = document.getElementById('pl-progress-fill');
    const plDetailsToggle = document.getElementById('pl-details-toggle');
    const plTrackList     = document.getElementById('pl-track-list');
    const plResult        = document.getElementById('pl-result');
    const plResultTitle   = document.getElementById('pl-result-title');
    const plResultStats   = document.getElementById('pl-result-stats');
    const btnPlGoto       = document.getElementById('btn-pl-goto');
    const btnPlZip        = document.getElementById('btn-pl-zip');
    const btnPlReset      = document.getElementById('btn-pl-reset');

    if (btnPlReset) btnPlReset.addEventListener('click', reset);

    // ── Playlist helpers ─────────────────────────────────────────────────────

    function hideAllPlaylist() {
      if (plConfirm)  plConfirm.classList.add('hidden');
      if (plProgress) plProgress.classList.add('hidden');
      if (plResult)   plResult.classList.add('hidden');
    }

    function showPlaylistConfirm(batchId, title, trackCount) {
      form.classList.add('hidden');
      hideAllPlaylist();
      if (progressArea) progressArea.classList.add('hidden');

      const I = window.I18n;
      if (plConfirmTitle) plConfirmTitle.textContent = truncate(title, 60);
      if (plConfirmCount) {
        const label = I ? I.t('pl.track_count') : '{n} tracks';
        plConfirmCount.textContent = label.replace('{n}', trackCount);
      }
      if (btnPlConfirm) btnPlConfirm.textContent = I ? I.t('pl.download_all') : 'Download All';
      if (btnPlCancel)  btnPlCancel.textContent  = I ? I.t('pl.cancel') : 'Cancel';
      if (plConfirm) plConfirm.classList.remove('hidden');

      // Wire up buttons (remove old listeners by replacing nodes)
      if (btnPlConfirm) {
        const fresh = btnPlConfirm.cloneNode(true);
        btnPlConfirm.parentNode.replaceChild(fresh, btnPlConfirm);
        fresh.addEventListener('click', () => confirmPlaylist(batchId));
      }
      if (btnPlCancel) {
        const fresh = btnPlCancel.cloneNode(true);
        btnPlCancel.parentNode.replaceChild(fresh, btnPlCancel);
        fresh.addEventListener('click', reset);
      }
    }

    async function confirmPlaylist(batchId) {
      if (plConfirm) plConfirm.classList.add('hidden');

      const I = window.I18n;
      const en = I && I.getLang() === 'en';

      // Show progress area
      if (plProgress) plProgress.classList.remove('hidden');
      if (plProgressLabel) plProgressLabel.textContent = I ? I.t('pl.starting') : 'Starting...';
      if (plProgressFill) plProgressFill.style.width = '0%';
      if (plTrackList) { plTrackList.innerHTML = ''; plTrackList.classList.add('hidden'); }

      // Wire details toggle
      let detailsOpen = false;
      if (plDetailsToggle) {
        plDetailsToggle.textContent = I ? I.t('pl.details_show') : 'Details ▼';
        const freshToggle = plDetailsToggle.cloneNode(true);
        plDetailsToggle.parentNode.replaceChild(freshToggle, plDetailsToggle);
        freshToggle.addEventListener('click', () => {
          detailsOpen = !detailsOpen;
          if (plTrackList) plTrackList.classList.toggle('hidden', !detailsOpen);
          freshToggle.textContent = detailsOpen
            ? (I ? I.t('pl.details_hide') : 'Details ▲')
            : (I ? I.t('pl.details_show') : 'Details ▼');
        });
      }

      try {
        const resp = await fetch(`/download/playlist/${batchId}/confirm`, { method: 'POST' });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          showError(data.error || (en ? 'Could not start batch' : 'No se pudo iniciar la descarga'));
          if (plProgress) plProgress.classList.add('hidden');
          return;
        }
      } catch (_) {
        showError(en ? 'Connection error' : 'Error de conexión');
        if (plProgress) plProgress.classList.add('hidden');
        return;
      }

      await pollPlaylist(batchId);
    }

    async function pollPlaylist(batchId) {
      const POLL_MS   = 2000;
      const MAX_POLLS = 600; // 20 min max
      let polls = 0;

      const I = window.I18n;

      return new Promise((resolve) => {
        const interval = setInterval(async () => {
          polls++;
          if (polls > MAX_POLLS) {
            clearInterval(interval);
            const en = I && I.getLang() === 'en';
            showError(en ? 'Timed out. Please try again.' : 'Tiempo de espera agotado.');
            if (plProgress) plProgress.classList.add('hidden');
            resolve();
            return;
          }

          let data;
          try {
            const resp = await fetch(`/download/playlist/${batchId}/status`);
            data = await resp.json();
          } catch (_) { return; }

          const total     = data.total || 0;
          const completed = data.completed || 0;
          const failed    = data.failed || 0;
          const skipped   = data.skipped || 0;
          const done      = completed + failed + skipped;
          const pct       = total > 0 ? Math.round((done / total) * 100) : 0;

          if (plProgressFill) plProgressFill.style.width = pct + '%';

          const en = I && I.getLang() === 'en';
          if (plProgressLabel) {
            const tpl = I ? I.t('pl.downloading_n') : 'Downloading {done} / {total}...';
            plProgressLabel.textContent = tpl.replace('{done}', done).replace('{total}', total);
          }

          // Update per-track list
          renderTrackList(data.tracks || []);

          if (data.status === 'done' || data.status === 'error') {
            clearInterval(interval);
            if (plProgressFill) plProgressFill.style.width = '100%';
            setTimeout(() => {
              if (plProgress) plProgress.classList.add('hidden');
              showPlaylistResult(data);
            }, 500);
            resolve();
          }
        }, POLL_MS);
      });
    }

    function renderTrackList(tracks) {
      const list = document.getElementById('pl-track-list');
      if (!list) return;
      list.innerHTML = '';
      for (const tr of tracks) {
        const item = document.createElement('div');
        item.className = 'track-item';

        let iconCls, iconChar;
        switch (tr.status) {
          case 'done':        iconCls = 'ti-done';  iconChar = '✓'; break;
          case 'downloading': iconCls = 'ti-dl';    iconChar = '↓'; break;
          case 'error':       iconCls = 'ti-err';   iconChar = '✕'; break;
          case 'skipped':     iconCls = 'ti-done';  iconChar = '–'; break;
          default:            iconCls = 'ti-queue'; iconChar = '·'; break;
        }

        item.innerHTML =
          `<span class="ti-icon ${iconCls}">${iconChar}</span>` +
          `<span class="ti-title">${escapeHtml(truncate(tr.title || tr.video_id || '?', 55))}</span>`;
        list.appendChild(item);
      }
    }

    function showPlaylistResult(data) {
      hideAllPlaylist();
      if (plResult) plResult.classList.remove('hidden');

      const I = window.I18n;
      const en = I && I.getLang() === 'en';
      const completed = data.completed || 0;
      const failed    = data.failed || 0;
      const skipped   = data.skipped || 0;

      if (plResultTitle) {
        const tpl = I ? I.t('pl.result_title') : '{n} tracks downloaded';
        plResultTitle.textContent = tpl.replace('{n}', completed);
      }

      if (plResultStats) {
        const parts = [];
        if (failed > 0) {
          const tpl = I ? I.t('pl.result_failed') : '{n} failed';
          parts.push(tpl.replace('{n}', failed));
        }
        if (skipped > 0) {
          const tpl = I ? I.t('pl.result_skipped') : '{n} skipped';
          parts.push(tpl.replace('{n}', skipped));
        }
        plResultStats.textContent = parts.join(' · ');
        plResultStats.style.display = parts.length ? '' : 'none';
      }

      if (btnPlGoto) {
        if (data.app_playlist_id) {
          btnPlGoto.href = `/player?playlist=${data.app_playlist_id}`;
          btnPlGoto.textContent = I ? I.t('pl.goto_playlist') : 'Go to Playlist';
          btnPlGoto.style.display = '';
        } else {
          btnPlGoto.style.display = 'none';
        }
      }

      if (btnPlZip) {
        if (completed > 0) {
          btnPlZip.href = `/download/playlist/${data.batch_id}/zip`;
          btnPlZip.textContent = I ? I.t('pl.download_zip') : '↓ ZIP';
          btnPlZip.style.display = '';
        } else {
          btnPlZip.style.display = 'none';
        }
      }

      if (btnPlReset) {
        btnPlReset.textContent = I ? I.t('home.convert_another') : 'convert another';
      }
    }

    function escapeHtml(str) {
      const d = document.createElement('div');
      d.textContent = str;
      return d.innerHTML;
    }

    // ── Submit handler ───────────────────────────────────────────────────────

    async function submitDownload(url) {
      if (progressArea) progressArea.classList.remove('hidden');
      const en = window.I18n && window.I18n.getLang() === 'en';
      setProgress(5, en ? 'sending...' : 'enviando...');

      const fpData = window._fpData || {};

      let data;
      try {
        const resp = await fetch('/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, fingerprint: JSON.stringify(fpData) }),
        });
        data = await resp.json();
        if (!resp.ok) {
          // Handle login_required for playlists
          if (data.error === 'login_required') {
            if (progressArea) progressArea.classList.add('hidden');
            const I = window.I18n;
            const msg = I ? I.t('pl.login_required')
              : (en ? 'Sign in to download playlists' : 'Inicia sesión para descargar playlists');
            showError(msg);
            return;
          }
          showError(data.error || (en ? 'Unknown error' : 'Error desconocido'));
          return;
        }
      } catch (err) {
        showError(en ? 'Connection error. Are you online?' : 'Error de conexión. ¿Estás conectado?');
        return;
      }

      // ── Playlist response ──────────────────────────────────────────────────
      if (data.type === 'playlist') {
        if (progressArea) progressArea.classList.add('hidden');
        showPlaylistConfirm(data.batch_id, data.title, data.track_count);
        return;
      }

      // ── Single-track response ──────────────────────────────────────────────
      const jobId = data.job_id || (data.job_ids && data.job_ids[0]);
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
