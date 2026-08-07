(() => {
  const config = window.RESONANCE_CHAT;
  const socket = io({ transports: ['websocket', 'polling'] });

  const log = document.getElementById('log');
  const composer = document.getElementById('composer');
  const sendButton = document.getElementById('send');
  const typingLine = document.getElementById('typing');
  const presence = document.getElementById('presence');

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function appendMessage(message) {
    const empty = log.querySelector('.empty');
    if (empty) empty.remove();

    const bubble = document.createElement('div');
    if (message.kind === 'call') {
      bubble.className = 'bubble bubble--system';
      bubble.textContent = message.body;
    } else {
      bubble.className = `bubble ${message.sender_id === config.meId ? 'bubble--me' : ''}`;
      bubble.textContent = message.body;
      const time = document.createElement('span');
      time.className = 'bubble__time';
      time.textContent = Resonance.timeLabel(message.created_at);
      bubble.appendChild(time);
    }
    log.appendChild(bubble);
    scrollToEnd();
  }

  function send() {
    const body = composer.value.trim();
    if (!body) return;
    socket.emit('message:send', { conversation_id: config.conversationId, body, client_ref: Date.now() });
    composer.value = '';
    composer.style.height = 'auto';
    socket.emit('typing', { conversation_id: config.conversationId, state: false });
  }

  sendButton.addEventListener('click', send);

  composer.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  let typingTimer = null;
  composer.addEventListener('input', () => {
    composer.style.height = 'auto';
    composer.style.height = `${Math.min(130, composer.scrollHeight)}px`;
    socket.emit('typing', { conversation_id: config.conversationId, state: true });
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => socket.emit('typing', { conversation_id: config.conversationId, state: false }), 1600);
  });

  socket.on('connect', () => socket.emit('conversation:join', { conversation_id: config.conversationId }));
  socket.on('conversation:ready', (data) => {
    if (presence) presence.textContent = presence.textContent.replace(/^(en ligne|hors ligne)/, data.partner_online ? 'en ligne' : 'hors ligne');
  });
  socket.on('message:new', (message) => {
    if (message.conversation_id !== config.conversationId) return;
    appendMessage(message);
  });
  socket.on('typing', (data) => {
    if (data.conversation_id !== config.conversationId || data.user_id === config.meId) return;
    typingLine.textContent = data.state ? `${config.otherName} écrit…` : '';
  });
  socket.on('presence:update', (data) => {
    if (data.user_id !== config.otherId || !presence) return;
    presence.textContent = presence.textContent.replace(/^(en ligne|hors ligne)/, data.online ? 'en ligne' : 'hors ligne');
    document.querySelectorAll(`.avatar[data-seed]`).forEach((node) => node.dataset.online = data.online ? '1' : '0');
  });
  socket.on('error:toast', (data) => Resonance.toast(data.message, 'error'));

  const call = (() => {
    const overlay = document.getElementById('call-overlay');
    const statusLine = document.getElementById('call-status');
    const timerLine = document.getElementById('call-timer');
    const acceptButton = document.getElementById('call-accept');
    const hangupButton = document.getElementById('call-hangup');
    const muteButton = document.getElementById('call-mute');
    const videoWrap = document.getElementById('call-video-wrap');
    const localVideo = document.getElementById('local-video');
    const remoteVideo = document.getElementById('remote-video');
    const remoteAudio = document.getElementById('remote-audio');

    let peer = null;
    let localStream = null;
    let mode = 'audio';
    let role = null;
    let timer = null;
    let seconds = 0;
    let pendingSignals = [];

    function setStatus(text) {
      statusLine.textContent = text;
    }

    function show(visible) {
      overlay.classList.toggle('hidden', !visible);
      if (visible) Resonance.paintAvatars(overlay);
    }

    function startTimer() {
      seconds = 0;
      clearInterval(timer);
      timer = setInterval(() => {
        seconds += 1;
        const minutes = String(Math.floor(seconds / 60)).padStart(2, '0');
        const rest = String(seconds % 60).padStart(2, '0');
        timerLine.textContent = `${minutes}:${rest}`;
      }, 1000);
    }

    async function media(wantVideo) {
      return navigator.mediaDevices.getUserMedia({ audio: true, video: wantVideo });
    }

    function buildPeer() {
      const connection = new RTCPeerConnection({ iceServers: config.iceServers });
      connection.onicecandidate = (event) => {
        if (event.candidate) {
          socket.emit('call:signal', { conversation_id: config.conversationId, mode, signal: { type: 'candidate', candidate: event.candidate } });
        }
      };
      connection.ontrack = (event) => {
        const [stream] = event.streams;
        if (mode === 'video') {
          remoteVideo.srcObject = stream;
        } else {
          remoteAudio.srcObject = stream;
        }
        setStatus('En communication');
        startTimer();
      };
      connection.onconnectionstatechange = () => {
        if (['failed', 'disconnected'].includes(connection.connectionState)) {
          setStatus('Connexion interrompue');
          setTimeout(() => stop('failed'), 1200);
        }
      };
      localStream.getTracks().forEach((track) => connection.addTrack(track, localStream));
      return connection;
    }

    async function drainSignals() {
      for (const signal of pendingSignals) await handleSignal(signal);
      pendingSignals = [];
    }

    async function handleSignal(signal) {
      if (!peer) {
        pendingSignals.push(signal);
        return;
      }
      try {
        if (signal.type === 'offer') {
          await peer.setRemoteDescription(new RTCSessionDescription(signal.sdp));
          const answer = await peer.createAnswer();
          await peer.setLocalDescription(answer);
          socket.emit('call:signal', { conversation_id: config.conversationId, mode, signal: { type: 'answer', sdp: answer } });
        } else if (signal.type === 'answer') {
          await peer.setRemoteDescription(new RTCSessionDescription(signal.sdp));
        } else if (signal.type === 'candidate') {
          await peer.addIceCandidate(new RTCIceCandidate(signal.candidate));
        }
      } catch (error) {
        Resonance.toast('Échec de la négociation d\u2019appel.', 'error');
      }
    }

    async function start(wantMode) {
      mode = wantMode;
      role = 'caller';
      show(true);
      videoWrap.classList.toggle('hidden', mode !== 'video');
      acceptButton.classList.add('hidden');
      muteButton.classList.remove('hidden');
      setStatus('Appel en cours…');
      timerLine.textContent = '00:00';

      try {
        localStream = await media(mode === 'video');
      } catch (error) {
        Resonance.toast('Micro ou caméra inaccessible. Vérifie les autorisations du navigateur.', 'error');
        show(false);
        return;
      }
      if (mode === 'video') localVideo.srcObject = localStream;

      peer = buildPeer();
      socket.emit('call:invite', { conversation_id: config.conversationId, mode });
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      socket.emit('call:signal', { conversation_id: config.conversationId, mode, signal: { type: 'offer', sdp: offer } });
    }

    async function accept() {
      acceptButton.classList.add('hidden');
      muteButton.classList.remove('hidden');
      setStatus('Connexion…');
      try {
        localStream = await media(mode === 'video');
      } catch (error) {
        Resonance.toast('Micro ou caméra inaccessible.', 'error');
        stop('refused');
        return;
      }
      if (mode === 'video') {
        localVideo.srcObject = localStream;
        videoWrap.classList.remove('hidden');
      }
      peer = buildPeer();
      socket.emit('call:accept', { conversation_id: config.conversationId });
      await drainSignals();
    }

    function stop(status = 'ended', notify = true) {
      clearInterval(timer);
      if (notify) {
        socket.emit('call:end', { conversation_id: config.conversationId, mode, status, duration: seconds });
      }
      if (peer) {
        peer.close();
        peer = null;
      }
      if (localStream) {
        localStream.getTracks().forEach((track) => track.stop());
        localStream = null;
      }
      pendingSignals = [];
      remoteVideo.srcObject = null;
      remoteAudio.srcObject = null;
      localVideo.srcObject = null;
      seconds = 0;
      role = null;
      show(false);
    }

    function incoming(data) {
      mode = data.mode;
      role = 'callee';
      show(true);
      videoWrap.classList.add('hidden');
      acceptButton.classList.remove('hidden');
      muteButton.classList.add('hidden');
      setStatus(`Appel ${data.mode === 'video' ? 'vidéo' : 'audio'} entrant`);
      timerLine.textContent = '00:00';
    }

    acceptButton.addEventListener('click', accept);
    hangupButton.addEventListener('click', () => stop('ended'));

    muteButton.addEventListener('click', () => {
      if (!localStream) return;
      const track = localStream.getAudioTracks()[0];
      if (!track) return;
      track.enabled = !track.enabled;
      muteButton.textContent = track.enabled ? 'Couper le micro' : 'Réactiver le micro';
    });

    return { start, incoming, handleSignal, stop, get role() { return role; } };
  })();

  document.getElementById('call-audio').addEventListener('click', () => call.start('audio'));
  document.getElementById('call-video').addEventListener('click', () => call.start('video'));

  socket.on('call:incoming', (data) => {
    if (data.conversation_id !== config.conversationId) return;
    call.incoming(data);
  });
  socket.on('call:signal', (data) => {
    if (data.conversation_id !== config.conversationId) return;
    call.handleSignal(data.signal);
  });
  socket.on('call:accepted', () => Resonance.toast('Appel accepté.', 'success'));
  socket.on('call:unavailable', (data) => {
    Resonance.toast(data.reason, 'error');
    call.stop('missed', false);
  });
  socket.on('call:ended', () => {
    Resonance.toast('Appel terminé.', 'neutral');
    call.stop('ended', false);
  });

  window.addEventListener('beforeunload', () => socket.emit('conversation:leave', { conversation_id: config.conversationId }));

  scrollToEnd();
  composer.focus();
})();
