/* Local demo. Provider and API credentials never leave the server. */
const $ = (id) => document.getElementById(id);
let room = null, session = null, busy = false, ending = false, clock = null;
const messages = new Map();
let sending = false;
let signedInEmail = null, registerMode = false;
function account(email) {
  signedInEmail = email;
  $('account-label').textContent = email || 'Continue as a guest, or sign in';
  $('account-button').textContent = email ? 'Sign out' : 'Sign in';
}
function openLogin() { $('auth-error').hidden = true; $('auth-dialog').showModal(); }
$('auth-close').onclick = () => $('auth-dialog').close();
$('auth-switch').onclick = () => {
  registerMode = !registerMode;
  $('auth-title').textContent = registerMode ? 'Create your account' : 'Welcome back';
  $('auth-submit').textContent = registerMode ? 'Create account' : 'Sign in';
  $('auth-switch').textContent = registerMode ? 'Already registered? Sign in' : 'New here? Create an account';
  $('auth-password').autocomplete = registerMode ? 'new-password' : 'current-password';
  $('auth-error').hidden = true;
};
$('auth-form').onsubmit = async (event) => {
  event.preventDefault(); $('auth-submit').disabled = true;
  $('auth-error').hidden = true;
  try {
    const result = await request(registerMode ? '/auth/register' : '/auth/login', {
      method: 'POST', body: JSON.stringify({ email: $('auth-email').value, password: $('auth-password').value }),
    });
    account(result.email); $('auth-password').value = ''; $('auth-dialog').close(); panel(true);
  } catch (e) { $('auth-error').textContent = e.message; $('auth-error').hidden = false; }
  finally { $('auth-submit').disabled = false; }
};
$('account-button').onclick = async () => {
  if (!signedInEmail) return openLogin();
  try {
    if (room || session) await cleanup();
    await request('/auth/logout', { method: 'POST' }); account(null);
  } catch (e) { error(e.message); }
};
request('/auth/me').then((user) => account(user.email)).catch(() => account(null));
function message(id, speaker, text, final = true) {
  let entry = messages.get(id);
  if (entry?.final && !final) return;
  if (!entry) {
    const row = document.createElement('div');
    row.className = 'message ' + (speaker === 'You' ? 'mine' : 'assistant');
    const label = document.createElement('small'); label.textContent = speaker;
    const content = document.createElement('p');
    row.append(label, content); $('messages').append(row);
    entry = { row, content }; messages.set(id, entry);
  }
  entry.content.textContent = text;
  entry.final = final; entry.row.classList.toggle('interim', !final);
  $('chat-empty').hidden = true;
  $('messages').scrollTop = $('messages').scrollHeight;
}
function preview() {
  const enabled = !!room?.localParticipant.isCameraEnabled;
  $('camera-preview').hidden = !enabled;
  $('camera').textContent = enabled ? 'Camera off' : 'Camera on';
  $('camera').setAttribute('aria-pressed', String(enabled));
  if (!enabled) { $('self-video').srcObject = null; return; }
  const track = room.localParticipant.getTrackPublication(window.LivekitClient.Track.Source.Camera)?.track;
  if (track) track.attach($('self-video'));
}
function panel(open) {
  $('panel').hidden = !open;
  $('chathead').setAttribute('aria-expanded', String(open));
  if (open) $('minimize').focus();
}
$('chathead').onclick = () => panel($('panel').hidden);
$('open-hero').onclick = () => panel(true);
$('minimize').onclick = () => { panel(false); $('chathead').focus(); };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') panel(false); });
function status(text, hint) { $('status').textContent = text; if (hint) $('hint').textContent = hint; }
function error(message = '') { $('error').textContent = message; $('error').hidden = !message; }
function controls() {
  preview();
  const mic = !!room?.localParticipant.isMicrophoneEnabled;
  const share = !!room?.localParticipant.isScreenShareEnabled;
  $('mute').textContent = mic ? 'Mute mic' : 'Unmute mic';
  $('mute').setAttribute('aria-pressed', String(!mic));
  $('share').textContent = share ? 'Stop sharing' : 'Share screen';
  $('share').setAttribute('aria-pressed', String(share));
}
async function request(path, options = {}) {
  const response = await fetch(path, { ...options, headers: {
    'Content-Type': 'application/json', 'X-Demo-Client': 'voice-demo', ...options.headers,
  }});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}).`);
  }
  return response.status === 204 ? null : response.json();
}
async function cleanup() {
  if (ending) return;
  ending = true;
  $('end').disabled = true;
  const oldRoom = room;
  room = null;
  preview(); $('chat-input').disabled = true; $('send').disabled = true;
  clearInterval(clock);
  let cleanupError = null;
  try { if (oldRoom) await oldRoom.disconnect(); } catch (e) { cleanupError = e; }
  if (session) {
    try {
      await request(`/demo/sessions/${session.room_name}`, {
        method: 'DELETE', headers: { 'X-Session-Token': session.session_token },
      });
      session = null;
    } catch (e) { cleanupError = e; }
  }
  $('audio').replaceChildren(); $('video').replaceChildren();
  $('live-label').hidden = true; $('enable-audio').hidden = true;
  $('controls').hidden = true; $('start').hidden = !!session;
  $('start').disabled = false; $('end').hidden = !session;
  $('end').disabled = false; $('end').textContent = session ? 'Retry ending session' : 'End conversation';
  busy = false; ending = false;
  status('Conversation ended', 'Whenever you need a hand, Alex is a conversation away.');
  if (cleanupError) error('Disconnected locally. Session cleanup needs a retry: ' + cleanupError.message);
}
$('start').onclick = async () => {
  if (busy || room || session) return;
  error();
  if (!window.LivekitClient) return error('The voice library could not load. Check your internet connection and refresh.');
  if (!navigator.mediaDevices?.getUserMedia) return error('Microphone access requires localhost or HTTPS and a supported browser.');
  busy = true; $('start').disabled = true;
  status('Connecting…', 'Allow your microphone when your browser asks.');
  try {
    // Ask before creating a billable session; release this permission-check stream.
    const permission = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    permission.getTracks().forEach((track) => track.stop());
    session = await request('/demo/sessions', { method: 'POST', body: JSON.stringify({ participant_name: 'Demo guest' }) });
    const { Room, RoomEvent, Track } = window.LivekitClient;
    const activeRoom = new Room();
    room = activeRoom;
    messages.clear();
    $('messages').querySelectorAll('.message').forEach((node) => node.remove());
    $('chat-empty').hidden = false;
    // Segment IDs let final transcripts replace interim text instead of duplicating it.
    activeRoom.registerTextStreamHandler('lk.transcription', async (reader, participantInfo) => {
      const attributes = reader.info.attributes || {};
      const id = `${participantInfo.identity}:${attributes['lk.segment_id'] || reader.info.id}`;
      const userTrack = activeRoom.localParticipant.getTrackPublication(Track.Source.Microphone)?.trackSid;
      const isUser = participantInfo.identity === activeRoom.localParticipant.identity ||
        (userTrack && attributes['lk.transcribed_track_id'] === userTrack);
      const final = attributes['lk.transcription_final'] !== 'false';
      let text = '';
      try {
        for await (const chunk of reader) {
          if (room !== activeRoom) return;
          text += chunk;
          message(id, isUser ? 'You' : 'Alex', text, final);
        }
      } catch { if (room === activeRoom) error('A transcript was interrupted. Some text may be missing.'); }
    });
    activeRoom.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      if (room !== activeRoom) return;
      if (track.kind === Track.Kind.Audio) {
        const element = track.attach(); element.autoplay = true; $('audio').append(element);
      } else if (track.kind === Track.Kind.Video && (participant.isAgent || participant.attributes['lk.publish_on_behalf'])) {
        const element = track.attach(); element.autoplay = true; element.muted = true; element.playsInline = true;
        $('video').replaceChildren(element); $('live-label').hidden = false;
        status('Alex is here', 'Speak naturally. You can interrupt or share your screen at any time.');
      }
    });
    activeRoom.on(RoomEvent.TrackUnsubscribed, (track) => {
      track.detach().forEach((element) => element.remove());
      if (!$('video').children.length) $('live-label').hidden = true;
    });
    activeRoom.on(RoomEvent.AudioPlaybackStatusChanged, () => { $('enable-audio').hidden = activeRoom.canPlaybackAudio; });
    activeRoom.on(RoomEvent.Reconnecting, () => status('Reconnecting…', 'Your connection was interrupted. Trying again.'));
    activeRoom.on(RoomEvent.Reconnected, () => status('Connected', 'You can continue your conversation.'));
    activeRoom.on(RoomEvent.Disconnected, () => { if (room === activeRoom) void cleanup(); });
    for (const event of [RoomEvent.LocalTrackPublished, RoomEvent.LocalTrackUnpublished, RoomEvent.TrackMuted, RoomEvent.TrackUnmuted]) activeRoom.on(event, controls);
    await activeRoom.connect(session.livekit_url, session.participant_token);
    await activeRoom.localParticipant.setMicrophoneEnabled(true);
    try { await activeRoom.startAudio(); } catch { $('enable-audio').hidden = false; }
    if (room !== activeRoom) throw new Error('The session disconnected during setup. Please try again.');
    $('start').hidden = true; $('end').hidden = false; $('controls').hidden = false;
    $('chat-input').disabled = false; $('send').disabled = false;
    status('Connected', 'Waiting for Alex. Make sure your voice agent worker is running.');
    controls(); busy = false;
    const began = Date.now(); $('timer').textContent = '00:00';
    clock = setInterval(() => {
      const elapsed = Math.floor((Date.now() - began) / 1000);
      $('timer').textContent = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`;
    }, 1000);
  } catch (e) {
    await cleanup();
    error((e.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow it in your browser and try again.' : e.message) + (session ? ' Use Retry ending session before reconnecting.' : ''));
  }
};
$('end').onclick = () => { error(); void cleanup(); };
$('camera').onclick = async () => {
  const current = room;
  if (!current) return;
  $('camera').disabled = true; error();
  try { await current.localParticipant.setCameraEnabled(!current.localParticipant.isCameraEnabled); preview(); }
  catch (e) { error('Could not change camera: ' + e.message); }
  finally { $('camera').disabled = false; }
};
$('chat-form').onsubmit = async (event) => {
  event.preventDefault();
  const text = $('chat-input').value.trim();
  const current = room;
  if (!current || !text || sending || busy || ending) return;
  sending = true; $('send').disabled = true; error();
  const id = 'typed-' + crypto.randomUUID();
  message(id, 'You', text);
  $('chat-input').value = '';
  try { await current.localParticipant.sendText(text, { topic: 'lk.chat' }); }
  catch (e) {
    if (room === current) {
      messages.get(id)?.row.classList.add('failed');
      error('Message could not be sent. Please try again: ' + e.message);
      if (!$('chat-input').value) $('chat-input').value = text;
    }
  } finally { sending = false; $('send').disabled = !room || ending; }
};
$('mute').onclick = async () => {
  if (!room) return;
  $('mute').disabled = true; error();
  try { await room.localParticipant.setMicrophoneEnabled(!room.localParticipant.isMicrophoneEnabled); controls(); }
  catch (e) { error('Could not change microphone: ' + e.message); }
  finally { $('mute').disabled = false; }
};
$('share').onclick = async () => {
  if (!room) return;
  $('share').disabled = true; error();
  try {
    await room.localParticipant.setScreenShareEnabled(!room.localParticipant.isScreenShareEnabled, { audio: false });
    controls();
    if (room.localParticipant.isScreenShareEnabled) $('hint').textContent = 'Screen sharing is on. Ask Alex about what’s visible.';
  } catch (e) { error('Screen sharing did not start: ' + e.message); }
  finally { $('share').disabled = false; }
};
$('enable-audio').onclick = async () => {
  try { await room?.startAudio(); } catch (e) { error('Could not enable audio: ' + e.message); }
};
window.addEventListener('pagehide', () => {
  if (session) fetch(`/demo/sessions/${session.room_name}`, { method: 'DELETE', keepalive: true,
    headers: { 'X-Demo-Client': 'voice-demo', 'X-Session-Token': session.session_token } }).catch(() => {});
  void room?.disconnect();
});
