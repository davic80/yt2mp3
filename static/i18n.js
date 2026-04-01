/**
 * i18n.js — v4.1.0
 * Lightweight client-side translation module.
 * Exposes window.I18n with t(), setLang(), getLang().
 * Persists choice in localStorage. Defaults to browser language (en/es).
 * Fires custom event 'i18n:change' on document when language switches.
 */
window.I18n = (function () {
  'use strict';

  const STORAGE_KEY = 'yt2mp3_lang';

  const translations = {
    es: {
      // ── Topbar nav ──
      'nav.download':    '→ Descargar',
      'nav.mydownloads': '↓ Mis descargas',
      'nav.player':      '♪ Player',

      // ── Auth ──
      'auth.login':       'entrar',
      'auth.logout':      'salir',
      'auth.goto_player': 'ir al player',

      // ── Admin ──
      'nav.admin_panel':  'Admin Panel',

      // ── Player bar ──
      'player_bar.no_track': 'Sin reproducción',

      // ── Home fragment ──
      'home.tagline':         'pega, pulsa, descarga',
      'home.placeholder':     'https://youtube.com/watch?v=...',
      'home.btn_magic':       'magia',
      'home.preparing':       'preparando...',
      'home.download_mp3':    'descargar mp3',
      'home.convert_another': 'convertir otro',
      'home.retry':           'reintentar',
      'home.error_invalid':   'URL de YouTube no válida',

      // ── Playlist batch (v5.0.0) ──
      'pl.track_count':       '{n} pistas',
      'pl.download_all':      'Descargar todo',
      'pl.cancel':            'Cancelar',
      'pl.starting':          'Iniciando...',
      'pl.downloading_n':     'Descargando {done} / {total}...',
      'pl.details_show':      'Detalles ▼',
      'pl.details_hide':      'Detalles ▲',
      'pl.result_title':      '{n} pistas descargadas',
      'pl.result_failed':     '{n} fallidas',
      'pl.result_skipped':    '{n} omitidas',
      'pl.goto_playlist':     'Ir a la Playlist',
      'pl.download_zip':      '↓ ZIP',
      'pl.login_required':    'Inicia sesión para descargar playlists',
      'pl.error_batch_start': 'No se pudo iniciar la descarga',

      // ── Player fragment ──
      'player.all':            'Todas',
      'player.favorites':      '♥ Favoritos',
      'player.playlists_label':'Playlists',
      'player.new_playlist':   '+ Nueva playlist',
      'player.search':         'Buscar...',
      'player.col_fav':        '♥',
      'player.col_title':      'Título',
      'player.col_size':       'Tamaño',
      'player.col_date':       'Fecha',
      'player.loading':        'Cargando...',
      'player.empty':          'No hay canciones.',
      'player.fav_add':        'Agregar a favoritos',
      'player.fav_remove':     'Quitar de favoritos',
      'player.remove_from_pl': 'Quitar de esta playlist',
      'player.add_new_pl':     '+ Nueva playlist',
      'player.confirm_del_pl': '¿Eliminar esta playlist?',
      'player.prompt_pl_name': 'Nombre de la playlist:',
      'player.prompt_pl_name2':'Nombre de la nueva playlist:',
      'player.options':        'Opciones',

      // ── Player — share ──
      'player.share':          'Compartir',
      'player.share_title':    'Compartir playlist',
      'player.share_copy':     'Copiar enlace',
      'player.share_revoke':   'Revocar enlace',
      'player.share_close':    'Cerrar',
      'player.share_copied':   '¡Copiado!',
      'player.shared_view':    'Playlist compartida',
      'player.claim':          '+ Agregar',
      'player.claimed':        'Agregado ✓',
      'player.shared_invalid': 'Este enlace no está disponible.',
      'player.shared_add_pl':  'Agregar a mis playlists',
      'player.shared_added_pl':'Playlist creada ✓',

      // ── Player — collaborative ──
      'player.share_mode_view':        'Solo ver',
      'player.share_mode_collaborate': 'Colaborativa',
      'player.join_playlist':          'Unirme a esta lista',
      'player.joined_playlist':        'Te uniste ✓',
      'player.added_by':               'por {name}',
      'player.collaborative':          'colab',

      // ── Player — lyrics ──
      'player.lyrics':           'Letras',
      'player.lyrics_loading':   'Buscando letras...',
      'player.lyrics_not_found': 'Letra no encontrada.',
      'player.lyrics_synced':     'Letra · Sincronizada',
      'player.lyrics_not_synced': 'Letra · No Sincronizada',

      // ── Mis descargas fragment ──
      'md.search':          'Buscar...',
      'md.select_all':      'Seleccionar todo',
      'md.zip':             '↓ ZIP',
      'md.delete_sel':      '✕ Eliminar selección',
      'md.col_title':       'Título',
      'md.col_size':        'Tamaño',
      'md.col_date':        'Fecha',
      'md.col_actions':     'Acciones',
      'md.play':            'Reproducir',
      'md.rename':          '✎ renombrar',
      'md.download':        'Descargar',
      'md.delete':          'Eliminar',
      'md.loading':         'Cargando...',
      'md.empty':           'No hay canciones.',
      'md.error_load':      'Error cargando descargas',
      'md.error_rename':    'Error al renombrar',
      'md.renamed':         'Renombrado',
      'md.deleted':         'Eliminado',
      'md.confirm_del_one': '¿Eliminar esta canción?',
    },

    en: {
      // ── Topbar nav ──
      'nav.download':    '→ Download',
      'nav.mydownloads': '↓ My downloads',
      'nav.player':      '♪ Player',

      // ── Auth ──
      'auth.login':       'sign in',
      'auth.logout':      'sign out',
      'auth.goto_player': 'go to player',

      // ── Admin ──
      'nav.admin_panel':  'Admin Panel',

      // ── Player bar ──
      'player_bar.no_track': 'Nothing playing',

      // ── Home fragment ──
      'home.tagline':         'paste, press, download',
      'home.placeholder':     'https://youtube.com/watch?v=...',
      'home.btn_magic':       'go',
      'home.preparing':       'preparing...',
      'home.download_mp3':    'download mp3',
      'home.convert_another': 'convert another',
      'home.retry':           'try again',
      'home.error_invalid':   'Invalid YouTube URL',

      // ── Playlist batch (v5.0.0) ──
      'pl.track_count':       '{n} tracks',
      'pl.download_all':      'Download All',
      'pl.cancel':            'Cancel',
      'pl.starting':          'Starting...',
      'pl.downloading_n':     'Downloading {done} / {total}...',
      'pl.details_show':      'Details ▼',
      'pl.details_hide':      'Details ▲',
      'pl.result_title':      '{n} tracks downloaded',
      'pl.result_failed':     '{n} failed',
      'pl.result_skipped':    '{n} skipped',
      'pl.goto_playlist':     'Go to Playlist',
      'pl.download_zip':      '↓ ZIP',
      'pl.login_required':    'Sign in to download playlists',
      'pl.error_batch_start': 'Could not start batch download',

      // ── Player fragment ──
      'player.all':            'All',
      'player.favorites':      '♥ Favorites',
      'player.playlists_label':'Playlists',
      'player.new_playlist':   '+ New playlist',
      'player.search':         'Search...',
      'player.col_fav':        '♥',
      'player.col_title':      'Title',
      'player.col_size':       'Size',
      'player.col_date':       'Date',
      'player.loading':        'Loading...',
      'player.empty':          'No songs.',
      'player.fav_add':        'Add to favorites',
      'player.fav_remove':     'Remove from favorites',
      'player.remove_from_pl': 'Remove from playlist',
      'player.add_new_pl':     '+ New playlist',
      'player.confirm_del_pl': 'Delete this playlist?',
      'player.prompt_pl_name': 'Playlist name:',
      'player.prompt_pl_name2':'New playlist name:',
      'player.options':        'Options',

      // ── Player — share ──
      'player.share':          'Share',
      'player.share_title':    'Share playlist',
      'player.share_copy':     'Copy link',
      'player.share_revoke':   'Revoke link',
      'player.share_close':    'Close',
      'player.share_copied':   'Copied!',
      'player.shared_view':    'Shared playlist',
      'player.claim':          '+ Add',
      'player.claimed':        'Added ✓',
      'player.shared_invalid': 'This link is no longer available.',
      'player.shared_add_pl':  'Add to my playlists',
      'player.shared_added_pl':'Playlist created ✓',

      // ── Player — collaborative ──
      'player.share_mode_view':        'View only',
      'player.share_mode_collaborate': 'Collaborative',
      'player.join_playlist':          'Join this playlist',
      'player.joined_playlist':        'Joined ✓',
      'player.added_by':               'by {name}',
      'player.collaborative':          'collab',

      // ── Player — lyrics ──
      'player.lyrics':           'Lyrics',
      'player.lyrics_loading':   'Looking up lyrics...',
      'player.lyrics_not_found': 'Lyrics not found.',
      'player.lyrics_synced':     'Lyrics · Synced',
      'player.lyrics_not_synced': 'Lyrics · Not Synced',

      // ── Mis descargas fragment ──
      'md.search':          'Search...',
      'md.select_all':      'Select all',
      'md.zip':             '↓ ZIP',
      'md.delete_sel':      '✕ Delete selected',
      'md.col_title':       'Title',
      'md.col_size':        'Size',
      'md.col_date':        'Date',
      'md.col_actions':     'Actions',
      'md.play':            'Play',
      'md.rename':          '✎ rename',
      'md.download':        'Download',
      'md.delete':          'Delete',
      'md.loading':         'Loading...',
      'md.empty':           'No songs.',
      'md.error_load':      'Error loading downloads',
      'md.error_rename':    'Error renaming',
      'md.renamed':         'Renamed',
      'md.deleted':         'Deleted',
      'md.confirm_del_one': 'Delete this song?',
    },
  };

  // ── Language resolution ─────────────────────────────────────────────────────

  function _browserLang() {
    const nav = (navigator.language || navigator.userLanguage || 'es').toLowerCase();
    return nav.startsWith('en') ? 'en' : 'es';
  }

  function getLang() {
    return localStorage.getItem(STORAGE_KEY) || _browserLang();
  }

  function setLang(lang) {
    if (lang !== 'es' && lang !== 'en') return;
    localStorage.setItem(STORAGE_KEY, lang);
    _applyShell();
    document.dispatchEvent(new CustomEvent('i18n:change', { detail: { lang } }));
  }

  function t(key) {
    const lang = getLang();
    return (translations[lang] && translations[lang][key]) ||
           (translations['es'][key]) ||
           key;
  }

  // ── Shell strings (topbar + player bar) ────────────────────────────────────
  // These are in the persistent shell so they must be updated on lang change
  // rather than relying on fragment re-render.

  function _applyShell() {
    const lang = getLang();

    // Topbar nav links — update .link-full span only (.link-short is always hidden)
    const nav = {
      '/':              'nav.download',
      '/mis-descargas': 'nav.mydownloads',
      '/player':        'nav.player',
    };
    document.querySelectorAll('.topbar-link[data-path]').forEach(a => {
      const key = nav[a.dataset.path];
      if (!key) return;
      const full = a.querySelector('.link-full');
      if (full) full.textContent = t(key);
    });

    // Admin link
    const adminLink = document.getElementById('admin-link');
    if (adminLink) adminLink.textContent = t('nav.admin_panel');

    // Auth buttons
    const loginBtn = document.querySelector('#auth-loggedout .auth-btn');
    if (loginBtn) loginBtn.textContent = t('auth.login');
    const logoutBtn = document.querySelector('#auth-loggedin .auth-btn');
    if (logoutBtn) logoutBtn.textContent = t('auth.logout');
    const nameLink = document.getElementById('auth-name');
    if (nameLink) nameLink.title = t('auth.goto_player');

    // Player bar empty title (only update if still showing the placeholder)
    const titleEl = document.getElementById('player-title');
    if (titleEl && titleEl.classList.contains('player-title-empty')) {
      titleEl.textContent = t('player_bar.no_track');
    }

    // Lang toggle buttons active state
    document.querySelectorAll('.lang-btn[data-lang]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.lang === lang);
    });
  }

  // Apply on initial load (after DOM ready — called from shell.html inline script)
  function init() {
    _applyShell();
    document.dispatchEvent(new CustomEvent('i18n:change', { detail: { lang: getLang() } }));
  }

  return { t, getLang, setLang, init };
})();
