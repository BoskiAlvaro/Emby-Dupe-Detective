import fnmatch as fnmod
import os
import json
import threading
import uuid
from datetime import datetime, timezone
import requests
from flask import Flask, render_template, request, jsonify, send_file
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "emby-dupes-secret-key-change-me")

DATA_DIR = os.environ.get("DATA_DIR", "/app/data" if os.path.exists("/app") else "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
EXCLUSIONS_FILE = os.path.join(DATA_DIR, "exclusions.json")
MEDIA_TYPES = ("movies", "series")

_exclusions_lock = threading.Lock()


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(config):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)


# ─── EXCLUSIONS ──────────────────────────────────────
# Stored separately from config.json: it's user data that grows over time,
# not settings, and keeping it apart means it can be backed up / reset
# independently and never touches the API key.
#
# Group exclusion: the duplicate group (by group key) is hidden as long as all
# of its files were part of the group when it was excluded. If a new file shows
# up in that group, the group is reported again.
# File exclusion: the file (by path) is ignored entirely when grouping.

def load_exclusions():
    try:
        with open(EXCLUSIONS_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("groups", [])
    data.setdefault("files", [])
    return data


def save_exclusions(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = EXCLUSIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, EXCLUSIONS_FILE)


def get_active_exclusions(media_type):
    data = load_exclusions()
    groups = {
        g["key"]: set(g.get("paths", []))
        for g in data["groups"]
        if g.get("media_type") == media_type
    }
    files = {f["path"] for f in data["files"] if f.get("media_type") == media_type}
    return groups, files


def group_key_str(key):
    return f"{key[0]}:{key[1]}"


def is_group_excluded(group, excluded_groups):
    snapshot = excluded_groups.get(group["key"])
    if snapshot is None:
        return False
    return all(f["path"] in snapshot for f in group["files"])


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_size(size_bytes):
    if not size_bytes:
        return "Unknown"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def format_bitrate(bps):
    if not bps:
        return None
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    elif bps >= 1_000:
        return f"{bps / 1_000:.0f} Kbps"
    return f"{bps} bps"


def format_runtime(ticks):
    if not ticks:
        return None
    total_seconds = ticks // 10_000_000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def classify_resolution(height):
    if not height:
        return "Unknown"
    if height >= 2160:
        return "4k"
    if height >= 1080:
        return "1080"
    if height >= 720:
        return "720"
    if height >= 480:
        return "480"
    return "sd"


DEFAULT_SCORING_CONFIG = {
    "audio_codec_scores": {
        "Unknown": 0,
        "aac": 1000,
        "ac3": 1000,
        "dca": 2000,
        "dca-ma": 4000,
        "eac3": 1250,
        "flac": 2500,
        "mp2": 500,
        "mp3": 1000,
        "pcm": 2500,
        "truehd": 4500,
        "wmapro": 200,
    },
    "video_codec_scores": {
        "Unknown": 0,
        "h264": 10000,
        "h265": 5000,
        "hevc": 5000,
        "mpeg1video": 250,
        "mpeg2video": 250,
        "mpeg4": 500,
        "msmpeg4": 100,
        "msmpeg4v2": 100,
        "msmpeg4v3": 100,
        "vc1": 3000,
        "vp9": 1000,
        "wmv2": 250,
        "wmv3": 250,
    },
    "video_resolution_scores": {
        "4k": 20000,
        "1080": 10000,
        "720": 5000,
        "480": 3000,
        "sd": 1000,
        "Unknown": 0,
    },
    "filename_scores": {},
    "score_filesize": True,
    "auto_select_losers": False,
}


def get_scoring_config():
    cfg = load_config()
    saved = cfg.get("scoring_config")
    if saved:
        merged = dict(DEFAULT_SCORING_CONFIG)
        merged.update(saved)
        return merged
    return dict(DEFAULT_SCORING_CONFIG)


def get_score(file_info, scoring_cfg):
    score = 0
    # Audio codec score
    audio_scores = scoring_cfg.get("audio_codec_scores", {})
    ac = file_info.get("audio_codec", "").lower()
    score += int(audio_scores.get(ac, 0))

    # Video codec score
    video_scores = scoring_cfg.get("video_codec_scores", {})
    vc = file_info.get("video_codec_raw", "").lower()
    score += int(video_scores.get(vc, 0))

    # Video resolution score
    res_scores = scoring_cfg.get("video_resolution_scores", {})
    res = file_info.get("video_resolution_label", "Unknown")
    score += int(res_scores.get(res, 0))

    # Filename pattern scores
    filename_scores = scoring_cfg.get("filename_scores", {})
    path = file_info.get("path", "")
    basename = os.path.basename(path).lower()
    for pattern, pattern_score in filename_scores.items():
        if fnmod.fnmatch(basename, pattern.lower()):
            score += int(pattern_score)

    # Video bitrate (bps → kbps, then *2)
    bitrate_kbps = (file_info.get("video_bitrate_raw", 0) or 0) / 1000
    score += int(bitrate_kbps) * 2

    # Video dimensions
    score += (file_info.get("video_width", 0) or 0) * 2
    score += (file_info.get("video_height", 0) or 0) * 2

    # Audio channels
    score += (file_info.get("audio_channels", 0) or 0) * 1000

    # File size
    if scoring_cfg.get("score_filesize", True):
        score += int((file_info.get("size", 0) or 0) / 100000)

    return int(score)


def get_emby_headers(api_key):
    return {
        "X-Emby-Token": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def build_base_url(host, port):
    host = host.strip().rstrip("/")
    if not host.startswith("http://") and not host.startswith("https://"):
        host = f"http://{host}"
    if port and str(port).strip():
        return f"{host}:{port}"
    return host

def fetch_all_items(host, port, api_key, item_type="Movie"):
    base_url = build_base_url(host, port)
    headers = get_emby_headers(api_key)
    fields = "MediaSources,Path,ProductionYear,OriginalTitle,SortName,ProviderIds,SeriesName,ParentIndexNumber,IndexNumber,RunTimeTicks"

    all_items = []
    start_index = 0
    limit = 5000 # Safer batch size

    while True:
        params = {
            "IncludeItemTypes": item_type,
            "Recursive": "true",
            "Fields": fields,
            "EnableTotalRecordCount": "true",
            "StartIndex": start_index,
            "Limit": limit,
        }
        resp = requests.get(f"{base_url}/Items", headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("Items", [])

        if not items:
            break

        all_items.extend(items)
        start_index += limit

    return all_items

def parse_movie_entry(item, source):
    streams = source.get("MediaStreams", [])
    video_stream = next((s for s in streams if s.get("Type") == "Video"), None)
    audio_streams = [s for s in streams if s.get("Type") == "Audio"]

    resolution = "Unknown"
    video_codec = "Unknown"
    video_bitrate = "Unknown"

    if video_stream:
        w = video_stream.get("Width")
        h = video_stream.get("Height")
        if w and h:
            resolution = f"{w}×{h}"
        vc = video_stream.get("Codec", "")
        video_codec = vc.upper() if vc else "Unknown"
        vbr = video_stream.get("BitRate") or source.get("Bitrate")
        video_bitrate = format_bitrate(vbr) or "Unknown"

    audio_info = []
    audio_channels_total = 0
    best_audio_codec = ""
    for a in audio_streams:
        codec = (a.get("Codec") or "").upper()
        abr = format_bitrate(a.get("BitRate"))
        lang = a.get("Language") or ""
        channels = a.get("Channels") or 0
        if channels > audio_channels_total:
            audio_channels_total = channels
        if not best_audio_codec and codec:
            best_audio_codec = codec.lower()
        label = codec
        if channels:
            label += f" {channels}ch"
        if abr:
            label += f" @ {abr}"
        if lang and lang.lower() not in ("und", ""):
            label += f" [{lang.upper()}]"
        if label:
            audio_info.append(label)

    video_width = video_stream.get("Width", 0) or 0 if video_stream else 0
    video_height = video_stream.get("Height", 0) or 0 if video_stream else 0
    video_bitrate_raw = (video_stream.get("BitRate") or source.get("Bitrate") or 0) if video_stream else 0
    video_codec_raw = (video_stream.get("Codec") or "").lower() if video_stream else ""
    video_resolution_label = classify_resolution(video_height)

    runtime_ticks = source.get("RunTimeTicks") or item.get("RunTimeTicks")
    runtime_display = format_runtime(runtime_ticks)

    return {
        "item_id": item.get("Id"),
        "source_id": source.get("Id"),
        "name": item.get("Name", "Unknown"),
        "year": item.get("ProductionYear"),
        "path": source.get("Path", ""),
        "size": source.get("Size") or 0,
        "size_display": format_size(source.get("Size")),
        "resolution": resolution,
        "video_codec": video_codec,
        "video_bitrate": video_bitrate,
        "audio_info": audio_info,
        "container": (source.get("Container") or "").upper(),
        "runtime_display": runtime_display,
        # Raw fields for scoring
        "video_width": video_width,
        "video_height": video_height,
        "video_bitrate_raw": video_bitrate_raw,
        "video_codec_raw": video_codec_raw,
        "video_resolution_label": video_resolution_label,
        "audio_codec": best_audio_codec,
        "audio_channels": audio_channels_total,
    }


def get_movie_key(item):
    provider_ids = item.get("ProviderIds") or {}
    imdb = provider_ids.get("Imdb") or provider_ids.get("imdb")
    tmdb = provider_ids.get("Tmdb") or provider_ids.get("tmdb")
    if imdb:
        return ("imdb", imdb)
    if tmdb:
        return ("tmdb", tmdb)
    # Fall back to normalized name + year only when no provider ID available
    name = (item.get("Name") or "").strip().lower()
    year = item.get("ProductionYear")
    return ("name_year", f"{name}|{year}")

def get_episode_key(item):
    series_id = item.get("SeriesId")
    series_name = (item.get("SeriesName") or "").strip().lower()
    series_identifier = series_id if series_id else series_name

    season = item.get("ParentIndexNumber")
    episode = item.get("IndexNumber")

    return ("series_season_ep", f"{series_identifier}|{season}|{episode}")

def find_duplicates(movies):
    scoring_cfg = get_scoring_config()
    excluded_groups, excluded_files = get_active_exclusions("movies")
    excluded_files_hit = set()

    groups = {}
    for item in movies:
        sources = item.get("MediaSources") or []
        if not sources:
            continue
        key = get_movie_key(item)
        if key not in groups:
            groups[key] = {
                "key": group_key_str(key),
                "name": item.get("Name", "Unknown"),
                "year": item.get("ProductionYear"),
                "files": [],
            }
        for source in sources:
            path = source.get("Path", "")
            if path and path in excluded_files:
                excluded_files_hit.add(path)
                continue
            entry = parse_movie_entry(item, source)
            entry["score"] = get_score(entry, scoring_cfg)
            groups[key]["files"].append(entry)

    duplicates = []
    excluded_groups_hit = 0
    for group in groups.values():
        if len(group["files"]) >= 2:
            if is_group_excluded(group, excluded_groups):
                excluded_groups_hit += 1
                continue
            group["files"].sort(key=lambda x: x.get("score", 0), reverse=True)
            duplicates.append(group)

    duplicates.sort(key=lambda x: x["name"].lower())
    exclusions_applied = excluded_groups_hit + len(excluded_files_hit)
    return duplicates, scoring_cfg.get("auto_select_losers", False), exclusions_applied


def find_series_duplicates(episodes):
    scoring_cfg = get_scoring_config()
    excluded_groups, excluded_files = get_active_exclusions("series")
    excluded_files_hit = set()

    # Group by episode identity
    ep_groups = {}
    for item in episodes:
        sources = item.get("MediaSources") or []
        if not sources:
            continue
        key = get_episode_key(item)
        if key not in ep_groups:
            ep_groups[key] = {
                "key": group_key_str(key),
                "series_name": item.get("SeriesName") or "Unknown",
                "season": item.get("ParentIndexNumber"),
                "episode": item.get("IndexNumber"),
                "name": item.get("Name", "Unknown"),
                "files": [],
            }
        for source in sources:
            path = source.get("Path", "")
            if path and path in excluded_files:
                excluded_files_hit.add(path)
                continue
            entry = parse_movie_entry(item, source)
            entry["score"] = get_score(entry, scoring_cfg)
            ep_groups[key]["files"].append(entry)

    # Only keep episodes with 2+ files that aren't excluded
    dup_episodes = {}
    excluded_groups_hit = 0
    for k, v in ep_groups.items():
        if len(v["files"]) < 2:
            continue
        if is_group_excluded(v, excluded_groups):
            excluded_groups_hit += 1
            continue
        dup_episodes[k] = v
    for ep in dup_episodes.values():
        ep["files"].sort(key=lambda x: x.get("score", 0), reverse=True)

    # Build hierarchical structure: series → seasons → episodes
    series_map = {}
    for ep in dup_episodes.values():
        sname = ep["series_name"]
        season = ep["season"]
        if sname not in series_map:
            series_map[sname] = {}
        if season not in series_map[sname]:
            series_map[sname][season] = []
        series_map[sname][season].append(ep)

    # Sort and build result
    result = []
    for sname in sorted(series_map.keys(), key=str.lower):
        seasons = []
        for snum in sorted(series_map[sname].keys(), key=lambda x: x if x is not None else -1):
            eps = sorted(series_map[sname][snum], key=lambda e: e["episode"] if e["episode"] is not None else -1)
            seasons.append({"season_number": snum, "episodes": eps})
        result.append({"series_name": sname, "seasons": seasons})

    exclusions_applied = excluded_groups_hit + len(excluded_files_hit)
    return result, scoring_cfg.get("auto_select_losers", False), exclusions_applied


@app.route("/")
def index():
    config = load_config()
    return render_template("index.html", config=config)


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = load_config()
    # Mask API key for display
    display = dict(cfg)
    if display.get("api_key"):
        k = display["api_key"]
        display["api_key_masked"] = k[:4] + "••••••••" + k[-4:] if len(k) > 8 else "••••••••"
    return jsonify(display)


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.json
    if not data.get("host"):
        return jsonify({"error": "Host is required"}), 400
    if not data.get("api_key"):
        return jsonify({"error": "API key is required"}), 400
    cfg = load_config()
    cfg["host"] = data["host"].strip()
    cfg["port"] = str(data.get("port", "8096")).strip()
    cfg["api_key"] = data["api_key"].strip()
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/test", methods=["POST"])
def test_connection():
    data = request.json
    try:
        base_url = build_base_url(data["host"], data.get("port", "8096"))
        headers = get_emby_headers(data["api_key"])
        resp = requests.get(f"{base_url}/System/Info/Public", headers=headers, timeout=10)
        resp.raise_for_status()
        info = resp.json()
        return jsonify({
            "ok": True,
            "server_name": info.get("ServerName", "Emby Server"),
            "version": info.get("Version", ""),
        })
    except requests.exceptions.ConnectionError:
        return jsonify({"ok": False, "error": "Cannot connect to server. Check host and port."}), 400
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timed out."}), 400
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            return jsonify({"ok": False, "error": "Invalid API key. Check your Emby API key."}), 401
        return jsonify({"ok": False, "error": f"HTTP {e.response.status_code} from server."}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/scan", methods=["POST"])
def scan():
    config = load_config()
    if not config.get("host") or not config.get("api_key"):
        return jsonify({"error": "Server not configured. Please configure connection first."}), 400

    data = request.json or {}
    media_type = data.get("media_type", "movies")

    try:
        if media_type == "series":
            episodes = fetch_all_items(config["host"], config.get("port", "8096"), config["api_key"], "Episode")
            duplicates, auto_select_losers, exclusions_applied = find_series_duplicates(episodes)
            total_episodes = len(episodes)
            dup_episode_count = sum(
                len(ep["files"])
                for s in duplicates
                for season in s["seasons"]
                for ep in season["episodes"]
            )
            dup_group_count = sum(
                len(season["episodes"])
                for s in duplicates
                for season in s["seasons"]
            )
            return jsonify({
                "media_type": "series",
                "duplicates": duplicates,
                "total_items": total_episodes,
                "duplicate_groups": dup_group_count,
                "duplicate_files": dup_episode_count,
                "auto_select_losers": auto_select_losers,
                "exclusions_applied": exclusions_applied,
            })
        else:
            movies = fetch_all_items(config["host"], config.get("port", "8096"), config["api_key"], "Movie")
            duplicates, auto_select_losers, exclusions_applied = find_duplicates(movies)
            return jsonify({
                "media_type": "movies",
                "duplicates": duplicates,
                "total_items": len(movies),
                "duplicate_groups": len(duplicates),
                "duplicate_files": sum(len(g["files"]) for g in duplicates),
                "auto_select_losers": auto_select_losers,
                "exclusions_applied": exclusions_applied,
            })
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Emby server."}), 400
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            return jsonify({"error": "Invalid API key."}), 401
        return jsonify({"error": f"Emby API error: {e.response.status_code}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug", methods=["POST"])
def debug_scan():
    config = load_config()
    if not config.get("host") or not config.get("api_key"):
        return jsonify({"error": "Not configured"}), 400
    try:
        movies = fetch_all_items(config["host"], config.get("port", "8096"), config["api_key"])
        groups = {}
        for item in movies:
            sources = item.get("MediaSources") or []
            if not sources:
                continue
            key = get_movie_key(item)
            groups.setdefault(key, []).append({
                "id": item.get("Id"),
                "name": item.get("Name"),
                "year": item.get("ProductionYear"),
                "provider_ids": item.get("ProviderIds"),
                "source_count": len(sources),
            })
        duplicate_groups = {str(k): v for k, v in groups.items() if len(v) >= 2}
        return jsonify({
            "total_movies": len(movies),
            "total_groups": len(groups),
            "duplicate_groups_found": len(duplicate_groups),
            "duplicates": duplicate_groups,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete", methods=["POST"])
def delete_items():
    data = request.json
    item_ids = list(set(data.get("item_ids", [])))
    delete_files = bool(data.get("delete_files", False))

    cfg = load_config()
    if not cfg.get("host") or not cfg.get("api_key"):
        return jsonify({"error": "Not configured"}), 400

    base_url = build_base_url(cfg["host"], cfg.get("port", "8096"))
    headers = get_emby_headers(cfg["api_key"])

    deleted, errors = [], []
    for item_id in item_ids:
        try:
            params = {"deleteFiles": "true"} if delete_files else {}
            resp = requests.delete(
                f"{base_url}/Items/{item_id}",
                headers=headers,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            deleted.append(item_id)
        except Exception as e:
            errors.append({"id": item_id, "error": str(e)})

    return jsonify({"deleted": deleted, "errors": errors})


@app.route("/api/export-config", methods=["GET"])
def get_export_config():
    cfg = load_config()
    return jsonify(cfg.get("export_config", {}))


@app.route("/api/export-config", methods=["POST"])
def set_export_config():
    data = request.json
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    cfg = load_config()
    cfg["export_config"] = {
        "prefix": data.get("prefix", ""),
        "suffix": data.get("suffix", ""),
        "stripFilename": bool(data.get("stripFilename", False)),
        "pathMappings": [
            {"from": m.get("from", ""), "to": m.get("to", "")}
            for m in data.get("pathMappings", [])
            if m.get("from", "").strip()
        ],
    }
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/export-config/series", methods=["GET"])
def get_series_export_config():
    cfg = load_config()
    return jsonify(cfg.get("series_export_config", {}))


@app.route("/api/export-config/series", methods=["POST"])
def set_series_export_config():
    data = request.json
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    cfg = load_config()
    cfg["series_export_config"] = {
        "prefix": data.get("prefix", ""),
        "suffix": data.get("suffix", ""),
        "stripFilename": bool(data.get("stripFilename", False)),
    }
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/scoring-config", methods=["GET"])
def get_scoring_config_api():
    return jsonify(get_scoring_config())


@app.route("/api/scoring-config", methods=["POST"])
def set_scoring_config_api():
    data = request.json
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    cfg = load_config()
    cfg["scoring_config"] = {
        "audio_codec_scores": data.get("audio_codec_scores", DEFAULT_SCORING_CONFIG["audio_codec_scores"]),
        "video_codec_scores": data.get("video_codec_scores", DEFAULT_SCORING_CONFIG["video_codec_scores"]),
        "video_resolution_scores": data.get("video_resolution_scores", DEFAULT_SCORING_CONFIG["video_resolution_scores"]),
        "filename_scores": data.get("filename_scores", {}),
        "score_filesize": bool(data.get("score_filesize", True)),
        "auto_select_losers": bool(data.get("auto_select_losers", False)),
    }
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/exclusions", methods=["GET"])
def get_exclusions():
    return jsonify(load_exclusions())


@app.route("/api/exclusions/group", methods=["POST"])
def add_group_exclusion():
    data = request.json or {}
    media_type = data.get("media_type")
    key = (data.get("key") or "").strip()
    paths = [p for p in data.get("paths", []) if isinstance(p, str) and p]
    if media_type not in MEDIA_TYPES:
        return jsonify({"error": "Invalid media type"}), 400
    if not key or not paths:
        return jsonify({"error": "Group key and paths are required"}), 400

    entry = {
        "id": uuid.uuid4().hex,
        "media_type": media_type,
        "key": key,
        "label": data.get("label") or key,
        "paths": sorted(set(paths)),
        "added": now_iso(),
    }
    with _exclusions_lock:
        excl = load_exclusions()
        # Re-excluding the same group replaces its file snapshot
        excl["groups"] = [
            g for g in excl["groups"]
            if not (g.get("media_type") == media_type and g.get("key") == key)
        ]
        excl["groups"].append(entry)
        save_exclusions(excl)
    return jsonify({"ok": True, "entry": entry})


@app.route("/api/exclusions/files", methods=["POST"])
def add_file_exclusions():
    data = request.json or {}
    media_type = data.get("media_type")
    files = data.get("files", [])
    if media_type not in MEDIA_TYPES:
        return jsonify({"error": "Invalid media type"}), 400

    added = []
    with _exclusions_lock:
        excl = load_exclusions()
        existing = {f["path"] for f in excl["files"] if f.get("media_type") == media_type}
        for f in files:
            path = (f.get("path") or "") if isinstance(f, dict) else ""
            if not path or path in existing:
                continue
            entry = {
                "id": uuid.uuid4().hex,
                "media_type": media_type,
                "path": path,
                "label": f.get("label") or os.path.basename(path),
                "added": now_iso(),
            }
            excl["files"].append(entry)
            existing.add(path)
            added.append(entry)
        if added:
            save_exclusions(excl)
    if not added and not files:
        return jsonify({"error": "No files given"}), 400
    return jsonify({"ok": True, "added": added})


@app.route("/api/exclusions/<entry_id>", methods=["DELETE"])
def remove_exclusion(entry_id):
    with _exclusions_lock:
        excl = load_exclusions()
        before = len(excl["groups"]) + len(excl["files"])
        excl["groups"] = [g for g in excl["groups"] if g.get("id") != entry_id]
        excl["files"] = [f for f in excl["files"] if f.get("id") != entry_id]
        if len(excl["groups"]) + len(excl["files"]) == before:
            return jsonify({"error": "Exclusion not found"}), 404
        save_exclusions(excl)
    return jsonify({"ok": True})


@app.route("/api/exclusions/remove", methods=["POST"])
def remove_exclusions_bulk():
    ids = set((request.json or {}).get("ids", []))
    if not ids:
        return jsonify({"error": "No ids given"}), 400
    with _exclusions_lock:
        excl = load_exclusions()
        before = len(excl["groups"]) + len(excl["files"])
        excl["groups"] = [g for g in excl["groups"] if g.get("id") not in ids]
        excl["files"] = [f for f in excl["files"] if f.get("id") not in ids]
        removed = before - len(excl["groups"]) - len(excl["files"])
        if removed:
            save_exclusions(excl)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/exclusions", methods=["DELETE"])
def clear_exclusions():
    media_type = request.args.get("media_type")
    if media_type not in MEDIA_TYPES:
        return jsonify({"error": "Invalid media type"}), 400
    with _exclusions_lock:
        excl = load_exclusions()
        excl["groups"] = [g for g in excl["groups"] if g.get("media_type") != media_type]
        excl["files"] = [f for f in excl["files"] if f.get("media_type") != media_type]
        save_exclusions(excl)
    return jsonify({"ok": True})


@app.route("/api/export", methods=["POST"])
def export_paths():
    data = request.json
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"error": "No paths selected"}), 400
    content = "\n".join(paths) + "\n"
    buf = BytesIO(content.encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="text/plain",
        as_attachment=True,
        download_name="duplicate_movie_paths.txt",
    )


if __name__ == "__main__":
    port = int(os.environ.get("APP_PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    print(f"Starting server on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=debug)
