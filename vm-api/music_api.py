#!/usr/bin/env python3
"""Cloud Player - music API for the Ubuntu VM.

Serves the contents of a music directory over HTTP to the Cloud Player
Worker, which reaches it through a Workers VPC Service bound to the "VM"
Cloudflare Tunnel. Python standard library only - no third-party packages.

Binds 127.0.0.1:8000 by default. That host/port is registered as the VPC
Service target; changing it means recreating the VPC Service.

Routes (identical to the paths the Worker exposes, so a URL that works
against the deployed Worker also works against this process directly):

    GET    /api/songs             list the library
    GET    /api/stream/{id}       stream a track (Range supported)
    GET    /api/download/{id}     same bytes, as a file attachment
    GET    /api/cover/{id}        cover image for a track, if one exists
    POST   /api/upload?name=...   raw request body written to a new file
    PUT    /api/metadata/{id}     override title/artist/album/cover
    DELETE /api/delete/{id}       remove a track
    GET    /api/health            liveness plus track count

There is no authentication here. Access control lives in the Worker; this
process listens on the loopback interface only, so the tunnel is the sole
route to it. Anything that can already run code on the VM can also write to
the music directory directly, so an additional shared secret would not add
a boundary that does not already exist.

Track ids are the base64url encoding of the filename, unpadded. That keeps
ids URL-safe and stable without maintaining an index, and it round-trips
exactly for any filename the filesystem accepts.

Metadata: title, artist and album are derived from the filename, and any
field may be overridden per track in metadata.json inside the music
directory. The derivation is: strip the extension; if the remainder
contains " - ", the part before it is the artist and the part after is the
title; otherwise the whole remainder is the title and the artist is empty.

Cover art is resolved from files on disk, never from tags embedded in the
audio (reading those would require a binary tag parser). In order: the
"cover" override in metadata.json, then an image sharing the track's
basename, then cover.*/folder.* in the music directory.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import sys
import threading
import uuid
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterator, List, Optional, Tuple, Union
from urllib.parse import parse_qs, quote, unquote, urlsplit

MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")
HOST = os.environ.get("MUSIC_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("MUSIC_API_PORT", "8000"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))

CHUNK = 64 * 1024
MAX_LINE = 65536
MAX_FILENAME_BYTES = 200
MAX_FIELD_CHARS = 500
INCOMING_DIRNAME = ".incoming"
METADATA_FILENAME = "metadata.json"

AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".wma": "audio/x-ms-wma",
}

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

GENERIC_COVER_STEMS = ("cover", "folder", "front", "album")

_metadata_lock = threading.RLock()

# Sentinel returned by parse_range for a syntactically valid but
# unsatisfiable range, which must produce 416 rather than a normal body.
UNSATISFIABLE = object()


# --------------------------------------------------------------------------
# filenames and ids
# --------------------------------------------------------------------------

def is_safe_name(name: str) -> bool:
    """True if name addresses a file directly inside the music directory."""
    if not name or len(name.encode("utf-8", "surrogateescape")) > 255:
        return False
    if name in (".", ".."):
        return False
    if name.startswith("."):
        return False
    if "/" in name or "\\" in name or "\0" in name:
        return False
    return True


def encode_id(filename: str) -> str:
    raw = filename.encode("utf-8", "surrogateescape")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_id(song_id: str) -> Optional[str]:
    if not song_id or len(song_id) > 512:
        return None
    padded = song_id + "=" * (-len(song_id) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None
    try:
        name = raw.decode("utf-8", "surrogateescape")
    except UnicodeDecodeError:
        return None
    return name if is_safe_name(name) else None


def extension_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def sanitize_filename(raw: str) -> Optional[str]:
    """Reduce a client-supplied name to a safe filename, or None."""
    if not raw:
        return None
    name = raw.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    name = name.strip().lstrip(".").strip()
    if not name:
        return None
    ext = extension_of(name)
    if ext not in AUDIO_TYPES:
        return None
    stem = name[: len(name) - len(ext)]
    encoded = stem.encode("utf-8", "surrogateescape")
    if len(encoded) > MAX_FILENAME_BYTES:
        stem = encoded[:MAX_FILENAME_BYTES].decode("utf-8", "ignore").strip()
        if not stem:
            return None
    name = stem + ext
    return name if is_safe_name(name) else None


def unique_path(directory: str, filename: str) -> Tuple[str, str]:
    """Return (path, filename), suffixing " (n)" until nothing is clobbered."""
    ext = extension_of(filename)
    stem = filename[: len(filename) - len(ext)]
    candidate = filename
    counter = 2
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = "{} ({}){}".format(stem, counter, ext)
        counter += 1
    return os.path.join(directory, candidate), candidate


# --------------------------------------------------------------------------
# metadata sidecar
# --------------------------------------------------------------------------

def metadata_path() -> str:
    return os.path.join(MUSIC_DIR, METADATA_FILENAME)


def load_metadata() -> Dict[str, Dict[str, str]]:
    """Read metadata.json. A missing or unreadable file yields no overrides."""
    with _metadata_lock:
        try:
            with open(metadata_path(), "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log("metadata.json unreadable ({}), ignoring overrides".format(exc))
            return {}
    tracks = document.get("tracks") if isinstance(document, dict) else None
    if not isinstance(tracks, dict):
        return {}
    cleaned = {}
    for key, value in tracks.items():
        if isinstance(key, str) and isinstance(value, dict):
            cleaned[key] = {
                field: value[field]
                for field in ("title", "artist", "album", "cover")
                if isinstance(value.get(field), str)
            }
    return cleaned


def save_metadata(tracks: Dict[str, Dict[str, str]]) -> None:
    """Write metadata.json atomically so a crash cannot truncate it."""
    with _metadata_lock:
        document = {"version": 1, "tracks": tracks}
        temp = os.path.join(MUSIC_DIR, ".{}.tmp".format(uuid.uuid4().hex))
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, metadata_path())


def derive_fields(filename: str) -> Tuple[str, str]:
    """(title, artist) implied by the filename alone."""
    stem = filename[: len(filename) - len(extension_of(filename))].strip()
    if " - " in stem:
        artist, _, title = stem.partition(" - ")
        artist, title = artist.strip(), title.strip()
        if artist and title:
            return title, artist
    return stem or filename, ""


# --------------------------------------------------------------------------
# cover art
# --------------------------------------------------------------------------

def resolve_cover(filename: str, override: Optional[str]) -> Optional[str]:
    """Absolute path of the cover image for a track, or None."""
    if override and is_safe_name(override) and extension_of(override) in IMAGE_TYPES:
        candidate = os.path.join(MUSIC_DIR, override)
        if os.path.isfile(candidate):
            return candidate
    stem = filename[: len(filename) - len(extension_of(filename))]
    for ext in IMAGE_TYPES:
        candidate = os.path.join(MUSIC_DIR, stem + ext)
        if os.path.isfile(candidate):
            return candidate
    for generic in GENERIC_COVER_STEMS:
        for ext in IMAGE_TYPES:
            candidate = os.path.join(MUSIC_DIR, generic + ext)
            if os.path.isfile(candidate):
                return candidate
    return None


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------

def describe(filename: str, stat: os.stat_result,
             overrides: Dict[str, Dict[str, str]]) -> Dict[str, object]:
    override = overrides.get(filename, {})
    title, artist = derive_fields(filename)
    cover = resolve_cover(filename, override.get("cover"))
    return {
        "id": encode_id(filename),
        "filename": filename,
        "title": override.get("title") or title,
        "artist": override.get("artist", artist),
        "album": override.get("album", ""),
        "contentType": AUDIO_TYPES.get(extension_of(filename), "application/octet-stream"),
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "cover": cover is not None,
    }


def list_songs() -> List[Dict[str, object]]:
    overrides = load_metadata()
    songs = []
    try:
        entries = list(os.scandir(MUSIC_DIR))
    except OSError as exc:
        log("cannot read {}: {}".format(MUSIC_DIR, exc))
        return []
    for entry in entries:
        name = entry.name
        if not is_safe_name(name) or extension_of(name) not in AUDIO_TYPES:
            continue
        try:
            if not entry.is_file():
                continue
            songs.append(describe(name, entry.stat(), overrides))
        except OSError:
            continue
    songs.sort(key=lambda song: (
        str(song["artist"]).lower(),
        str(song["title"]).lower(),
        str(song["filename"]).lower(),
    ))
    return songs


# --------------------------------------------------------------------------
# range requests
# --------------------------------------------------------------------------

def parse_range(header: Optional[str], size: int) -> Union[None, object, Tuple[int, int]]:
    """Parse a Range header into inclusive (start, end).

    Returns None when the whole entity should be sent (no header, an
    unparseable header, or a multi-range request, all of which RFC 9110
    permits a server to answer with 200), or UNSATISFIABLE for a valid
    range that falls outside the entity.
    """
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None
    spec = header[6:].strip()
    if "," in spec:
        return None
    start_text, sep, end_text = spec.partition("-")
    if not sep:
        return None
    start_text, end_text = start_text.strip(), end_text.strip()
    try:
        if not start_text:
            if not end_text:
                return None
            suffix = int(end_text)
            if suffix <= 0:
                return UNSATISFIABLE
            if size == 0:
                return UNSATISFIABLE
            start = max(0, size - suffix)
            return start, size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or end < start or start >= size:
        return UNSATISFIABLE
    return start, min(end, size - 1)


# --------------------------------------------------------------------------
# request handling
# --------------------------------------------------------------------------

def log(message: str) -> None:
    sys.stdout.write("[music-api] {}\n".format(message))
    sys.stdout.flush()


class BodyTooLarge(Exception):
    pass


class BadChunkedBody(Exception):
    pass


class MusicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "cloud-player-music-api/1.0"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log("{} {}".format(self.address_string(), format % args))

    def end_headers(self) -> None:
        # Stamped on every response so the Worker can tell a 5xx that this
        # process produced from one invented by the tunnel when this process is
        # unreachable. Those two need very different messages in the app.
        self.send_header("X-Music-API", "1")
        super().end_headers()

    def send_json(self, status: int, payload: Dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json_error(self, status: int, message: str) -> None:
        self.send_json(status, {"error": message})

    def route(self) -> Tuple[str, List[str], Dict[str, List[str]]]:
        parts = urlsplit(self.path)
        segments = [unquote(segment) for segment in parts.path.split("/") if segment]
        return parts.path, segments, parse_qs(parts.query)

    # -- verbs ------------------------------------------------------------

    def do_GET(self) -> None:
        self.handle_read(send_body=True)

    def do_HEAD(self) -> None:
        self.handle_read(send_body=False)

    def handle_read(self, send_body: bool) -> None:
        _, segments, _ = self.route()
        if segments == ["api", "health"]:
            self.send_json(200, {
                "ok": True,
                "musicDir": MUSIC_DIR,
                "tracks": len(list_songs()),
            })
            return
        if segments == ["api", "songs"]:
            self.send_json(200, {"songs": list_songs()})
            return
        if len(segments) == 3 and segments[0] == "api":
            kind, song_id = segments[1], segments[2]
            if kind in ("stream", "download"):
                self.serve_track(song_id, as_attachment=(kind == "download"),
                                 send_body=send_body)
                return
            if kind == "cover":
                self.serve_cover(song_id, send_body=send_body)
                return
        self.send_json_error(404, "not found")

    def do_POST(self) -> None:
        _, segments, query = self.route()
        if segments == ["api", "upload"]:
            self.handle_upload(query)
            return
        self.discard_body()
        self.send_json_error(404, "not found")

    def do_PUT(self) -> None:
        _, segments, _ = self.route()
        if len(segments) == 3 and segments[:2] == ["api", "metadata"]:
            self.handle_metadata(segments[2])
            return
        self.discard_body()
        self.send_json_error(404, "not found")

    def do_DELETE(self) -> None:
        _, segments, _ = self.route()
        if len(segments) == 3 and segments[:2] == ["api", "delete"]:
            self.handle_delete(segments[2])
            return
        self.send_json_error(404, "not found")

    # -- reading ----------------------------------------------------------

    def track_path(self, song_id: str) -> Optional[Tuple[str, str]]:
        filename = decode_id(song_id)
        if not filename or extension_of(filename) not in AUDIO_TYPES:
            return None
        path = os.path.join(MUSIC_DIR, filename)
        if not os.path.isfile(path):
            return None
        return path, filename

    def serve_track(self, song_id: str, as_attachment: bool, send_body: bool) -> None:
        resolved = self.track_path(song_id)
        if resolved is None:
            self.send_json_error(404, "track not found")
            return
        path, filename = resolved
        content_type = AUDIO_TYPES.get(extension_of(filename), "application/octet-stream")
        disposition = None
        if as_attachment:
            disposition = "attachment; filename*=UTF-8''{}".format(
                quote(filename, safe=""))
        self.serve_file(path, content_type, send_body, disposition,
                        cache_control="private, max-age=0, must-revalidate")

    def serve_cover(self, song_id: str, send_body: bool) -> None:
        filename = decode_id(song_id)
        if not filename:
            self.send_json_error(404, "track not found")
            return
        override = load_metadata().get(filename, {}).get("cover")
        path = resolve_cover(filename, override)
        if path is None:
            self.send_json_error(404, "no cover")
            return
        content_type = IMAGE_TYPES.get(extension_of(path), "application/octet-stream")
        self.serve_file(path, content_type, send_body, None,
                        cache_control="private, max-age=300")

    def serve_file(self, path: str, content_type: str, send_body: bool,
                   disposition: Optional[str], cache_control: str) -> None:
        try:
            stat = os.stat(path)
            handle = open(path, "rb")
        except OSError as exc:
            log("cannot open {}: {}".format(path, exc))
            self.send_json_error(404, "not found")
            return

        with handle:
            size = stat.st_size
            requested = parse_range(self.headers.get("Range"), size)
            if requested is UNSATISFIABLE:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */{}".format(size))
                self.send_header("Content-Length", "0")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return

            if requested is None:
                start, end = 0, size - 1
                status = 200
            else:
                start, end = requested  # type: ignore[misc]
                status = 206

            length = 0 if size == 0 else end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
            self.send_header("ETag", '"{:x}-{:x}"'.format(int(stat.st_mtime), size))
            self.send_header("Cache-Control", cache_control)
            if disposition:
                self.send_header("Content-Disposition", disposition)
            if status == 206:
                self.send_header("Content-Range",
                                 "bytes {}-{}/{}".format(start, end, size))
            self.end_headers()

            if not send_body or length == 0:
                return
            try:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    block = handle.read(min(CHUNK, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                # Routine: audio elements abort ranges constantly while seeking.
                self.close_connection = True

    # -- writing ----------------------------------------------------------

    def iter_body(self) -> Iterator[bytes]:
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            yield from self.iter_chunked()
            return
        remaining = int(self.headers.get("Content-Length") or 0)
        if remaining > MAX_UPLOAD_BYTES:
            raise BodyTooLarge()
        while remaining > 0:
            block = self.rfile.read(min(CHUNK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block

    def iter_chunked(self) -> Iterator[bytes]:
        while True:
            line = self.rfile.readline(MAX_LINE)
            if not line:
                raise BadChunkedBody("truncated before chunk size")
            header = line.split(b";", 1)[0].strip()
            try:
                size = int(header, 16)
            except ValueError:
                raise BadChunkedBody("bad chunk size {!r}".format(header[:32]))
            if size == 0:
                while True:
                    trailer = self.rfile.readline(MAX_LINE)
                    if not trailer or trailer in (b"\r\n", b"\n"):
                        return
            remaining = size
            while remaining > 0:
                block = self.rfile.read(min(CHUNK, remaining))
                if not block:
                    raise BadChunkedBody("truncated inside chunk")
                remaining -= len(block)
                yield block
            self.rfile.read(2)

    def discard_body(self) -> None:
        try:
            for _ in self.iter_body():
                pass
        except (BodyTooLarge, BadChunkedBody, OSError):
            self.close_connection = True

    def handle_upload(self, query: Dict[str, List[str]]) -> None:
        raw_name = ""
        if query.get("name"):
            raw_name = query["name"][0]
        elif self.headers.get("X-Filename"):
            raw_name = unquote(self.headers["X-Filename"])
        filename = sanitize_filename(raw_name)
        if filename is None:
            self.discard_body()
            self.send_json_error(400, "missing or unsupported filename; allowed "
                                      "extensions: " + ", ".join(sorted(AUDIO_TYPES)))
            return

        incoming = os.path.join(MUSIC_DIR, INCOMING_DIRNAME)
        try:
            os.makedirs(incoming, exist_ok=True)
        except OSError as exc:
            self.discard_body()
            self.send_json_error(500, "cannot create staging directory: {}".format(exc))
            return

        staged = os.path.join(incoming, "{}.part".format(uuid.uuid4().hex))
        written = 0
        try:
            with open(staged, "wb") as handle:
                for block in self.iter_body():
                    written += len(block)
                    if written > MAX_UPLOAD_BYTES:
                        raise BodyTooLarge()
                    handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
        except BodyTooLarge:
            self.remove_quietly(staged)
            self.close_connection = True
            self.send_json_error(413, "upload exceeds {} bytes".format(MAX_UPLOAD_BYTES))
            return
        except BadChunkedBody as exc:
            self.remove_quietly(staged)
            self.close_connection = True
            self.send_json_error(400, "malformed chunked body: {}".format(exc))
            return
        except OSError as exc:
            self.remove_quietly(staged)
            self.send_json_error(500, "write failed: {}".format(exc))
            return

        if written == 0:
            self.remove_quietly(staged)
            self.send_json_error(400, "empty upload")
            return

        with _metadata_lock:
            final, stored_name = unique_path(MUSIC_DIR, filename)
            try:
                os.replace(staged, final)
            except OSError as exc:
                self.remove_quietly(staged)
                self.send_json_error(500, "cannot store file: {}".format(exc))
                return

        log("stored {} ({} bytes)".format(stored_name, written))
        try:
            stat = os.stat(final)
        except OSError as exc:
            self.send_json_error(500, "stored but unreadable: {}".format(exc))
            return
        self.send_json(201, {"song": describe(stored_name, stat, load_metadata())})

    def handle_metadata(self, song_id: str) -> None:
        resolved = self.track_path(song_id)
        if resolved is None:
            self.discard_body()
            self.send_json_error(404, "track not found")
            return
        _, filename = resolved

        try:
            raw = b"".join(self.iter_body())
        except (BodyTooLarge, BadChunkedBody) as exc:
            self.close_connection = True
            self.send_json_error(400, "bad body: {}".format(exc))
            return
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self.send_json_error(400, "body must be JSON")
            return
        if not isinstance(payload, dict):
            self.send_json_error(400, "body must be a JSON object")
            return

        with _metadata_lock:
            tracks = load_metadata()
            entry = dict(tracks.get(filename, {}))
            for field in ("title", "artist", "album", "cover"):
                if field not in payload:
                    continue
                value = payload[field]
                if value is None or value == "":
                    entry.pop(field, None)
                    continue
                if not isinstance(value, str) or len(value) > MAX_FIELD_CHARS:
                    self.send_json_error(
                        400, "{} must be a string of at most {} characters".format(
                            field, MAX_FIELD_CHARS))
                    return
                entry[field] = value.strip()
            if entry:
                tracks[filename] = entry
            else:
                tracks.pop(filename, None)
            try:
                save_metadata(tracks)
            except OSError as exc:
                self.send_json_error(500, "cannot write metadata: {}".format(exc))
                return
            try:
                stat = os.stat(os.path.join(MUSIC_DIR, filename))
            except OSError as exc:
                self.send_json_error(500, "track vanished: {}".format(exc))
                return
            song = describe(filename, stat, tracks)
        self.send_json(200, {"song": song})

    def handle_delete(self, song_id: str) -> None:
        resolved = self.track_path(song_id)
        if resolved is None:
            self.send_json_error(404, "track not found")
            return
        path, filename = resolved
        with _metadata_lock:
            try:
                os.remove(path)
            except OSError as exc:
                self.send_json_error(500, "cannot delete: {}".format(exc))
                return
            tracks = load_metadata()
            if tracks.pop(filename, None) is not None:
                try:
                    save_metadata(tracks)
                except OSError as exc:
                    log("deleted {} but metadata write failed: {}".format(filename, exc))
        log("deleted {}".format(filename))
        self.send_json(200, {"deleted": song_id, "filename": filename})

    @staticmethod
    def remove_quietly(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass


def clean_staging() -> None:
    """Drop partial uploads left behind by an interrupted run."""
    incoming = os.path.join(MUSIC_DIR, INCOMING_DIRNAME)
    if not os.path.isdir(incoming):
        return
    try:
        shutil.rmtree(incoming)
    except OSError as exc:
        log("could not clear {}: {}".format(incoming, exc))


def main() -> int:
    if sys.version_info < (3, 8):
        sys.stderr.write("Python 3.8 or newer is required; this is {}.{}.\n".format(
            sys.version_info[0], sys.version_info[1]))
        return 1

    if not os.path.isdir(MUSIC_DIR):
        try:
            os.makedirs(MUSIC_DIR, exist_ok=True)
        except OSError as exc:
            sys.stderr.write("music directory {} is unusable: {}\n".format(MUSIC_DIR, exc))
            return 1
    if not os.access(MUSIC_DIR, os.R_OK | os.W_OK | os.X_OK):
        sys.stderr.write("music directory {} is not readable and writable by "
                         "this user\n".format(MUSIC_DIR))
        return 1

    clean_staging()
    server = ThreadingHTTPServer((HOST, PORT), MusicHandler)
    server.daemon_threads = True
    log("serving {} on http://{}:{} ({} tracks)".format(
        MUSIC_DIR, HOST, PORT, len(list_songs())))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
