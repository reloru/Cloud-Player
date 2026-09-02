# Handoff: Cloud Player

The app is built and deployed. This file records how the Cloudflare side is
wired, the decisions behind it, and what is verified versus not.

```
iPhone --HTTPS--> Cloudflare Worker --(Workers VPC Service, over the "VM" tunnel)--> Ubuntu VM :8000 (music API, /music)
```

## State

- **Worker**: deployed at `https://cloud-player.reloru.workers.dev`.
- **`AUTH_PASSWORD`**: set via `wrangler secret put`, confirmed present with
  `wrangler secret list`.
- **VM API**: written, tested, **not yet running on the VM.** That is the one
  remaining step, and it is on the VM side only — see the root README.

Until `music_api.py` listens on `127.0.0.1:8000`, every `/api/*` call returns
`502 {"error":"music API unreachable", ...}`. That specific message means the
Worker and the tunnel are healthy and only the VM process is missing; a blank
500 would mean something else.

## Infrastructure — do not recreate

- **Tunnel**: named "VM", id `a2b9bc89-8a31-4406-8ad5-47d4923efd7b`.
- **VPC Service**: id `01a06084-afb3-7651-935c-59e35fa26e66`, name
  `music-api`, type `http`, target `localhost:8000` over that tunnel. Wired
  into `worker/wrangler.jsonc` as the `MUSIC_API` binding. In code:
  `env.MUSIC_API.fetch("http://localhost:8000/...")`.
- **Why VPC Services and not a public hostname**: all auth is enforced in the
  Worker, so a published DNS route on the tunnel would let anyone bypass the
  Worker and hit upload/delete on the VM directly. A VPC Service binding can
  only reach the one registered host:port, and needs no DNS record at all.
  Cost: Workers VPC is in beta per Cloudflare's docs, and creating the service
  needed the account's Connectivity Directory Admin role.

## Decisions

**Auth.** The client caches the password in `localStorage` and sends
`Authorization: Bearer <password>` on write requests only. The Worker hashes
both sides with SHA-256 and compares the 32-byte digests with
`crypto.subtle.timingSafeEqual` — that function throws on operands of
differing length, so hashing first is what keeps the comparison constant-time
and stops response timing from leaking the password's length. A missing
secret denies everything rather than allowing it. The client's `Authorization`
header is never forwarded to the VM.

**Language on the VM.** Python, standard library only. Verified against 3.11;
the code targets 3.8+ and exits with a message on anything older. The VM's
actual Python version was never checked from this environment — confirm with
`python3 --version`.

**No auth on the VM API itself.** It listens on loopback, so the tunnel is the
only route in, and anything that can already run code on the VM can write to
`/music` regardless. A second secret would not add a boundary that does not
already exist.

**Metadata: filename plus a sidecar.** Title and artist are derived from the
filename and overridable per track in `metadata.json`. Chosen over parsing
embedded tags, which would mean hand-writing an ID3v2/MP4/FLAC parser.
Consequence: **cover art embedded in audio files is not read.** Art comes from
image files on disk instead. Adding embedded-art support later means writing
that parser — it is not a configuration change.

**Uploads are raw bodies, not multipart.** `POST /api/upload?name=<encoded>`
with the file as the request body. Parsing `multipart/form-data` would mean
hand-rolling a multipart reader, since the stdlib `cgi` module was removed in
Python 3.13. The Worker pipes `request.body` through without buffering; the VM
streams it to `.incoming/<uuid>.part` and `os.replace`s it into place, so a
partial file never appears in the library. Both `Content-Length` and
`Transfer-Encoding: chunked` framings are handled, because Cloudflare may
re-frame the streamed body either way.

**Offline: app shell only.** The service worker never intercepts `/api/`.
A worker that answered a 206 range request from a cached 200 breaks seeking
and, on iOS, playback. Downloading a track is a plain HTTP attachment, which
is unrelated to Cache API storage; caching audio for offline *playback* is the
thing that was left out.

**`PUT /api/metadata/{id}`** is a fifth route beyond the original spec's four.
The sidecar needs a writer.

## Non-obvious things that bit during the build

Each of these was a real defect caught by running the thing, and each is
guarded by a test now.

- `hidden` is an IDL property of `HTMLElement`, **not** `SVGElement`. Setting
  `svg.hidden = true` writes a dead JS expando and leaves the content
  attribute alone, so the play/pause icons never swapped. Use
  `toggleAttribute('hidden', …)`. Reading `.hidden` back gives you the expando
  you just wrote, so a test that checks it will pass against broken code —
  assert on `hasAttribute('hidden')`.
- A class selector that sets `display` outranks the user agent's
  `[hidden] { display: none }` at equal specificity, so `.sheet-backdrop
  { display: flex }` left the modal backdrop permanently over the page,
  swallowing every tap. `[hidden] { display: none !important }` near the top of
  the stylesheet fixes the whole class of bug.
- The assets binding 307-redirects `/index.html` to `/`. Precaching a
  redirecting URL fails `cache.addAll`, which rejects the install step and
  leaves the app with no offline shell at all. Precache `/` only.
- The VPC Service does **not** reject when the origin is unreachable; it
  resolves with a bodiless 5xx, so a `try`/`catch` around the fetch never
  fires. `music_api.py` stamps `X-Music-API: 1` on every response and the
  Worker treats a 5xx without it as transport failure, which keeps the VM's own
  500s passing through untouched.
- `<a class="sheet-btn">` takes the user agent link colour unless the rule sets
  `color` explicitly.

## Verified

Run in this environment, not inferred:

- `vm-api/test_music_api.py` — 85 checks over a real socket: uploads in both
  framings, Range/206/416/suffix ranges, cover resolution, metadata
  overrides, deletion, path traversal, keep-alive.
- Worker end to end — 83 checks against the real `src/index.ts` running in
  `wrangler dev`, with the `MUSIC_API` binding pointed at a shim worker
  forwarding to a live `music_api.py`. Covers an 8 MiB streamed upload byte
  for byte, Range passthrough across all three hops, the auth gate, method
  and route rejection, and that the client's `Authorization` header does not
  reach the VM.
- PWA in Chromium at iPhone viewport — 43 checks: playback, queue order,
  shuffle/repeat, seeking, Media Session metadata and artwork, the login,
  edit and delete sheets, service worker registration and cache contents, and
  a clean console.
- `tsc --noEmit` clean; live deployment probed (assets 200, auth gate 401,
  `/api/*` 502 with the expected message).

## Not verified

- **Anything on the VM.** No session here can reach it. The VM's Python
  version, that `/music` exists and is writable, and the tunnel's live health
  are all unconfirmed from here.
- **Any behaviour on real iOS Safari.** The UI was driven in Chromium with an
  iPhone viewport, which is not the same engine. Specifically unverified:
  Add to Home Screen, lock screen media controls, and whether the download
  link lands in the Files app.
- The success path through the real VPC Service, since the VM API has never
  been running while the deployed Worker was probed. Only the failure path has
  been exercised end to end against Cloudflare.
