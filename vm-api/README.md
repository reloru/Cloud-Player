# vm-api

The music API that runs on the Ubuntu VM behind the existing "VM" Cloudflare
Tunnel. Python standard library only — no packages to install.

`music_api.py` is the whole service. It must listen on **`127.0.0.1:8000`**:
that host and port are registered as the Workers VPC Service target
(`01a06084-afb3-7651-935c-59e35fa26e66`), so changing the port means
recreating the VPC Service.

## Running it

```
MUSIC_DIR=/music python3 music_api.py
```

Environment variables, all optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MUSIC_DIR` | `/music` | Where tracks live. Created if missing. |
| `MUSIC_API_HOST` | `127.0.0.1` | Bind address. Loopback keeps it off the network. |
| `MUSIC_API_PORT` | `8000` | Must stay `8000` in production. |
| `MAX_UPLOAD_BYTES` | `524288000` (500 MB) | Rejects larger uploads with 413. |

To run it permanently, edit and install `cloud-player-api.service`:

```
sudo cp cloud-player-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloud-player-api
journalctl -u cloud-player-api -f
```

The unit's `User=ubuntu` and `ExecStart` path are placeholders — set them to
whatever matches the box. Whichever user it runs as must be able to write to
`MUSIC_DIR`, otherwise the process refuses to start and says so.

## Routes

Paths match what the Worker exposes, so a URL that works against the deployed
Worker works against this process directly.

| Route | Notes |
| --- | --- |
| `GET /api/songs` | The library, sorted by artist then title. |
| `GET /api/stream/{id}` | Range-capable. `Accept-Ranges`, `Content-Range`, `ETag`, `Last-Modified`. |
| `GET /api/download/{id}` | Same bytes plus `Content-Disposition: attachment`. |
| `GET /api/cover/{id}` | Cover image, 404 when there isn't one. |
| `POST /api/upload?name=<urlencoded>` | Raw body written straight to disk. |
| `PUT /api/metadata/{id}` | JSON `{title, artist, album, cover}`. |
| `DELETE /api/delete/{id}` | Removes the file and its sidecar entry. |
| `GET /api/health` | Liveness and track count. |

There is **no authentication here.** It is enforced in the Worker, and this
process listens only on loopback, so the tunnel is the sole route in. Anything
that can already run code on the VM can write to the music directory anyway, so
a second secret would not add a boundary that does not already exist.

## Track ids

A track id is the unpadded base64url encoding of its filename. That is
URL-safe, stable across restarts, needs no index, and round-trips exactly.
Decoded names are rejected unless they name a file directly inside
`MUSIC_DIR`: no separators, no `..`, no leading dot, and the extension must be
one of the recognised audio types.

## Metadata

Title and artist are derived from the filename: drop the extension, then if
what remains contains `" - "`, the part before it is the artist and the part
after is the title. `Nina Simone - Feeling Good.mp3` becomes artist "Nina
Simone", title "Feeling Good". A name with no `" - "` becomes the title alone.

Any field can be overridden in `metadata.json` in the music directory, which
the API reads on every listing and rewrites on `PUT /api/metadata/{id}` (the
PWA's "Edit details"). Clearing a field removes the override and restores the
derived value. The file is written atomically, so an interrupted write cannot
truncate it.

```json
{
  "version": 1,
  "tracks": {
    "untitled sketch 04.wav": {
      "title": "Sketch in C",
      "artist": "Reed",
      "album": "Demos"
    }
  }
}
```

## Cover art

Resolved from files on disk, in this order:

1. the `cover` field in `metadata.json` for that track,
2. an image sharing the track's basename — `Dreams.mp3` → `Dreams.jpg`,
3. `cover.*`, `folder.*`, `front.*` or `album.*` in the music directory.

Recognised image types: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`.

**Art embedded inside the audio files themselves is not read.** Extracting it
would need a hand-written ID3v2/MP4/FLAC tag parser, which is a deliberate
non-goal here — the sidecar approach was chosen instead. Adding it later means
writing that parser; it is not a configuration change.

## Uploads

`POST /api/upload?name=<urlencoded filename>` with the file as the raw request
body. Not `multipart/form-data`: parsing that would mean hand-rolling a
multipart reader, since the stdlib `cgi` module was removed in Python 3.13.

The body is streamed to `MUSIC_DIR/.incoming/<uuid>.part` in 64 KB blocks and
then `os.replace`d into place, so a partial file never appears in the library
and nothing is buffered in memory. Both `Content-Length` and
`Transfer-Encoding: chunked` bodies are handled — Cloudflare may re-frame the
Worker's streamed body either way. Colliding names get a `" (2)"` suffix rather
than overwriting.

Accepted extensions: `.mp3 .m4a .aac .flac .wav .aif .aiff .ogg .oga .opus
.wma`. Anything else is rejected with 400.

## Tests

```
python3 test_music_api.py
```

Starts the API on a scratch port against a temporary directory and drives it
over a real socket — 81 checks covering uploads (both framings), Range
handling including 416 and suffix ranges, cover resolution, metadata
overrides, deletion, and path-traversal rejection. No network access, no
packages, nothing left behind.

## Requirements

Python 3.8 or newer; the process exits with a message if it is older. Verified
against 3.11. The Ubuntu 24.04 VM's exact Python version was not checked from
the session that wrote this — confirm with `python3 --version` before assuming.
