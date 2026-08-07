const Resonance = (() => {
  const toastHost = (() => {
    let host = document.querySelector('.toasts');
    if (!host) {
      host = document.createElement('div');
      host.className = 'toasts';
      document.body.appendChild(host);
    }
    return host;
  })();

  function toast(message, tone = 'neutral', ttl = 4200) {
    const node = document.createElement('div');
    node.className = `toast toast--${tone === 'error' ? 'bad' : tone === 'success' ? 'good' : 'neutral'}`;
    node.setAttribute('role', 'status');
    node.textContent = message;
    toastHost.appendChild(node);
    setTimeout(() => {
      node.style.opacity = '0';
      node.style.transform = 'translateY(8px)';
      node.style.transition = 'opacity .25s, transform .25s';
      setTimeout(() => node.remove(), 280);
    }, ttl);
  }

  async function api(url, options = {}) {
    const config = {
      method: options.method || 'GET',
      headers: { 'X-Requested-With': 'fetch' },
      credentials: 'same-origin',
    };
    if (options.body !== undefined) {
      config.headers['Content-Type'] = 'application/json';
      config.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch(url, config);
    } catch (error) {
      throw new Error('Connexion perdue. Vérifie ton réseau.');
    }
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }
    if (!response.ok || payload.ok === false) {
      const failure = new Error(payload.error || 'Une erreur est survenue.');
      failure.field = payload.field;
      failure.status = response.status;
      throw failure;
    }
    return payload;
  }

  const player = (() => {
    const audio = new Audio();
    audio.preload = 'none';
    audio.volume = 0.85;
    let currentId = null;
    const listeners = new Set();

    function broadcast() {
      listeners.forEach((fn) => fn(currentId, !audio.paused));
    }

    audio.addEventListener('ended', () => {
      currentId = null;
      broadcast();
    });
    audio.addEventListener('error', () => {
      if (currentId !== null) toast('Extrait indisponible pour ce morceau.', 'error');
      currentId = null;
      broadcast();
    });

    return {
      toggle(id, hasPreview) {
        if (!hasPreview) {
          toast('Aucun extrait disponible pour ce morceau.', 'error');
          return;
        }
        if (currentId === String(id) && !audio.paused) {
          audio.pause();
          currentId = null;
          broadcast();
          return;
        }
        currentId = String(id);
        // On passe toujours par le backend (plutôt que par l'URL Deezer
        // brute, qui expire) pour obtenir un lien d'aperçu frais à chaque
        // lecture.
        audio.src = `/api/preview/${encodeURIComponent(id)}`;
        audio.currentTime = 0;
        audio.play().then(broadcast).catch(() => {
          currentId = null;
          broadcast();
        });
      },
      stop() {
        audio.pause();
        currentId = null;
        broadcast();
      },
      onChange(fn) {
        listeners.add(fn);
        return () => listeners.delete(fn);
      },
      get current() {
        return currentId;
      },
    };
  })();

  const PLAY_ICON = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.2v13.6L19 12z"/></svg>';
  const EQ_ICON = '<span class="equaliser"><i></i><i></i><i></i></span>';

  function bindPlayButtons(scope = document) {
    scope.querySelectorAll('[data-preview]').forEach((button) => {
      if (button.dataset.bound === '1') return;
      button.dataset.bound = '1';
      button.innerHTML = PLAY_ICON;
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        player.toggle(button.dataset.trackId, Boolean(button.dataset.preview));
      });
    });
  }

  player.onChange((id, playing) => {
    document.querySelectorAll('[data-preview]').forEach((button) => {
      const active = playing && button.dataset.trackId === id;
      button.dataset.state = active ? 'playing' : 'idle';
      button.innerHTML = active ? EQ_ICON : PLAY_ICON;
      button.setAttribute('aria-label', active ? 'Arrêter l\u2019extrait' : 'Écouter l\u2019extrait');
    });
  });

  const PALETTE = ['#16161a', '#2438c8', '#ff4a1c', '#2b0b18', '#00a95c', '#ff48b0'];

  function tint(seed = '') {
    let sum = 0;
    for (let i = 0; i < seed.length; i += 1) sum += seed.charCodeAt(i);
    return PALETTE[sum % PALETTE.length];
  }

  function paintAvatars(scope = document) {
    scope.querySelectorAll('[data-seed]').forEach((node) => {
      node.style.background = tint(node.dataset.seed);
    });
  }

  function spectrum(score, bands = [], options = {}) {
    const value = Math.max(0, Math.min(100, Math.round(Number(score) || 0)));
    const caption = options.caption || 'de résonance';
    const showLabels = options.labels !== false;
    const width = 300;
    const reach = 30;
    const labelRoom = showLabels ? 15 : 4;
    const height = reach * 2 + labelRoom + 6;
    const axis = reach + 3;

    const columns = bands.length ? bands.slice(0, 9) : [];
    const slot = columns.length ? width / columns.length : width;
    const barWidth = Math.min(26, slot * 0.62);

    const shapes = columns.map((band, index) => {
      const x = index * slot + (slot - barWidth) / 2;
      const mine = band.mine * reach;
      const theirs = band.theirs * reach;
      const shared = band.shared * reach;
      const short = (band.label || '').slice(0, 7).toUpperCase();
      return `
        <rect class="meter__mine" x="${x.toFixed(1)}" y="${(axis - mine).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${mine.toFixed(1)}"/>
        <rect class="meter__theirs" x="${x.toFixed(1)}" y="${axis.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${theirs.toFixed(1)}"/>
        ${shared > 0.4 ? `<rect class="meter__shared" x="${x.toFixed(1)}" y="${(axis - shared).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${(shared * 2).toFixed(1)}"/>` : ''}
        ${showLabels ? `<text class="meter__tick" x="${(x + barWidth / 2).toFixed(1)}" y="${(axis + reach + 11).toFixed(1)}" text-anchor="middle">${escapeHtml(short)}</text>` : ''}`;
    }).join('');

    const plot = columns.length
      ? `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Genres partagés">
           ${shapes}
           <line class="meter__axis" x1="0" y1="${axis}" x2="${width}" y2="${axis}"/>
         </svg>`
      : `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Aucun genre partagé">
           <line class="meter__axis" x1="0" y1="${axis}" x2="${width}" y2="${axis}"/>
           <text class="meter__tick" x="${width / 2}" y="${axis - 6}" text-anchor="middle">AUCUN GENRE COMMUN</text>
         </svg>`;

    const legend = options.legend
      ? `<div class="meter__legend">
           <span><i style="background:var(--flame)"></i>Toi</span>
           <span><i style="background:var(--blue)"></i>${escapeHtml(options.legend)}</span>
           <span><i style="background:var(--over)"></i>Commun</span>
         </div>`
      : '';

    return `
      <div class="meter ${options.stacked ? 'meter--stacked' : ''}">
        <div class="meter__figure">
          <span class="meter__number">${value}<sup>%</sup></span>
          <span class="meter__caption">${escapeHtml(caption)}</span>
        </div>
        <div class="meter__plot">${plot}${legend}</div>
      </div>`;
  }

  function initials(name = '?') {
    return name.trim().slice(0, 2).toUpperCase();
  }

  function escapeHtml(value = '') {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function timeLabel(value) {
    if (!value) return '';
    const date = new Date(value.replace(' ', 'T') + (value.endsWith('Z') ? '' : 'Z'));
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindPlayButtons();
    paintAvatars();
  });

  return { api, toast, player, bindPlayButtons, paintAvatars, spectrum, tint, initials, escapeHtml, timeLabel };
})();
