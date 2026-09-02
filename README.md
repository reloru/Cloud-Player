# Cloud Player

Personal music PWA. Your library lives on your own VM; nothing is exposed to
the internet except the Worker.

```
iPhone --HTTPS--> Cloudflare Worker --(Workers VPC Service, over the "VM" tunnel)--> Ubuntu VM :8000 --> /music
```

Deployed at **https://cloud-player.reloru.workers.dev**

- `worker/` — the Cloudflare Worker. Serves the PWA from the `assets` binding
  and proxies `/api/*` to the VM through the `MUSIC_API` VPC Service binding.
- `worker/public/` — the PWA itself. Plain HTML, CSS and JavaScript, no
  framework, no build step, no dependencies.
- `vm-api/` — the Python service that runs on the VM: filesystem access under
  `/music`, metadata, Range-request streaming, streaming uploads.
- `docs/HANDOFF.md` — how the Cloudflare side is wired and why.

## Getting it running

The Worker is already deployed and the `AUTH_PASSWORD` secret is set. The
remaining step is on the VM:

```
git clone https://github.com/reloru/Cloud-Player /opt/cloud-player
MUSIC_DIR=/music python3 /opt/cloud-player/vm-api/music_api.py
```

Then open the deployed URL. Until that process is listening on
`127.0.0.1:8000`, every `/api/*` call returns
`502 {"error":"music API unreachable"}` — that message means the Worker and
tunnel are fine and only the VM process is missing.

See `vm-api/README.md` for the systemd unit and configuration.

## Using it

Add to Home Screen in Safari to install it. Browsing and playback need no
password. Uploading, editing details and deleting ask for the password once
and keep it in `localStorage` on that device, sending it as
`Authorization: Bearer <password>` on those requests only.

Tap a track to play. The bottom bar has previous/play/next, a scrubber,
shuffle and repeat (off → all → one). Lock screen and Control Center
controls work through the Media Session API. The `⋯` menu on each row offers
edit, download and delete.

## Routes

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /api/songs` | — | The library. |
| `GET /api/stream/{id}` | — | Playback. Range requests end to end. |
| `GET /api/download/{id}` | — | Same bytes as an attachment. |
| `GET /api/cover/{id}` | — | Cover image, 404 when there is none. |
| `POST /api/upload?name=…` | yes | Raw body, streamed to disk. |
| `PUT /api/metadata/{id}` | yes | Override title/artist/album/cover. |
| `DELETE /api/delete/{id}` | yes | Remove a track. |
| `GET /api/health` | yes | Liveness and track count. |
| anything else | — | The PWA, from the assets binding. |

`PUT /api/metadata/{id}` is not in the original four-route spec. It exists
because titles come from a sidecar `metadata.json` that something has to be
able to write.

## Metadata and cover art

Title and artist are derived from the filename — `Nina Simone - Feeling
Good.mp3` becomes artist "Nina Simone", title "Feeling Good" — and any field
can be overridden per track in `metadata.json` in the music directory, which
"Edit details" writes.

Cover art is read from **files on disk**: the `cover` field in the sidecar, an
image sharing the track's basename (`Dreams.mp3` → `Dreams.jpg`), or
`cover.jpg`/`folder.jpg` in the music directory. Art **embedded inside the
audio files** is not extracted; that needs a hand-written ID3v2/MP4/FLAC
parser, which this build deliberately does not have. Tracks without art get a
generated initial-letter tile.

## Tests

```
python3 vm-api/test_music_api.py     # 85 checks, stdlib only, no network
cd worker && npm install && npx tsc --noEmit
```

## Deploying the Worker

```
cd worker
npm install
npx wrangler deploy
```

`npx wrangler dev` uses `remote: true` on the VPC binding, so local dev talks
to the real tunnel and needs the VM process running.
