(function () {
  'use strict';

  const form          = document.getElementById('form');
  const urlInput      = document.getElementById('url-input');
  const btnMagic      = document.getElementById('btn-magic');
  const progressArea  = document.getElementById('progress-area');
  const progressFill  = document.getElementById('progress-fill');
  const progressLabel = document.getElementById('progress-label');
  const resultArea    = document.getElementById('result-area');
  const downloadLink  = document.getElementById('download-link');
  const downloadLabel = document.getElementById('download-label');
  const errorArea     = document.getElementById('error-area');
  const errorMsg      = document.getElementById('error-msg');

  document.getElementById('btn-reset').addEventListener('click', reset);
  document.getElementById('btn-reset-err').addEventListener('click', reset);

  // ── Playlist confirmation banner (injected dynamically) ──────────────────
  let playlistBanner = null;

  function showPlaylistBanner(onZip, onIndividual) {
    removePlaylistBanner();
    playlistBanner = document.createElement('div');
    playlistBanner.id = 'playlist-banner';
    playlistBanner.style.cssText = [
      'margin-top:12px',
      'padding:10px 14px',
      'background:#222',
      'border:1px solid #39FF14',
      'border-radius:6px',
      'font-size:0.85rem',
      'color:#ccc',
      'display:flex',
      'flex-direction:column',
      'gap:10px',
    ].join(';');

    const msg = document.createElement('span');
    msg.textContent = 'Playlist detectada — ¿cómo quieres descargar?';

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';

    const btnZip = document.createElement('button');
    btnZip.textContent = 'Descargar ZIP';
    btnZip.style.cssText = 'background:#39FF14;color:#111;border:none;padding:5px 14px;border-radius:4px;cursor:pointer;font-weight:700;font-size:0.85rem;';
    btnZip.addEventListener('click', function () {
      removePlaylistBanner();
      onZip();
    });

    const btnInd = document.createElement('button');
    btnInd.textContent = 'Canción por canción';
    btnInd.style.cssText = 'background:transparent;color:#39FF14;border:1px solid #39FF14;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:0.85rem;';
    btnInd.addEventListener('click', function () {
      removePlaylistBanner();
      onIndividual();
    });

    const btnNo = document.createElement('button');
    btnNo.textContent = 'Cancelar';
    btnNo.style.cssText = 'background:transparent;color:#aaa;border:1px solid #555;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:0.85rem;margin-left:auto;';
    btnNo.addEventListener('click', function () {
      removePlaylistBanner();
      urlInput.disabled = false;
      btnMagic.disabled = false;
    });

    btnRow.appendChild(btnZip);
    btnRow.appendChild(btnInd);
    btnRow.appendChild(btnNo);
    playlistBanner.appendChild(msg);
    playlistBanner.appendChild(btnRow);
    form.parentNode.insertBefore(playlistBanner, form.nextSibling);
  }

  function removePlaylistBanner() {
    if (playlistBanner && playlistBanner.parentNode) {
      playlistBanner.parentNode.removeChild(playlistBanner);
    }
    playlistBanner = null;
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function reset() {
    resultArea.classList.add('hidden');
    errorArea.classList.add('hidden');
    progressArea.classList.add('hidden');
    removePlaylistBanner();
    // Remove injected playlist result if present
    const pr = document.getElementById('playlist-result');
    if (pr) pr.parentNode.removeChild(pr);
    form.classList.remove('hidden');
    urlInput.disabled = false;
    btnMagic.disabled = false;
    urlInput.value = '';
    urlInput.focus();
    setProgress(0, 'preparando...');
  }

  function setProgress(pct, label) {
    progressFill.style.width = pct + '%';
    if (label) progressLabel.textContent = label;
  }

  function showError(msg) {
    form.classList.remove('hidden');
    progressArea.classList.add('hidden');
    resultArea.classList.add('hidden');
    errorArea.classList.remove('hidden');
    errorMsg.textContent = msg;
    urlInput.disabled = false;
    btnMagic.disabled = false;
  }

  function isPlaylistOnly(url) {
    try {
      const u = new URL(url.startsWith('http') ? url : 'https://' + url);
      return u.searchParams.has('list');
    } catch (_) {
      return false;
    }
  }

  // ── Form submit ───────────────────────────────────────────────────────────

  form.addEventListener('submit', async function (e) {
    e.preventDefault();

    const url = urlInput.value.trim();
    if (!url) { urlInput.focus(); return; }

    urlInput.disabled = true;
    btnMagic.disabled = true;
    errorArea.classList.add('hidden');
    resultArea.classList.add('hidden');
    removePlaylistBanner();

    if (isPlaylistOnly(url)) {
      showPlaylistBanner(
        () => submitDownload(url, 'zip'),
        () => submitDownload(url, 'individual')
      );
    } else {
      submitDownload(url, 'single');
    }
  });

  async function submitDownload(url, mode) {
    progressArea.classList.remove('hidden');
    setProgress(5, 'enviando...');

    const fpData = window._fpData || {};

    let jobIds;
    try {
      const resp = await fetch('/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, fingerprint: JSON.stringify(fpData) }),
      });

      const data = await resp.json();

      if (!resp.ok) {
        showError(data.error || 'Error desconocido');
        return;
      }

      jobIds = data.job_ids || (data.job_id ? [data.job_id] : []);
    } catch (err) {
      showError('Error de conexión. ¿Estás conectado?');
      return;
    }

    if (!jobIds.length) {
      showError('El servidor no devolvió ninguna tarea.');
      return;
    }

    if (jobIds.length === 1) {
      setProgress(10, 'descargando...');
      await pollSingle(jobIds[0]);
    } else {
      await pollPlaylist(jobIds, mode);
    }
  }

  // ── Single-track polling ──────────────────────────────────────────────────

  async function pollSingle(jobId) {
    const POLL_MS   = 1500;
    const MAX_POLLS = 200;
    let polls = 0;

    return new Promise((resolve) => {
      const interval = setInterval(async () => {
        polls++;
        if (polls > MAX_POLLS) {
          clearInterval(interval);
          showError('Tiempo de espera agotado. Inténtalo de nuevo.');
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

        if (status === 'pending' || status === 'downloading') {
          const label = pct < 20 ? 'analizando...'
                      : pct < 60 ? 'descargando...'
                      : pct < 90 ? 'convirtiendo a mp3...'
                      : 'casi listo...';
          setProgress(Math.max(pct, 10), label);

        } else if (status === 'done') {
          clearInterval(interval);
          setProgress(100, '¡listo!');

          setTimeout(() => {
            progressArea.classList.add('hidden');
            resultArea.classList.remove('hidden');
            downloadLink.style.display = '';
            downloadLink.href = `/files/${jobId}`;
            const title = data.title || 'audio';
            downloadLabel.textContent = `↓ ${truncate(title, 40)}.mp3`;
          }, 400);
          resolve();

        } else if (status === 'error') {
          clearInterval(interval);
          showError(data.error_message || 'Error procesando el vídeo.');
          resolve();
        }
      }, POLL_MS);
    });
  }

  // ── Playlist polling ──────────────────────────────────────────────────────

  async function pollPlaylist(jobIds, mode) {
    const total    = jobIds.length;
    const POLL_MS  = 2000;
    const MAX_WAIT = 600000; // 10 min hard cap
    const started  = Date.now();

    setProgress(5, `Descargando: 0 / ${total}`);

    const statuses = Object.fromEntries(jobIds.map(id => [id, 'pending']));
    const jobData  = {};   // id → last /status response (for title + file_name)
    let done   = 0;
    let errors = 0;

    await new Promise((resolve) => {
      const interval = setInterval(async () => {
        if (Date.now() - started > MAX_WAIT) {
          clearInterval(interval);
          showError('Tiempo de espera agotado. Puede que algunos tracks se hayan descargado.');
          resolve();
          return;
        }

        const pending = jobIds.filter(id => statuses[id] === 'pending' || statuses[id] === 'downloading');
        if (!pending.length) {
          clearInterval(interval);
          resolve();
          return;
        }

        await Promise.all(pending.map(async (id) => {
          try {
            const resp = await fetch(`/status/${id}`);
            const data = await resp.json();
            jobData[id] = data;
            if (data.status === 'done') {
              statuses[id] = 'done';
              done++;
            } else if (data.status === 'error') {
              statuses[id] = 'error';
              errors++;
              done++;
            } else {
              statuses[id] = data.status || 'pending';
            }
          } catch (_) { /* transient */ }
        }));

        const pct = Math.round((done / total) * 95);
        setProgress(pct, `Descargando: ${done} / ${total}`);
      }, POLL_MS);
    });

    // All settled — hand off to the right completion handler
    setProgress(100, '¡listo!');
    await new Promise(r => setTimeout(r, 400));
    progressArea.classList.add('hidden');

    if (mode === 'zip') {
      await finishZipMode(jobIds, total, errors);
    } else {
      finishIndividualMode(jobIds, jobData, total, errors);
    }
  }

  // ── ZIP completion ────────────────────────────────────────────────────────

  async function finishZipMode(jobIds, total, errors) {
    const successCount = total - errors;
    setProgress(100, 'preparando ZIP...');
    progressArea.classList.remove('hidden');

    try {
      const resp = await fetch('/playlist-zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: jobIds }),
      });

      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}));
        showError(d.error || 'No se pudo crear el ZIP.');
        return;
      }

      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `yt2mp3-${new Date().toISOString().slice(0, 10)}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);

      progressArea.classList.add('hidden');
      resultArea.classList.remove('hidden');
      downloadLink.style.display = 'none';
      downloadLabel.textContent = errors > 0
        ? `${successCount} / ${total} tracks en el ZIP (${errors} errores)`
        : `${total} tracks descargados en ZIP`;
    } catch (err) {
      showError('Error al descargar el ZIP.');
    }
  }

  // ── Individual completion ─────────────────────────────────────────────────

  function finishIndividualMode(jobIds, jobData, total, errors) {
    const successCount = total - errors;
    const doneIds = jobIds.filter(id => jobData[id] && jobData[id].status === 'done');

    // Build the playlist result block inside #result-area
    // Hide the standard single-track link
    downloadLink.style.display = 'none';

    // Remove any previous playlist-result
    const old = document.getElementById('playlist-result');
    if (old) old.parentNode.removeChild(old);

    const container = document.createElement('div');
    container.id = 'playlist-result';

    // Scrollable track list
    const list = document.createElement('div');
    list.className = 'playlist-links';

    doneIds.forEach(id => {
      const d = jobData[id];
      const title = (d && d.title) ? truncate(d.title, 50) : id;
      const a = document.createElement('a');
      a.href = `/files/${id}`;
      a.download = '';
      a.className = 'playlist-track-link';
      a.textContent = `↓ ${title}.mp3`;
      list.appendChild(a);
    });

    container.appendChild(list);

    // ZIP all button
    if (doneIds.length > 0) {
      const zipBtn = document.createElement('a');
      zipBtn.href = '#';
      zipBtn.className = 'download-btn';
      zipBtn.style.marginTop = '0.5rem';
      zipBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
      </svg><span>↓ descargar todo .zip</span>`;
      zipBtn.addEventListener('click', async function (e) {
        e.preventDefault();
        zipBtn.style.opacity = '0.5';
        zipBtn.style.pointerEvents = 'none';
        try {
          const resp = await fetch('/playlist-zip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_ids: jobIds }),
          });
          if (!resp.ok) { zipBtn.style.opacity = '1'; zipBtn.style.pointerEvents = ''; return; }
          const blob = await resp.blob();
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = `yt2mp3-${new Date().toISOString().slice(0, 10)}.zip`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        } catch (_) {}
        zipBtn.style.opacity = '1';
        zipBtn.style.pointerEvents = '';
      });
      container.appendChild(zipBtn);
    }

    // Summary label
    const summary = document.createElement('p');
    summary.className = 'progress-label';
    summary.style.marginTop = '0.6rem';
    summary.textContent = errors > 0
      ? `${successCount} / ${total} tracks · ${errors} errores`
      : `${total} tracks descargados`;
    container.appendChild(summary);

    // Insert before the reset button inside result-area
    const resetBtn = resultArea.querySelector('.reset-btn');
    resultArea.insertBefore(container, resetBtn);

    resultArea.classList.remove('hidden');
  }

  function truncate(str, max) {
    return str.length > max ? str.slice(0, max) + '\u2026' : str;
  }
})();
