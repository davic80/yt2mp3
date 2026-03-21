(function () {
  'use strict';

  const form        = document.getElementById('form');
  const urlInput    = document.getElementById('url-input');
  const btnMagic    = document.getElementById('btn-magic');
  const progressArea = document.getElementById('progress-area');
  const progressFill = document.getElementById('progress-fill');
  const progressLabel = document.getElementById('progress-label');
  const resultArea  = document.getElementById('result-area');
  const downloadLink = document.getElementById('download-link');
  const downloadLabel = document.getElementById('download-label');
  const errorArea   = document.getElementById('error-area');
  const errorMsg    = document.getElementById('error-msg');

  document.getElementById('btn-reset').addEventListener('click', reset);
  document.getElementById('btn-reset-err').addEventListener('click', reset);

  function reset() {
    resultArea.classList.add('hidden');
    errorArea.classList.add('hidden');
    progressArea.classList.add('hidden');
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

  form.addEventListener('submit', async function (e) {
    e.preventDefault();

    const url = urlInput.value.trim();
    if (!url) {
      urlInput.focus();
      return;
    }

    // UI → loading state
    urlInput.disabled = true;
    btnMagic.disabled = true;
    errorArea.classList.add('hidden');
    resultArea.classList.add('hidden');
    progressArea.classList.remove('hidden');
    setProgress(5, 'enviando...');

    // Collect fingerprint gathered by inline script
    const fpData = window._fpData || {};

    let jobId;
    try {
      const resp = await fetch('/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: url,
          fingerprint: JSON.stringify(fpData),
        }),
      });

      const data = await resp.json();

      if (!resp.ok) {
        showError(data.error || 'Error desconocido');
        return;
      }

      jobId = data.job_id;
    } catch (err) {
      showError('Error de conexión. ¿Estás conectado?');
      return;
    }

    // Poll status
    setProgress(10, 'descargando...');
    await pollStatus(jobId);
  });

  async function pollStatus(jobId) {
    const POLL_MS = 1500;
    const MAX_POLLS = 200; // ~5 min timeout
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
        } catch (err) {
          // transient network error, keep polling
          return;
        }

        const pct = data.progress || 0;
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
            downloadLink.href = `/files/${jobId}`;
            const title = data.title || 'audio';
            downloadLabel.textContent = `↓ ${truncate(title, 40)}.mp3`;
          }, 400);
          resolve();

        } else if (status === 'error') {
          clearInterval(interval);
          showError(data.error_message || 'Error al procesar el vídeo.');
          resolve();
        }
      }, POLL_MS);
    });
  }

  function truncate(str, max) {
    return str.length > max ? str.slice(0, max) + '…' : str;
  }
})();
