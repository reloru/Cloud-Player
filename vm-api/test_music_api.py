#!/usr/bin/env python3
"""Self-contained test suite for music_api.py. Standard library only.

Starts the API on a scratch port against a temporary music directory and
exercises it over a real socket: uploads, Range requests, cover art,
metadata overrides, deletion, and path-traversal rejection.

    python3 test_music_api.py
"""
import base64
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PORT = 8391
HOST = "127.0.0.1"
ROOT = os.path.dirname(os.path.abspath(__file__))
API = os.path.join(ROOT, "music_api.py")

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print("  ok   {}".format(label))
    else:
        print("  FAIL {} {}".format(label, detail))
        failures.append(label)


def conn():
    return http.client.HTTPConnection(HOST, PORT, timeout=10)


def request(method, path, body=None, headers=None):
    c = conn()
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    result = (r.status, dict(r.getheaders()), data)
    c.close()
    return result


def jrequest(method, path, body=None, headers=None):
    status, hdrs, data = request(method, path, body, headers)
    try:
        return status, hdrs, json.loads(data.decode("utf-8"))
    except ValueError:
        return status, hdrs, {"_raw": data[:200]}


def png_bytes():
    # 1x1 red PNG, hand-assembled so the test has no image dependency.
    import struct
    import zlib

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def main():
    music = tempfile.mkdtemp(prefix="cp-music-")
    env = dict(os.environ, MUSIC_DIR=music, MUSIC_API_PORT=str(PORT),
               MUSIC_API_HOST=HOST)
    proc = subprocess.Popen([sys.executable, API], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                request("GET", "/api/health")
                break
            except OSError:
                time.sleep(0.1)
        else:
            print("server never came up")
            print(proc.stdout.read().decode())
            return 1

        print("every response carries the X-Music-API marker")
        # The Worker uses this to tell a 5xx this process produced from one the
        # tunnel invented when this process is unreachable.
        for method, path in [("GET", "/api/health"), ("GET", "/api/songs"),
                             ("GET", "/api/nope"), ("DELETE", "/api/delete/QQ")]:
            _, hdrs, _ = request(method, path)
            check("marker on {} {}".format(method, path),
                  hdrs.get("X-Music-API") == "1", hdrs)

        print("health / empty library")
        status, _, body = jrequest("GET", "/api/health")
        check("health 200", status == 200, status)
        check("health empty", body.get("tracks") == 0, body)
        status, _, body = jrequest("GET", "/api/songs")
        check("songs empty", status == 200 and body == {"songs": []}, body)

        print("upload with Content-Length")
        payload = bytes(range(256)) * 400  # 102400 bytes
        status, _, body = jrequest(
            "POST", "/api/upload?name=Pink%20Floyd%20-%20Echoes.mp3", payload,
            {"Content-Type": "application/octet-stream",
             "Content-Length": str(len(payload))})
        check("upload 201", status == 201, (status, body))
        song = body.get("song", {})
        song_id = song.get("id")
        check("derived artist", song.get("artist") == "Pink Floyd", song)
        check("derived title", song.get("title") == "Echoes", song)
        check("size recorded", song.get("size") == len(payload), song)
        check("content type", song.get("contentType") == "audio/mpeg", song)
        check("no cover yet", song.get("cover") is False, song)
        check("id round-trips",
              base64.urlsafe_b64decode(song_id + "=" * (-len(song_id) % 4))
              .decode() == "Pink Floyd - Echoes.mp3", song_id)
        check("bytes on disk",
              open(os.path.join(music, "Pink Floyd - Echoes.mp3"), "rb").read()
              == payload)

        print("upload with chunked transfer-encoding")
        c = conn()
        c.putrequest("POST", "/api/upload?name=chunky.flac")
        c.putheader("Transfer-Encoding", "chunked")
        c.putheader("Content-Type", "application/octet-stream")
        c.endheaders()
        blob = b"FLAC" + b"z" * 70000
        for i in range(0, len(blob), 8192):
            piece = blob[i:i + 8192]
            c.send(b"%x\r\n" % len(piece) + piece + b"\r\n")
        c.send(b"0\r\n\r\n")
        r = c.getresponse()
        chunked_body = json.loads(r.read().decode())
        check("chunked upload 201", r.status == 201, (r.status, chunked_body))
        c.close()
        check("chunked bytes intact",
              open(os.path.join(music, "chunky.flac"), "rb").read() == blob)

        print("collision handling")
        status, _, body = jrequest(
            "POST", "/api/upload?name=chunky.flac", b"second",
            {"Content-Length": "6"})
        check("collision suffixed",
              body.get("song", {}).get("filename") == "chunky (2).flac", body)

        print("listing")
        status, _, body = jrequest("GET", "/api/songs")
        names = [s["filename"] for s in body["songs"]]
        check("three tracks listed", len(names) == 3, names)
        check("sorted by artist then title",
              names[0].startswith("chunky"), names)

        print("range requests")
        status, hdrs, data = request("GET", "/api/stream/" + song_id)
        check("full 200", status == 200, status)
        check("full body", data == payload, len(data))
        check("accept-ranges", hdrs.get("Accept-Ranges") == "bytes", hdrs)

        status, hdrs, data = request("GET", "/api/stream/" + song_id,
                                     headers={"Range": "bytes=100-199"})
        check("partial 206", status == 206, status)
        check("partial body", data == payload[100:200], len(data))
        check("content-range",
              hdrs.get("Content-Range") == "bytes 100-199/102400", hdrs)
        check("content-length 100", hdrs.get("Content-Length") == "100", hdrs)

        status, hdrs, data = request("GET", "/api/stream/" + song_id,
                                     headers={"Range": "bytes=102300-"})
        check("open-ended 206", status == 206, status)
        check("open-ended body", data == payload[102300:], len(data))

        status, hdrs, data = request("GET", "/api/stream/" + song_id,
                                     headers={"Range": "bytes=-50"})
        check("suffix 206", status == 206, status)
        check("suffix body", data == payload[-50:], len(data))

        status, hdrs, data = request("GET", "/api/stream/" + song_id,
                                     headers={"Range": "bytes=0-1"})
        check("safari probe 206", status == 206 and data == payload[0:2],
              (status, len(data)))

        status, hdrs, data = request("GET", "/api/stream/" + song_id,
                                     headers={"Range": "bytes=999999-"})
        check("unsatisfiable 416", status == 416, status)
        check("416 content-range",
              hdrs.get("Content-Range") == "bytes */102400", hdrs)

        status, hdrs, data = request("GET", "/api/stream/" + song_id,
                                     headers={"Range": "bytes=0-10,20-30"})
        check("multi-range falls back to 200", status == 200, status)
        check("multi-range full body", data == payload, len(data))

        status, hdrs, data = request("GET", "/api/stream/" + song_id,
                                     headers={"Range": "gibberish"})
        check("bad range ignored -> 200", status == 200, status)

        status, hdrs, data = request("HEAD", "/api/stream/" + song_id)
        check("HEAD 200 no body", status == 200 and data == b"", (status, data))
        check("HEAD content-length",
              hdrs.get("Content-Length") == str(len(payload)), hdrs)

        print("download")
        status, hdrs, data = request("GET", "/api/download/" + song_id)
        check("download 200", status == 200, status)
        check("attachment header",
              hdrs.get("Content-Disposition", "").startswith("attachment;"), hdrs)
        check("filename* encoded",
              "Pink%20Floyd%20-%20Echoes.mp3" in hdrs.get("Content-Disposition", ""),
              hdrs.get("Content-Disposition"))
        check("download body", data == payload, len(data))

        print("cover art")
        status, _, _ = request("GET", "/api/cover/" + song_id)
        check("no cover 404", status == 404, status)
        with open(os.path.join(music, "Pink Floyd - Echoes.png"), "wb") as f:
            f.write(png_bytes())
        status, hdrs, data = request("GET", "/api/cover/" + song_id)
        check("stem cover found", status == 200, status)
        check("cover mime", hdrs.get("Content-Type") == "image/png", hdrs)
        check("cover bytes", data == png_bytes(), len(data))
        status, _, body = jrequest("GET", "/api/songs")
        target = [s for s in body["songs"] if s["id"] == song_id][0]
        check("listing reports cover", target["cover"] is True, target)

        print("generic cover fallback")
        chunky_id = chunked_body["song"]["id"]
        status, _, _ = request("GET", "/api/cover/" + chunky_id)
        check("chunky has no cover", status == 404, status)
        with open(os.path.join(music, "cover.jpg"), "wb") as f:
            f.write(png_bytes())
        status, _, _ = request("GET", "/api/cover/" + chunky_id)
        check("folder-level cover used", status == 200, status)
        os.remove(os.path.join(music, "cover.jpg"))

        print("metadata overrides")
        status, _, body = jrequest(
            "PUT", "/api/metadata/" + song_id,
            json.dumps({"title": "Echoes (Live)", "album": "Pompeii"}).encode(),
            {"Content-Type": "application/json"})
        check("metadata 200", status == 200, (status, body))
        check("title overridden",
              body["song"]["title"] == "Echoes (Live)", body)
        check("album set", body["song"]["album"] == "Pompeii", body)
        check("artist still derived",
              body["song"]["artist"] == "Pink Floyd", body)
        check("metadata.json written",
              os.path.isfile(os.path.join(music, "metadata.json")))
        with open(os.path.join(music, "metadata.json")) as f:
            doc = json.load(f)
        check("sidecar shape",
              doc["tracks"]["Pink Floyd - Echoes.mp3"]["title"] == "Echoes (Live)",
              doc)
        status, _, body = jrequest("GET", "/api/songs")
        target = [s for s in body["songs"] if s["id"] == song_id][0]
        check("listing uses override", target["title"] == "Echoes (Live)", target)

        status, _, body = jrequest(
            "PUT", "/api/metadata/" + song_id,
            json.dumps({"title": ""}).encode())
        check("empty clears override", body["song"]["title"] == "Echoes", body)

        status, _, body = jrequest(
            "PUT", "/api/metadata/" + song_id,
            json.dumps({"title": "x" * 900}).encode())
        check("overlong field rejected", status == 400, (status, body))

        status, _, body = jrequest("PUT", "/api/metadata/" + song_id, b"not json")
        check("non-json rejected", status == 400, (status, body))

        print("metadata.json is not itself a track")
        status, _, body = jrequest("GET", "/api/songs")
        check("sidecar hidden from listing",
              "metadata.json" not in [s["filename"] for s in body["songs"]], body)

        print("path traversal and bad ids")
        for probe in ["../../etc/passwd", "/etc/passwd", "..", ".", "",
                      "metadata.json", ".bashrc"]:
            bad = base64.urlsafe_b64encode(probe.encode()).decode().rstrip("=")
            status, _, _ = request("GET", "/api/stream/" + bad)
            check("traversal blocked: {!r}".format(probe), status == 404, status)
        status, _, _ = request("GET", "/api/stream/!!!not-base64!!!")
        check("bad base64 -> 404", status == 404, status)
        status, _, _ = request("GET", "/api/stream/" + "A" * 600)
        check("overlong id -> 404", status == 404, status)

        print("upload validation")
        status, _, body = jrequest("POST", "/api/upload?name=evil.sh", b"x",
                                   {"Content-Length": "1"})
        check("bad extension rejected", status == 400, (status, body))
        status, _, body = jrequest("POST", "/api/upload", b"x",
                                   {"Content-Length": "1"})
        check("missing name rejected", status == 400, (status, body))
        status, _, body = jrequest("POST", "/api/upload?name=empty.mp3", b"",
                                   {"Content-Length": "0"})
        check("empty upload rejected", status == 400, (status, body))
        status, _, body = jrequest(
            "POST", "/api/upload?name=" + "../../escape.mp3", b"x",
            {"Content-Length": "1"})
        check("traversal name stripped",
              body.get("song", {}).get("filename") == "escape.mp3", body)
        check("escaped file stayed inside music dir",
              os.path.isfile(os.path.join(music, "escape.mp3")))
        status, _, body = jrequest(
            "POST", "/api/upload?name=.hidden.mp3", b"x", {"Content-Length": "1"})
        check("leading dots stripped",
              body.get("song", {}).get("filename") == "hidden.mp3", body)

        print("staging directory is not exposed")
        check("no leftover .part files",
              not any(n.endswith(".part")
                      for n in os.listdir(os.path.join(music, ".incoming"))),
              os.listdir(os.path.join(music, ".incoming")))

        print("delete")
        status, _, body = jrequest("DELETE", "/api/delete/" + song_id)
        check("delete 200", status == 200, (status, body))
        check("file gone",
              not os.path.exists(os.path.join(music, "Pink Floyd - Echoes.mp3")))
        status, _, _ = request("GET", "/api/stream/" + song_id)
        check("stream after delete 404", status == 404, status)
        status, _, body = jrequest("DELETE", "/api/delete/" + song_id)
        check("second delete 404", status == 404, status)
        with open(os.path.join(music, "metadata.json")) as f:
            doc = json.load(f)
        check("sidecar entry pruned",
              "Pink Floyd - Echoes.mp3" not in doc["tracks"], doc)

        print("unknown routes")
        status, _, _ = request("GET", "/api/nope")
        check("unknown GET 404", status == 404, status)
        status, _, _ = request("GET", "/")
        check("root 404", status == 404, status)

        print("keep-alive across several requests on one connection")
        c = conn()
        codes = []
        for _ in range(4):
            c.request("GET", "/api/songs")
            r = c.getresponse()
            r.read()
            codes.append(r.status)
        c.close()
        check("connection reused", codes == [200] * 4, codes)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(music, ignore_errors=True)

    print()
    if failures:
        print("{} FAILED of {} checks:".format(len(failures), checks))
        for name in failures:
            print("  - " + name)
        return 1
    print("all {} checks passed".format(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
