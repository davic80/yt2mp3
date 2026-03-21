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

  function showPlaylistBanner(onConfirm) {
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
      'align-items:center',
      'gap:12px',
      'flex-wrap:wrap',
    ].join(';');

    const msg = document.createElement('span');
    msg.textContent = 'Playlist detected — all songs will be downloaded individually.';
    msg.style.flex = '1';

    const btnYes = document.createElement('button');
    btnYes.textContent = 'Download all';
    btnYes.style.cssText = 'background:#39FF14;color:#111;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-weight:700;';
    btnYes.addEventListener('click', function () {
      removePlaylistBanner();
      onConfirm();
    });

    const btnNo = document.createElement('button');
    btnNo.textContent = 'Cancel';
    btnNo.style.cssText = 'background:transparent;color:#aaa;border:1px solid #555;padding:4px 10px;border-radius:4px;cursor:pointer;';
    btnNo.addEventListener('click', function () {
      removePlaylistBanner();
      // Re-enable inputs so user can edit the URL
      urlInput.disabled = false;
      btnMagic.disabled = false;
    });

    playlistBanner.appendChild(msg);
    playlistBanner.appendChild(btnYes);
    playlistBanner.appendChild(btnNo);
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
    form.classList.remove('hidden');
    urlInput.disabled = false;
    btnMagic.disabled = false;
    urlInput.value = '';
    urlInput.focus();
    setProgress(0, 'preparing...');
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

    // Disable inputs while we decide what to do
    urlInput.disabled = true;
    btnMagic.disabled = true;
    errorArea.classList.add('hidden');
    resultArea.classList.add('hidden');
    removePlaylistBanner();

    if (isPlaylistOnly(url)) {
      // Show confirmation banner; actual submission happens on "Download all"
      showPlaylistBanner(() => submitDownload(url));
    } else {
      submitDownload(url);
    }
  });

  async function submitDownload(url) {
    progressArea.classList.remove('hidden');
    setProgress(5, 'sending...');

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
        showError(data.error || 'Unknown error');
        return;
      }

      // Backend always returns job_ids (array)
      jobIds = data.job_ids || (data.job_id ? [data.job_id] : []);
    } catch (err) {
      showError('Connection error. Are you connected?');
      return;
    }

    if (!jobIds.length) {
      showError('No jobs returned from server.');
      return;
    }

    if (jobIds.length === 1) {
      // Single track — existing progress bar UX
      setProgress(10, 'downloading...');
      await pollSingle(jobIds[0]);
    } else {
      // Playlist — counter UX
      await pollPlaylist(jobIds);
    }
  }

  // ── Single-track polling (original UX) ────────────────────────────────────

  async function pollSingle(jobId) {
    const POLL_MS  = 1500;
    const MAX_POLLS = 200;
    let polls = 0;

    return new Promise((resolve) => {
      const interval = setInterval(async () => {
        polls++;
        if (polls > MAX_POLLS) {
          clearInterval(interval);
          showError('Timed out. Please try again.');
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
          const label = pct < 20 ? 'analyzing...'
                      : pct < 60 ? 'downloading...'
                      : pct < 90 ? 'converting to mp3...'
                      : 'almost ready...';
          setProgress(Math.max(pct, 10), label);

        } else if (status === 'done') {
          clearInterval(interval);
          setProgress(100, 'done!');

          setTimeout(() => {
            progressArea.classList.add('hidden');
            resultArea.classList.remove('hidden');
            downloadLink.href = `/files/${jobId}`;
            const title = data.title || 'audio';
            downloadLabel.textContent = `↓ ${truncate(title, 40)}.mp3`;
          }, 400);
          resolve();

        } else if (status === 'error') {
          clearInterval(interval);
          showError(data.error_message || 'Error processing the video.');
          resolve();
        }
      }, POLL_MS);
    });
  }

  // ── Playlist polling — counter UX ─────────────────────────────────────────

  async function pollPlaylist(jobIds) {
    const total    = jobIds.length;
    const POLL_MS  = 2000;
    const MAX_WAIT = 600000; // 10 min hard cap
    const started  = Date.now();

    setProgress(5, `Downloading: 0 / ${total}`);

    // Poll all jobs until each is done or errored
    const statuses = Object.fromEntries(jobIds.map(id => [id, 'pending']));
    let done = 0;
    let errors = 0;

    await new Promise((resolve) => {
      const interval = setInterval(async () => {
        if (Date.now() - started > MAX_WAIT) {
          clearInterval(interval);
          showError('Timed out waiting for playlist. Some tracks may have downloaded.');
          resolve();
          return;
        }

        // Poll only jobs that are still pending/downloading
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
        setProgress(pct, `Downloading: ${done} / ${total}`);
      }, POLL_MS);
    });

    // All settled
    setProgress(100, 'done!');
    setTimeout(() => {
      progressArea.classList.add('hidden');
      resultArea.classList.remove('hidden');
      downloadLink.style.display = 'none';   // no single file to link
      const successCount = done - errors;
      downloadLabel.textContent = errors > 0
        ? `${successCount} / ${total} tracks downloaded (${errors} errors)`
        : `${total} tracks downloaded successfully`;
    }, 400);
  }

  function truncate(str, max) {
    return str.length > max ? str.slice(0, max) + '\u2026' : str;
  }
})();
