/* Cloud Player service worker - caches the app shell only.
 *
 * /api/* is deliberately never intercepted. Audio is served with Range
 * requests, and a service worker that answered a 206 request from a cached
 * 200 response breaks seeking (and, on iOS, playback outright). Letting those
 * requests fall through to the network keeps range handling entirely between
 * the browser, the Worker and the VM.
 */
'use strict';

var CACHE = 'cloud-player-shell-v1';

// "/" only, never "/index.html": the Workers assets binding 307s /index.html
// to /, and a redirected response cannot be written to the Cache API, which
// would reject the whole install step and leave the app with no offline shell.
var SHELL = [
  '/',
  '/styles.css',
  '/app.js',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(SHELL);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.map(function (name) {
        return name === CACHE ? null : caches.delete(name);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', function (event) {
  var request = event.request;

  if (request.method !== 'GET') {
    return;
  }

  var url;
  try {
    url = new URL(request.url);
  } catch (err) {
    return;
  }

  if (url.origin !== self.location.origin || url.pathname.indexOf('/api/') === 0) {
    return;
  }

  // Navigations: prefer the network so a deploy is picked up immediately,
  // fall back to the cached shell when offline.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).then(function (response) {
        if (response && response.status === 200 && !response.redirected) {
          var copy = response.clone();
          caches.open(CACHE).then(function (cache) {
            cache.put('/', copy);
          });
        }
        return response;
      }).catch(function () {
        return caches.match('/').then(function (cached) {
          return cached || new Response(
            '<!DOCTYPE html><meta charset="utf-8"><title>Offline</title>' +
            '<body style="background:#0b0b0e;color:#f2f2f5;font:16px system-ui;' +
            'padding:2rem">Cloud Player is offline.</body>',
            { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          );
        });
      })
    );
    return;
  }

  // Everything else in the shell: serve from cache immediately and refresh it
  // in the background, so a deploy lands on the next load.
  event.respondWith(
    caches.match(request).then(function (cached) {
      var network = fetch(request).then(function (response) {
        if (response && response.status === 200 && response.type === 'basic'
            && !response.redirected) {
          var copy = response.clone();
          caches.open(CACHE).then(function (cache) {
            cache.put(request, copy);
          });
        }
        return response;
      }).catch(function () {
        return cached;
      });
      return cached || network;
    })
  );
});
