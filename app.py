"""
Eclipse Music addon — YouTube Music source (our own build).

Resolves and streams audio from YouTube Music without triggering YouTube
"Sign in to confirm you're not a bot" on datacenter IPs. The key is the
Android player client (com.google.android.youtube) used by native apps like
InnerTune / Jetiner — unlike the web browser client, it isn't bot-gated.

Because the resolved googlevideo URL is locked to this server's IP, audio is
proxied through this server so it plays on any device.

Endpoints:
  GET /manifest.json
  GET /search?q=...        tracks / albums / artists / playlists
  GET /stream/<id>         returns the proxied audio URL
  GET /proxy/<id>          streams the audio bytes (range/seek aware)
  GET /album/<id>          album tracks
  GET /artist/<id>         artist top tracks + albums
  GET /playlist/<id>       playlist tracks
"""
import os
import time
import threading

import requests
import yt_dlp
from flask import Flask, request, jsonify, redirect, Response, stream_with_context
from flask_cors import CORS
from ytmusicapi import YTMusic

app = Flask(__name__)
CORS(app)
yt = YTMusic()

STREAM_MODE = os.environ.get("STREAM_MODE", "proxy").lower()

# Player clients we try, most-preferred first. "web_music" (YTM's official
# client) is first: paired with the bgutil PO-Token provider and the EJS
# challenge solver it returns real audio streams even from datacenter IPs.
# The native clients fall back when the POT server is unavailable.
FALLBACK_CLIENTS = os.environ.get(
    "CLIENTS", "web_music,web_safari,web,android,ios,tv"
).split(",")

CACHE_DIR = os.environ.get("YTDLP_CACHE", "/tmp/ytdlp-cache")

_url_cache = {}   # video_id -> (url, content_type, expiry_ts)
_cache_lock = threading.Lock()
_client_cache = {}  # client -> set of video_ids that worked (best-client memo)
_client_lock = threading.Lock()


def _ydl_opts(client):
    return {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio[protocol^=https]/bestaudio/best",
        "extractor_args": {"youtube": {"player_client": [client]}},
        # Lets yt-dlp fetch the external JS challenge solver (yt-dlp-ejs) for
        # solving n-sig on the web client, which the bgutil POT server unblocks.
        "remote_components": ["ejs:github"],
        "cachedir": CACHE_DIR,
    }


def _preferred_client():
    with _client_lock:
        for c in FALLBACK_CLIENTS:
            if _client_cache.get(c):
                return c
    return None


def _remember_client(client):
    with _client_lock:
        _client_cache[client] = True


def resolve_url(video_id):
    """Return (direct_url, content_type) for a YouTube Music track."""
    now = time.time()
    with _cache_lock:
        hit = _url_cache.get(video_id)
        if hit and hit[2] > now + 300:
            return hit[0], hit[1]

    client = _preferred_client() or FALLBACK_CLIENTS[0]
    last_err = None
    for candidate in ([client] + [c for c in FALLBACK_CLIENTS if c != client]):
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(candidate)) as ydl:
                info = ydl.extract_info(
                    f"https://music.youtube.com/watch?v={video_id}",
                    download=False,
                )
            url = info.get("url")
            if not url and info.get("requested_formats"):
                url = info["requested_formats"][0].get("url")
            if not url:
                raise RuntimeError("no playable url resolved")
            ext = info.get("ext", "m4a")
            ctype = "audio/mp4" if ext in ("m4a", "mp4") else f"audio/{ext}"
            _remember_client(candidate)
            with _cache_lock:
                _url_cache[video_id] = (url, ctype, now + 3600)
            return url, ctype
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    raise RuntimeError(f"stream resolution failed: {last_err}")


def best_thumb(thumbs):
    if not thumbs:
        return None
    return max(thumbs, key=lambda t: t.get("width", 0) or 0).get("url")


def artist_names(artists):
    if not artists:
        return ""
    return ", ".join(a.get("name", "") for a in artists if a.get("name"))


def map_track(item):
    album = item.get("album")
    return {
        "id": item.get("videoId"),
        "title": item.get("title"),
        "artist": artist_names(item.get("artists")),
        "album": album.get("name") if isinstance(album, dict) else album,
        "duration": item.get("duration_seconds"),
        "artworkURL": best_thumb(item.get("thumbnails")),
    }


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "com.eclipse.user.ytmusic",
        "name": "YouTube Music",
        "version": "1.0.0",
        "description": "Search and stream from YouTube Music",
        "resources": ["search", "stream", "catalog"],
        "types": ["track", "album", "artist"],
        "contentType": "music",
    })


def _search_tracks(query):
    return [map_track(r) for r in yt.search(query, filter="songs", limit=20) if r.get("videoId")]


def _search_albums(query):
    out = []
    for r in yt.search(query, filter="albums", limit=15):
        if not r.get("browseId"):
            continue
        out.append({
            "id": r.get("browseId"), "title": r.get("title") or r.get("album"),
            "artist": artist_names(r.get("artists")),
            "artworkURL": best_thumb(r.get("thumbnails")), "year": r.get("year"),
        })
    return out


def _search_artists(query):
    out = []
    for r in yt.search(query, filter="artists", limit=10):
        if not r.get("browseId"):
            continue
        out.append({
            "id": r.get("browseId"), "name": r.get("artist"),
            "artworkURL": best_thumb(r.get("thumbnails")),
        })
    return out


def _search_playlists(query):
    out = []
    for r in yt.search(query, filter="playlists", limit=15):
        if not r.get("browseId"):
            continue
        out.append({
            "id": r.get("browseId"), "title": r.get("title"),
            "creator": r.get("author"),
            "artworkURL": best_thumb(r.get("thumbnails")),
            "trackCount": r.get("itemCount"),
        })
    return out


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"tracks": [], "albums": [], "artists": [], "playlists": []})

    return jsonify({
        "tracks": _search_tracks(query),
        "albums": _search_albums(query),
        "artists": _search_artists(query),
        "playlists": _search_playlists(query),
    })


@app.route("/stream/<video_id>")
def stream(video_id):
    try:
        url, _ = resolve_url(video_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502

    if STREAM_MODE == "direct":
        return jsonify({"url": url, "format": "m4a"})

    base = request.host_url.rstrip("/")
    return jsonify({"url": f"{base}/proxy/{video_id}", "format": "m4a"})


@app.route("/proxy/<video_id>")
def proxy(video_id):
    try:
        url, ctype = resolve_url(video_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502

    fwd = {}
    if request.headers.get("Range"):
        fwd["Range"] = request.headers["Range"]

    upstream = requests.get(url, headers=fwd, stream=True, timeout=30)
    out_headers = {"Accept-Ranges": "bytes", "Content-Type": ctype}
    for h in ("Content-Length", "Content-Range"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(stream_with_context(generate()),
                    status=upstream.status_code, headers=out_headers)


@app.route("/album/<album_id>")
def album(album_id):
    a = yt.get_album(album_id)
    tracks = [{
        "id": t.get("videoId"), "title": t.get("title"),
        "artist": artist_names(t.get("artists")) or artist_names(a.get("artists")),
        "duration": t.get("duration_seconds"),
        "artworkURL": best_thumb(t.get("thumbnails")) or best_thumb(a.get("thumbnails")),
    } for t in a.get("tracks", []) if t.get("videoId")]
    return jsonify({
        "id": album_id, "title": a.get("title"),
        "artist": artist_names(a.get("artists")),
        "artworkURL": best_thumb(a.get("thumbnails")),
        "year": a.get("year"), "description": a.get("description"),
        "trackCount": a.get("trackCount"), "tracks": tracks,
    })


@app.route("/artist/<artist_id>")
def artist(artist_id):
    ar = yt.get_artist(artist_id)
    top_tracks = [{
        "id": s.get("videoId"), "title": s.get("title"),
        "artist": artist_names(s.get("artists")) or ar.get("name"),
        "artworkURL": best_thumb(s.get("thumbnails")),
    } for s in ar.get("songs", {}).get("results", []) if s.get("videoId")]
    albums = [{
        "id": al.get("browseId"), "title": al.get("title"),
        "artist": artist_names(al.get("artists")) or ar.get("name"),
        "artworkURL": best_thumb(al.get("thumbnails")), "year": al.get("year"),
    } for al in ar.get("albums", {}).get("results", []) if al.get("browseId")]
    return jsonify({
        "id": artist_id, "name": ar.get("name"),
        "artworkURL": best_thumb(ar.get("thumbnails")),
        "bio": ar.get("description"), "topTracks": top_tracks, "albums": albums,
    })


@app.route("/playlist/<playlist_id>")
def playlist(playlist_id):
    pid = playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
    p = yt.get_playlist(pid, limit=200)
    tracks = [{
        "id": t.get("videoId"), "title": t.get("title"),
        "artist": artist_names(t.get("artists")),
        "duration": t.get("duration_seconds"),
        "artworkURL": best_thumb(t.get("thumbnails")),
    } for t in p.get("tracks", []) if t.get("videoId")]
    creator = p.get("author")
    if isinstance(creator, dict):
        creator = creator.get("name")
    return jsonify({
        "id": playlist_id, "title": p.get("title"),
        "description": p.get("description"),
        "artworkURL": best_thumb(p.get("thumbnails")),
        "creator": creator, "tracks": tracks,
    })


@app.route("/health")
def health():
    return jsonify({"ok": True, "mode": STREAM_MODE, "clients": FALLBACK_CLIENTS})


@app.route("/")
def home():
    return redirect("/manifest.json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
