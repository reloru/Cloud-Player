/* Cloud Player - library browsing, playback queue and uploads. No dependencies. */
'use strict';

var PASSWORD_KEY = 'cloudplayer.password';
var REPEAT_MODES = ['off', 'all', 'one'];

/* Mirrors AUDIO_TYPES in vm-api/music_api.py. The file input carries no accept
   attribute (see index.html), so this is what keeps a stray PDF from being
   uploaded only to come back as a 400. */
var AUDIO_EXTENSIONS = [
  '.mp3', '.m4a', '.aac', '.flac', '.wav', '.aif', '.aiff',
  '.ogg', '.oga', '.opus', '.wma'
];

var el = {
  library: document.getElementById('library'),
  empty: document.getElementById('empty'),
  status: document.getElementById('status'),
  search: document.getElementById('search'),
  player: document.getElementById('player'),
  npArt: document.getElementById('np-art'),
  npTitle: document.getElementById('np-title'),
  npArtist: document.getElementById('np-artist'),
  btnPlay: document.getElementById('btn-play'),
  btnPrev: document.getElementById('btn-prev'),
  btnNext: document.getElementById('btn-next'),
  btnShuffle: document.getElementById('btn-shuffle'),
  btnRepeat: document.getElementById('btn-repeat'),
  repeatOne: document.getElementById('repeat-one'),
  iconPlay: document.getElementById('icon-play'),
  iconPause: document.getElementById('icon-pause'),
  seek: document.getElementById('seek'),
  timeCurrent: document.getElementById('time-current'),
  timeTotal: document.getElementById('time-total'),
  btnUpload: document.getElementById('btn-upload'),
  btnAuth: document.getElementById('btn-auth'),
  fileInput: document.getElementById('file-input'),
  sheet: document.getElementById('sheet'),
  sheetTitle: document.getElementById('sheet-title'),
  sheetBody: document.getElementById('sheet-body')
};

var audio = new Audio();
audio.preload = 'metadata';

var state = {
  songs: [],
  visible: [],
  order: [],
  pos: -1,
  currentId: null,
  shuffle: false,
  repeat: 'off',
  scrubbing: false,
  loading: false
};

/* -- credentials --------------------------------------------------------- */

function getPassword() {
  try {
    return localStorage.getItem(PASSWORD_KEY) || '';
  } catch (err) {
    return '';
  }
}

function setPassword(value) {
  try {
    if (value) {
      localStorage.setItem(PASSWORD_KEY, value);
    } else {
      localStorage.removeItem(PASSWORD_KEY);
    }
  } catch (err) {
    /* Private mode or blocked storage: the password lives for this page only. */
  }
  updateAuthButton();
}

function updateAuthButton() {
  var signedIn = getPassword() !== '';
  el.btnAuth.classList.toggle('on', signedIn);
  el.btnAuth.setAttribute('aria-label', signedIn ? 'Signed in' : 'Sign in');
}

/* -- api ----------------------------------------------------------------- */

function api(path, options) {
  options = options || {};
  var headers = new Headers(options.headers || {});
  if (options.auth) {
    headers.set('Authorization', 'Bearer ' + getPassword());
  }
  return fetch(path, {
    method: options.method || 'GET',
    headers: headers,
    body: options.body
  }).then(function (response) {
    return response.text().then(function (text) {
      var payload = null;
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch (err) {
          payload = null;
        }
      }
      if (!response.ok) {
        var error = new Error(
          (payload && payload.error) || 'request failed (' + response.status + ')'
        );
        error.status = response.status;
        throw error;
      }
      return payload;
    });
  });
}

/**
 * Run an action that needs the password, collecting it first if necessary and
 * re-collecting it if the Worker rejects what we have.
 */
function withAuth(purpose, action) {
  if (!getPassword()) {
    openLoginSheet(purpose, action);
    return;
  }
  action().catch(function (error) {
    if (error && error.status === 401) {
      setPassword('');
      openLoginSheet(purpose, action);
      return;
    }
    showStatus(error.message, true);
  });
}

/* -- status -------------------------------------------------------------- */

var statusTimer = null;

function showStatus(message, isError, sticky) {
  window.clearTimeout(statusTimer);
  el.status.textContent = message;
  el.status.className = 'status' + (isError ? ' error' : '');
  el.status.hidden = false;
  if (!sticky) {
    statusTimer = window.setTimeout(clearStatus, isError ? 6000 : 3000);
  }
}

function clearStatus() {
  window.clearTimeout(statusTimer);
  el.status.hidden = true;
  el.status.textContent = '';
  el.status.className = 'status';
}

/* -- helpers ------------------------------------------------------------- */

function formatTime(seconds) {
  if (!isFinite(seconds) || seconds < 0) {
    return '0:00';
  }
  var total = Math.floor(seconds);
  var hours = Math.floor(total / 3600);
  var minutes = Math.floor((total % 3600) / 60);
  var secs = total % 60;
  var pad = function (n) { return n < 10 ? '0' + n : String(n); };
  return hours > 0
    ? hours + ':' + pad(minutes) + ':' + pad(secs)
    : minutes + ':' + pad(secs);
}

function hasAudioExtension(filename) {
  var lower = String(filename || '').toLowerCase();
  for (var i = 0; i < AUDIO_EXTENSIONS.length; i++) {
    var ext = AUDIO_EXTENSIONS[i];
    if (lower.length > ext.length && lower.lastIndexOf(ext) === lower.length - ext.length) {
      return true;
    }
  }
  return false;
}

function songById(id) {
  for (var i = 0; i < state.songs.length; i++) {
    if (state.songs[i].id === id) {
      return state.songs[i];
    }
  }
  return null;
}

function placeholderTint(song) {
  var source = song.title || song.filename || '';
  var hash = 0;
  for (var i = 0; i < source.length; i++) {
    hash = (hash * 31 + source.charCodeAt(i)) | 0;
  }
  return 'hsl(' + (Math.abs(hash) % 360) + ' 24% 27%)';
}

function initialOf(song) {
  var source = (song.title || song.filename || '?').trim();
  return source ? source.charAt(0).toUpperCase() : '?';
}

function paintArt(node, song) {
  node.className = node.className.replace(/\s*has-image/, '');
  node.style.backgroundImage = '';
  node.style.backgroundColor = '';
  node.textContent = '';
  if (!song) {
    return;
  }
  if (song.cover) {
    node.className += ' has-image';
    node.style.backgroundImage = 'url("/api/cover/' + encodeURIComponent(song.id) + '")';
  } else {
    node.style.backgroundColor = placeholderTint(song);
    node.textContent = initialOf(song);
  }
}

function subtitleOf(song) {
  var bits = [];
  if (song.artist) {
    bits.push(song.artist);
  }
  if (song.album) {
    bits.push(song.album);
  }
  return bits.join(' — ') || 'Unknown artist';
}

/* -- library ------------------------------------------------------------- */

/** True while the "could not reach the library" banner is the visible status. */
var libraryBannerShown = false;

function loadLibrary() {
  state.loading = true;
  return api('/api/songs')
    .then(function (payload) {
      state.songs = (payload && payload.songs) || [];
      state.loading = false;
      renderLibrary();
      // Clear only the banner this function put up. An upload summary is also
      // styled as an error when something was skipped or failed, and a refresh
      // always follows an upload - clearing by CSS class wiped those before
      // they could be read.
      if (libraryBannerShown) {
        libraryBannerShown = false;
        clearStatus();
      }
    })
    .catch(function (error) {
      state.loading = false;
      state.songs = [];
      renderLibrary();
      libraryBannerShown = true;
      showStatus('Could not reach the library: ' + error.message, true, true);
    });
}

function renderLibrary() {
  var query = el.search.value.trim().toLowerCase();
  state.visible = state.songs.filter(function (song) {
    if (!query) {
      return true;
    }
    var haystack = [song.title, song.artist, song.album, song.filename]
      .join(' ')
      .toLowerCase();
    return haystack.indexOf(query) !== -1;
  });

  var list = document.createDocumentFragment();
  state.visible.forEach(function (song) {
    list.appendChild(buildTrackRow(song));
  });
  el.library.textContent = '';
  el.library.appendChild(list);

  if (state.visible.length === 0) {
    el.empty.hidden = false;
    if (state.songs.length === 0) {
      el.empty.textContent = state.loading
        ? 'Loading…'
        : 'No music yet. Tap the upload button to add tracks from Files.';
    } else {
      el.empty.textContent = 'Nothing matches “' + el.search.value.trim() + '”.';
    }
  } else {
    el.empty.hidden = true;
  }
  highlightPlaying();
}

function buildTrackRow(song) {
  var row = document.createElement('li');
  row.className = 'track';
  row.dataset.id = song.id;

  var main = document.createElement('button');
  main.type = 'button';
  main.className = 'track-main';

  var art = document.createElement('div');
  art.className = 'art';
  paintArt(art, song);
  main.appendChild(art);

  var text = document.createElement('div');
  text.className = 'track-text';
  var title = document.createElement('span');
  title.className = 'track-title';
  title.textContent = song.title || song.filename;
  var artist = document.createElement('span');
  artist.className = 'track-artist';
  artist.textContent = subtitleOf(song);
  text.appendChild(title);
  text.appendChild(artist);
  main.appendChild(text);

  main.addEventListener('click', function () {
    startFrom(song.id);
  });
  row.appendChild(main);

  var menu = document.createElement('button');
  menu.type = 'button';
  menu.className = 'icon-button';
  menu.setAttribute('aria-label', 'Options for ' + (song.title || song.filename));
  menu.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<circle cx="12" cy="5.5" r="1.4" class="fill"/>' +
    '<circle cx="12" cy="12" r="1.4" class="fill"/>' +
    '<circle cx="12" cy="18.5" r="1.4" class="fill"/></svg>';
  menu.addEventListener('click', function () {
    openTrackSheet(song);
  });
  row.appendChild(menu);

  return row;
}

function highlightPlaying() {
  var rows = el.library.querySelectorAll('.track');
  for (var i = 0; i < rows.length; i++) {
    rows[i].classList.toggle('playing', rows[i].dataset.id === state.currentId);
  }
}

/* -- playback ------------------------------------------------------------ */

function shuffled(items) {
  var copy = items.slice();
  for (var i = copy.length - 1; i > 0; i--) {
    var j = Math.floor(Math.random() * (i + 1));
    var swap = copy[i];
    copy[i] = copy[j];
    copy[j] = swap;
  }
  return copy;
}

/** Rebuild the play order from what is currently on screen, starting at startId. */
function buildOrder(startId) {
  var ids = state.visible.map(function (song) { return song.id; });
  if (state.shuffle) {
    var rest = shuffled(ids.filter(function (id) { return id !== startId; }));
    state.order = startId ? [startId].concat(rest) : rest;
    state.pos = startId ? 0 : -1;
  } else {
    state.order = ids;
    state.pos = ids.indexOf(startId);
  }
}

function startFrom(id) {
  buildOrder(id);
  playAt(state.pos);
}

function playAt(pos) {
  if (pos < 0 || pos >= state.order.length) {
    return;
  }
  var song = songById(state.order[pos]);
  if (!song) {
    return;
  }
  state.pos = pos;
  state.currentId = song.id;

  el.player.hidden = false;
  el.npTitle.textContent = song.title || song.filename;
  el.npArtist.textContent = subtitleOf(song);
  paintArt(el.npArt, song);
  el.seek.value = '0';
  el.seek.disabled = true;
  el.timeCurrent.textContent = '0:00';
  el.timeTotal.textContent = '0:00';
  highlightPlaying();
  updateMediaMetadata(song);

  audio.src = '/api/stream/' + encodeURIComponent(song.id);
  audio.play().catch(function (error) {
    showStatus('Playback failed: ' + error.message, true);
  });
}

function playNext(automatic) {
  if (automatic && state.repeat === 'one') {
    audio.currentTime = 0;
    audio.play().catch(function () { /* ignored: user can press play */ });
    return;
  }
  if (state.pos + 1 < state.order.length) {
    playAt(state.pos + 1);
    return;
  }
  if (state.repeat === 'all' && state.order.length > 0) {
    playAt(0);
    return;
  }
  if (automatic) {
    audio.pause();
    reflectPlayState();
  }
}

function playPrevious() {
  if (audio.currentTime > 3 || state.pos <= 0) {
    audio.currentTime = 0;
    return;
  }
  playAt(state.pos - 1);
}

function togglePlay() {
  if (!state.currentId) {
    if (state.visible.length > 0) {
      startFrom(state.visible[0].id);
    }
    return;
  }
  if (audio.paused) {
    audio.play().catch(function (error) {
      showStatus('Playback failed: ' + error.message, true);
    });
  } else {
    audio.pause();
  }
}

function reflectPlayState() {
  var playing = !audio.paused && !audio.ended;
  // These two are <svg>, and `hidden` is an IDL attribute of HTMLElement only:
  // assigning `.hidden` on an SVGElement sets a dead expando and leaves the
  // content attribute untouched. toggleAttribute works on any Element.
  el.iconPlay.toggleAttribute('hidden', playing);
  el.iconPause.toggleAttribute('hidden', !playing);
  el.btnPlay.setAttribute('aria-label', playing ? 'Pause' : 'Play');
  if ('mediaSession' in navigator) {
    navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
  }
}

/* -- media session ------------------------------------------------------- */

function updateMediaMetadata(song) {
  if (!('mediaSession' in navigator) || typeof window.MediaMetadata !== 'function') {
    return;
  }
  var artwork = [];
  if (song.cover) {
    artwork.push({ src: '/api/cover/' + encodeURIComponent(song.id) });
  } else {
    artwork.push({ src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' });
  }
  navigator.mediaSession.metadata = new window.MediaMetadata({
    title: song.title || song.filename,
    artist: song.artist || 'Unknown artist',
    album: song.album || '',
    artwork: artwork
  });
}

function updatePositionState() {
  if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) {
    return;
  }
  if (!isFinite(audio.duration) || audio.duration <= 0) {
    return;
  }
  try {
    navigator.mediaSession.setPositionState({
      duration: audio.duration,
      playbackRate: audio.playbackRate || 1,
      position: Math.min(audio.currentTime, audio.duration)
    });
  } catch (err) {
    /* Some engines reject position updates mid-seek; the UI slider still works. */
  }
}

function installMediaHandlers() {
  if (!('mediaSession' in navigator) || !navigator.mediaSession.setActionHandler) {
    return;
  }
  var handlers = {
    play: function () { audio.play().catch(function () {}); },
    pause: function () { audio.pause(); },
    previoustrack: playPrevious,
    nexttrack: function () { playNext(false); },
    seekbackward: function (details) {
      audio.currentTime = Math.max(0, audio.currentTime - ((details && details.seekOffset) || 10));
    },
    seekforward: function (details) {
      audio.currentTime = Math.min(
        audio.duration || 0,
        audio.currentTime + ((details && details.seekOffset) || 10)
      );
    },
    seekto: function (details) {
      if (details && typeof details.seekTime === 'number') {
        audio.currentTime = details.seekTime;
      }
    }
  };
  Object.keys(handlers).forEach(function (action) {
    try {
      navigator.mediaSession.setActionHandler(action, handlers[action]);
    } catch (err) {
      /* Unsupported action on this engine. */
    }
  });
}

/* -- sheets -------------------------------------------------------------- */

function openSheet(title, build) {
  el.sheetTitle.textContent = title;
  el.sheetBody.textContent = '';
  build(el.sheetBody);
  el.sheet.hidden = false;
}

function closeSheet() {
  el.sheet.hidden = true;
  el.sheetBody.textContent = '';
}

function sheetButton(label, className, onClick) {
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'sheet-btn' + (className ? ' ' + className : '');
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}

function labelledInput(labelText, value, type) {
  var label = document.createElement('label');
  label.textContent = labelText;
  var input = document.createElement('input');
  input.type = type || 'text';
  input.value = value || '';
  input.autocapitalize = 'none';
  input.autocomplete = type === 'password' ? 'current-password' : 'off';
  label.appendChild(input);
  return { label: label, input: input };
}

function openTrackSheet(song) {
  openSheet(song.title || song.filename, function (body) {
    var actions = document.createElement('div');
    actions.className = 'sheet-actions';

    actions.appendChild(sheetButton('Edit details', '', function () {
      openEditSheet(song);
    }));

    var download = document.createElement('a');
    download.className = 'sheet-btn';
    download.href = '/api/download/' + encodeURIComponent(song.id);
    download.setAttribute('download', song.filename);
    download.textContent = 'Download';
    download.addEventListener('click', function () {
      window.setTimeout(closeSheet, 150);
    });
    actions.appendChild(download);

    actions.appendChild(sheetButton('Delete', 'destructive', function () {
      openDeleteSheet(song);
    }));
    actions.appendChild(sheetButton('Cancel', '', closeSheet));
    body.appendChild(actions);
  });
}

function openEditSheet(song) {
  openSheet('Edit details', function (body) {
    var title = labelledInput('Title', song.title);
    var artist = labelledInput('Artist', song.artist);
    var album = labelledInput('Album', song.album);
    body.appendChild(title.label);
    body.appendChild(artist.label);
    body.appendChild(album.label);

    var note = document.createElement('p');
    note.className = 'sheet-note';
    note.textContent =
      'Saved to metadata.json next to your music. Clearing a field restores the ' +
      'value derived from the filename.';
    body.appendChild(note);

    var actions = document.createElement('div');
    actions.className = 'sheet-actions';
    actions.appendChild(sheetButton('Save', 'primary', function () {
      var payload = {
        title: title.input.value.trim(),
        artist: artist.input.value.trim(),
        album: album.input.value.trim()
      };
      closeSheet();
      withAuth('save details', function () {
        return api('/api/metadata/' + encodeURIComponent(song.id), {
          method: 'PUT',
          auth: true,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function () {
          showStatus('Details saved.');
          return loadLibrary();
        });
      });
    }));
    actions.appendChild(sheetButton('Cancel', '', closeSheet));
    body.appendChild(actions);
  });
}

function openDeleteSheet(song) {
  openSheet('Delete this track?', function (body) {
    var note = document.createElement('p');
    note.className = 'sheet-note';
    note.textContent =
      '“' + (song.title || song.filename) + '” will be removed from the ' +
      'VM permanently. This cannot be undone.';
    body.appendChild(note);

    var actions = document.createElement('div');
    actions.className = 'sheet-actions';
    actions.appendChild(sheetButton('Delete', 'destructive', function () {
      closeSheet();
      withAuth('delete a track', function () {
        return api('/api/delete/' + encodeURIComponent(song.id), {
          method: 'DELETE',
          auth: true
        }).then(function () {
          if (state.currentId === song.id) {
            audio.pause();
            state.currentId = null;
            audio.removeAttribute('src');
            audio.load();
            state.order = [];
            state.pos = -1;
            el.player.hidden = true;
          }
          showStatus('Deleted.');
          return loadLibrary();
        });
      });
    }));
    actions.appendChild(sheetButton('Keep it', '', closeSheet));
    body.appendChild(actions);
  });
}

function openLoginSheet(purpose, pending) {
  openSheet(getPassword() ? 'Signed in' : 'Sign in', function (body) {
    var note = document.createElement('p');
    note.className = 'sheet-note';
    note.textContent = purpose
      ? 'Enter the player password to ' + purpose + '. It is stored on this device only.'
      : 'The password is stored on this device and sent only when uploading, ' +
        'editing or deleting.';
    body.appendChild(note);

    var field = labelledInput('Password', '', 'password');
    body.appendChild(field.label);

    var actions = document.createElement('div');
    actions.className = 'sheet-actions';
    var save = sheetButton('Save', 'primary', function () {
      var candidate = field.input.value;
      if (!candidate) {
        return;
      }
      save.disabled = true;
      save.textContent = 'Checking…';
      fetch('/api/health', { headers: { Authorization: 'Bearer ' + candidate } })
        .then(function (response) {
          if (response.status === 401) {
            save.disabled = false;
            save.textContent = 'Save';
            showStatus('That password was rejected.', true);
            return;
          }
          setPassword(candidate);
          closeSheet();
          showStatus('Signed in.');
          if (pending) {
            pending().catch(function (error) {
              showStatus(error.message, true);
            });
          }
        })
        .catch(function (error) {
          save.disabled = false;
          save.textContent = 'Save';
          showStatus('Could not verify: ' + error.message, true);
        });
    });
    actions.appendChild(save);

    if (getPassword()) {
      actions.appendChild(sheetButton('Forget password', 'destructive', function () {
        setPassword('');
        closeSheet();
        showStatus('Password forgotten on this device.');
      }));
    }
    actions.appendChild(sheetButton('Cancel', '', closeSheet));
    body.appendChild(actions);

    window.setTimeout(function () { field.input.focus(); }, 50);
  });
}

/* -- uploads ------------------------------------------------------------- */

function uploadOne(file, onProgress) {
  return new Promise(function (resolve, reject) {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?name=' + encodeURIComponent(file.name));
    xhr.setRequestHeader('Authorization', 'Bearer ' + getPassword());
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.addEventListener('progress', function (event) {
      if (event.lengthComputable) {
        onProgress(event.loaded / event.total);
      }
    });
    xhr.addEventListener('load', function () {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
        return;
      }
      var message = 'upload failed (' + xhr.status + ')';
      try {
        var parsed = JSON.parse(xhr.responseText);
        if (parsed && parsed.error) {
          message = parsed.error;
        }
      } catch (err) {
        /* Non-JSON error body; the status code stands on its own. */
      }
      var error = new Error(message);
      error.status = xhr.status;
      reject(error);
    });
    xhr.addEventListener('error', function () {
      reject(new Error('network error during upload'));
    });
    xhr.addEventListener('abort', function () {
      reject(new Error('upload cancelled'));
    });
    xhr.send(file);
  });
}

function uploadAll(files, skipped) {
  var queue = Array.prototype.slice.call(files);
  var done = 0;
  var failures = [];
  skipped = skipped || 0;

  showStatus('Uploading 1 of ' + queue.length + '…', false, true);
  var bar = document.createElement('div');
  bar.className = 'progress';
  var fill = document.createElement('span');
  bar.appendChild(fill);
  el.status.appendChild(bar);

  function step() {
    if (queue.length === 0) {
      // Any "skipped" notice has to ride along with this final message: shown
      // on its own before the upload starts, the progress line overwrites it
      // a moment later and it is never read.
      var tail = skipped > 0
        ? ' Skipped ' + skipped + ' non-audio file' + (skipped === 1 ? '' : 's') + '.'
        : '';
      if (failures.length === 0) {
        showStatus('Added ' + done + (done === 1 ? ' track.' : ' tracks.') + tail,
                   skipped > 0);
      } else {
        showStatus(
          done + ' added, ' + failures.length + ' failed: ' + failures.join('; ') + tail,
          true
        );
      }
      return loadLibrary();
    }
    var file = queue.shift();
    var index = done + failures.length + 1;
    var total = index + queue.length;
    el.status.firstChild.textContent =
      'Uploading ' + index + ' of ' + total + ': ' + file.name;
    fill.style.width = '0%';
    return uploadOne(file, function (fraction) {
      fill.style.width = Math.round(fraction * 100) + '%';
    })
      .then(function () {
        done += 1;
        return step();
      })
      .catch(function (error) {
        if (error.status === 401) {
          throw error;
        }
        failures.push(file.name + ' (' + error.message + ')');
        return step();
      });
  }

  return step();
}

/* -- events -------------------------------------------------------------- */

el.search.addEventListener('input', renderLibrary);

el.btnPlay.addEventListener('click', togglePlay);
el.btnPrev.addEventListener('click', playPrevious);
el.btnNext.addEventListener('click', function () { playNext(false); });

el.btnShuffle.addEventListener('click', function () {
  state.shuffle = !state.shuffle;
  el.btnShuffle.setAttribute('aria-pressed', state.shuffle ? 'true' : 'false');
  if (state.currentId) {
    buildOrder(state.currentId);
  }
});

el.btnRepeat.addEventListener('click', function () {
  var next = (REPEAT_MODES.indexOf(state.repeat) + 1) % REPEAT_MODES.length;
  state.repeat = REPEAT_MODES[next];
  el.btnRepeat.classList.toggle('on', state.repeat !== 'off');
  el.repeatOne.hidden = state.repeat !== 'one';
  el.btnRepeat.setAttribute('aria-label', 'Repeat ' + state.repeat);
});

el.btnUpload.addEventListener('click', function () {
  // The picker must open on this gesture. Signing in involves an async check
  // that consumes it, so an unauthenticated tap collects the password and the
  // user taps again rather than the picker silently failing to open.
  if (!getPassword()) {
    openLoginSheet('add music', null);
    return;
  }
  el.fileInput.click();
});

el.btnAuth.addEventListener('click', function () { openLoginSheet('', null); });

el.fileInput.addEventListener('change', function () {
  var files = el.fileInput.files;
  if (!files || files.length === 0) {
    return;
  }
  var picked = Array.prototype.slice.call(files);
  el.fileInput.value = '';

  var chosen = picked.filter(function (file) {
    return hasAudioExtension(file.name);
  });
  var skipped = picked.length - chosen.length;
  if (chosen.length === 0) {
    showStatus(
      'Not an audio file. Supported: ' + AUDIO_EXTENSIONS.join(' ') + '.',
      true
    );
    return;
  }
  uploadAll(chosen, skipped).catch(function (error) {
    if (error && error.status === 401) {
      setPassword('');
      showStatus('Sign in again to upload.', true);
      openLoginSheet('add music', function () {
        return uploadAll(chosen, skipped);
      });
      return;
    }
    showStatus(error.message, true);
  });
});

el.sheet.addEventListener('click', function (event) {
  if (event.target === el.sheet) {
    closeSheet();
  }
});

document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape' && !el.sheet.hidden) {
    closeSheet();
  }
});

audio.addEventListener('play', reflectPlayState);
audio.addEventListener('pause', reflectPlayState);
audio.addEventListener('ended', function () { playNext(true); });

audio.addEventListener('loadedmetadata', function () {
  el.seek.disabled = !isFinite(audio.duration) || audio.duration <= 0;
  el.timeTotal.textContent = formatTime(audio.duration);
  updatePositionState();
});

audio.addEventListener('timeupdate', function () {
  if (state.scrubbing) {
    return;
  }
  el.timeCurrent.textContent = formatTime(audio.currentTime);
  if (isFinite(audio.duration) && audio.duration > 0) {
    el.seek.value = String(Math.round((audio.currentTime / audio.duration) * 1000));
  }
});

audio.addEventListener('durationchange', updatePositionState);
audio.addEventListener('seeked', updatePositionState);

audio.addEventListener('error', function () {
  // Tearing the source down after a delete also fires this; nothing to report.
  if (!state.currentId) {
    return;
  }
  var song = songById(state.currentId);
  showStatus(
    'Could not play ' + (song ? song.title || song.filename : 'this track') + '.',
    true
  );
});

el.seek.addEventListener('input', function () {
  state.scrubbing = true;
  if (isFinite(audio.duration) && audio.duration > 0) {
    el.timeCurrent.textContent =
      formatTime((Number(el.seek.value) / 1000) * audio.duration);
  }
});

el.seek.addEventListener('change', function () {
  state.scrubbing = false;
  if (isFinite(audio.duration) && audio.duration > 0) {
    audio.currentTime = (Number(el.seek.value) / 1000) * audio.duration;
  }
});

/* -- boot ---------------------------------------------------------------- */

updateAuthButton();
reflectPlayState();
installMediaHandlers();
loadLibrary();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {
      /* Offline shell is a nicety; the app works without it. */
    });
  });
}
