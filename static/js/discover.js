(() => {
  const host = document.getElementById('matches');
  const state = document.getElementById('state');
  const refreshButton = document.getElementById('refresh');
  let cache = [];

  function skeletons(count = 4) {
    host.innerHTML = Array.from({ length: count }, () => '<div class="skeleton" style="height:180px;margin-bottom:2px"></div>').join('');
  }

  function sharedLine(match) {
    if (match.shared_artist_count > 0) {
      const names = match.shared_artists.slice(0, 4).map(Resonance.escapeHtml).join(' · ');
      const extra = match.shared_artist_count > 4 ? ` +${match.shared_artist_count - 4}` : '';
      return `<b>${match.shared_artist_count} artiste${match.shared_artist_count > 1 ? 's' : ''} en commun</b><br>${names}${extra}`;
    }
    if (match.breakdown.genres > 0) return 'Aucun artiste commun, mais des genres qui se recoupent.';
    return 'Peu de recoupement pour l\u2019instant.';
  }

  function statusChip(match) {
    if (match.liked && match.likes_me) return '<span class="chip chip--wave">Like réciproque</span>';
    if (match.likes_me) return '<span class="chip chip--amber">T\u2019a liké</span>';
    if (match.liked) return '<span class="chip">Like envoyé</span>';
    return '';
  }

  function row(match) {
    const person = match.user;
    const meta = [person.age ? `${person.age} ans` : null, person.city || null, person.online ? 'en ligne' : null]
      .filter(Boolean).join(' — ') || `@${person.username}`;

    const tracks = (match.top_tracks || []).map((track) => `
      <div class="track">
        <img class="track__cover" src="${Resonance.escapeHtml(track.cover)}" alt="" loading="lazy">
        <span class="grow truncate">
          <span class="track__title truncate" style="display:block">${Resonance.escapeHtml(track.title)}</span>
          <span class="track__artist truncate" style="display:block">${Resonance.escapeHtml(track.artist_name)}</span>
        </span>
        <button class="play" data-preview="${Resonance.escapeHtml(track.preview || '')}" data-track-id="${track.track_id}" type="button"></button>
      </div>`).join('');

    return `
      <article class="match" data-user="${person.id}">
        <div class="match__main">
          <div class="row">
            <span class="avatar" data-seed="${Resonance.escapeHtml(person.avatar_seed || person.username)}" data-online="${person.online ? 1 : 0}">${Resonance.initials(person.display_name)}</span>
            <span class="grow truncate">
              <a class="match__name truncate" style="display:block" href="/profil/${encodeURIComponent(person.username)}">${Resonance.escapeHtml(person.display_name)}</a>
              <span class="label truncate" style="display:block">${Resonance.escapeHtml(meta)}</span>
            </span>
            ${statusChip(match)}
          </div>

          ${Resonance.spectrum(match.score, match.spectrum || [], { legend: person.display_name })}

          ${person.bio ? `<p class="match__bio">${Resonance.escapeHtml(person.bio)}</p>` : ''}
          <p class="match__shared">${sharedLine(match)}</p>

          <div class="match__actions">
            <button class="btn btn--quiet btn--sm" data-action="pass" type="button">Passer</button>
            <button class="btn btn--sm ${match.liked ? 'btn--quiet' : ''}" data-action="like" type="button" ${match.liked ? 'disabled' : ''}>${match.liked ? 'Like envoyé' : 'Liker'}</button>
            ${match.unlocked ? '<button class="btn btn--wave btn--sm" data-action="talk" type="button">Discuter</button>' : ''}
          </div>
        </div>

        <div class="match__side">
          <div class="bars">
            <div class="bar"><span>Genres</span><span class="bar__track"><span class="bar__fill" style="width:${match.breakdown.genres}%"></span></span><span class="bar__value">${match.breakdown.genres}</span></div>
            <div class="bar"><span>Artistes</span><span class="bar__track"><span class="bar__fill" style="width:${match.breakdown.artistes}%"></span></span><span class="bar__value">${match.breakdown.artistes}</span></div>
            <div class="bar"><span>Titres</span><span class="bar__track"><span class="bar__fill" style="width:${match.breakdown.morceaux}%"></span></span><span class="bar__value">${match.breakdown.morceaux}</span></div>
          </div>
          <div class="chips">${(match.genres || []).map((g) => `<span class="chip">${Resonance.escapeHtml(g)}</span>`).join('')}</div>
          ${tracks ? `<div>${tracks}</div>` : ''}
        </div>
      </article>`;
  }

  function render() {
    if (!cache.length) {
      host.innerHTML = '';
      state.innerHTML = `
        <div class="empty">
          <h3>Personne à afficher pour l'instant</h3>
          <p>Ajoute quelques titres à ton profil ou reviens plus tard : les scores se recalculent à chaque nouveau membre.</p>
          <a class="btn btn--ghost" href="/gouts/morceaux">Compléter mes goûts</a>
        </div>`;
      return;
    }
    state.innerHTML = '';
    host.innerHTML = cache.map(row).join('');
    Resonance.bindPlayButtons(host);
    Resonance.paintAvatars(host);
  }

  async function load() {
    skeletons();
    state.innerHTML = '';
    try {
      const result = await Resonance.api('/api/matches?limit=30');
      cache = result.matches;
      render();
    } catch (error) {
      host.innerHTML = '';
      state.innerHTML = `<div class="empty"><h3>Chargement impossible</h3><p>${Resonance.escapeHtml(error.message)}</p></div>`;
    }
  }

  host.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const article = button.closest('.match');
    const userId = Number(article.dataset.user);
    const action = button.dataset.action;
    button.disabled = true;

    try {
      if (action === 'pass') {
        await Resonance.api(`/api/pass/${userId}`, { method: 'POST', body: {} });
        cache = cache.filter((item) => item.user.id !== userId);
        article.style.transition = 'opacity .2s';
        article.style.opacity = '0';
        setTimeout(render, 200);
        return;
      }

      if (action === 'like') {
        const result = await Resonance.api(`/api/like/${userId}`, { method: 'POST', body: {} });
        const entry = cache.find((item) => item.user.id === userId);
        if (entry) {
          entry.liked = true;
          entry.unlocked = result.mutual;
        }
        Resonance.toast(
          result.mutual ? `Like réciproque — la discussion est ouverte (${result.score}%).` : 'Like envoyé. La discussion s\u2019ouvrira si le like est rendu.',
          'success',
        );
        render();
        return;
      }

      if (action === 'talk') {
        const result = await Resonance.api('/api/conversations', { method: 'POST', body: { user_id: userId } });
        window.location.href = result.redirect;
      }
    } catch (error) {
      Resonance.toast(error.message, 'error');
      button.disabled = false;
    }
  });

  refreshButton.addEventListener('click', load);

  if (window.io) {
    const socket = io({ transports: ['websocket', 'polling'] });
    socket.on('match:new', (data) => {
      Resonance.toast(`${data.name} t'a liké en retour — ${data.score}% de résonance.`, 'success', 7000);
      load();
    });
    socket.on('presence:update', (data) => {
      const avatar = host.querySelector(`.match[data-user="${data.user_id}"] .avatar`);
      if (avatar) avatar.dataset.online = data.online ? '1' : '0';
    });
  }

  load();
})();
