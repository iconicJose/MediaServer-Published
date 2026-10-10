"""Media catalog and playback for the user-selected library."""

import argparse
import http.cookiejar
import difflib
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import struct
import time
import urllib.parse
import urllib.error
import urllib.request
import integrations
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

VIDEO_EXTENSIONS = {'.mp4', '.m4v', '.mkv', '.webm', '.mov', '.avi', '.ts', '.m2ts', '.mts', '.mpg', '.mpeg', '.wmv', '.flv', '.ogv', '.3gp'}
CONVERSIONS = ThreadPoolExecutor(max_workers=1)
CONVERSION_JOBS = {}
CONVERSION_LOCK = threading.Lock()
DELETED_SOURCES = set()
HLS_LOCK = threading.Lock()
HLS_JOBS = {}
HLS_IDLE_TIMEOUT = 10 * 60
METADATA = ThreadPoolExecutor(max_workers=1)
METADATA_LOCK = threading.Lock()
METADATA_JOB = None
MANAGE_LOCK = threading.Lock()
SUBTITLE_LOCK = threading.Lock()
PROGRESS_LOCK = threading.Lock()


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    pending = path.with_suffix('.part')
    pending.write_text(json.dumps(value), encoding='utf-8')
    pending.chmod(0o600)
    pending.replace(path)


def opensubtitles_request(url, key, token='', data=None):
    headers = {'Api-Key': key, 'User-Agent': 'MediaServerPublished/1.0', 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def opensubtitles_login(credentials):
    result = opensubtitles_request('https://api.opensubtitles.com/api/v1/login',
                                   credentials['key'], data={'username': credentials['user'],
                                                             'password': credentials['password']})
    host = result.get('base_url', 'api.opensubtitles.com')
    if host != 'api.opensubtitles.com' and not host.endswith('.opensubtitles.com'):
        raise ValueError('Invalid OpenSubtitles API host')
    if not result.get('token'):
        raise ValueError('OpenSubtitles login failed')
    return host, result['token']


def opensubtitles_hash(file):
    size = file.stat().st_size
    if size < 131072:
        return None
    checksum = size
    with file.open('rb') as source:
        for offset in (0, size - 65536):
            source.seek(offset)
            block = source.read(65536)
            checksum = (checksum + sum(word[0] for word in struct.iter_unpack('<Q', block))) & (2**64 - 1)
    return f'{checksum:016x}'


def download_opensubtitles(server, file, relative, item_id):
    settings = read_json(server.settings_path)
    credentials = settings.get('opensubtitles')
    if not isinstance(credentials, dict) or not all(credentials.get(k) for k in ('key', 'user', 'password')):
        raise ValueError('Configure OpenSubtitles in Settings first')
    destination = file.with_name(file.stem + '.en.OpenSubtitles.srt')
    if destination.exists():
        return 'English subtitles already downloaded'
    host, token = opensubtitles_login(credentials)
    name, episode, series_key = media_description(file, relative)
    details = read_json(server.metadata_path).get(str(item_id), {})
    query = {'languages': 'en', 'order_by': 'download_count'}
    fingerprint = opensubtitles_hash(file)
    if fingerprint:
        query['moviehash'] = fingerprint
    if episode:
        query.update(query=details.get('series') or series_key or name,
                     season_number=int(episode[1]), episode_number=int(episode[2]))
    else:
        query['query'] = details.get('name') or clean_title(name) or name
    url = f'https://{host}/api/v1/subtitles?' + urllib.parse.urlencode(query)
    results = opensubtitles_request(url, credentials['key'], token).get('data', [])
    candidates = [row for row in results if row.get('attributes', {}).get('language') == 'en'
                  and not row.get('attributes', {}).get('foreign_parts_only')]
    chosen = next((row for row in candidates if row['attributes'].get('moviehash_match')), None)
    if chosen is None:
        chosen = next((row for row in candidates if row['attributes'].get('release', '').casefold() == file.stem.casefold()), None)
    if chosen is None:
        chosen = candidates[0] if candidates else None
    if chosen is None:
        raise ValueError('No English subtitles found for this video')
    file_id = chosen['attributes'].get('files', [{}])[0].get('file_id')
    if not isinstance(file_id, int):
        raise ValueError('Subtitle result has no downloadable file')
    result = opensubtitles_request(f'https://{host}/api/v1/download', credentials['key'], token,
                                   {'file_id': file_id, 'sub_format': 'srt'})
    link = result.get('link', '')
    parsed = urllib.parse.urlsplit(link)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.opensubtitles.com'):
        raise ValueError('Invalid subtitle download URL')
    with urllib.request.urlopen(link, timeout=25) as response:
        data = response.read(5_000_001)
    if len(data) > 5_000_000 or b'-->' not in data:
        raise ValueError('Invalid subtitle file')
    pending = destination.with_suffix('.srt.part')
    pending.write_bytes(data)
    pending.chmod(0o600)
    pending.replace(destination)
    return 'English subtitles downloaded'


def tmdb_request(credential, endpoint, **params):
    url = 'https://api.themoviedb.org/3/' + endpoint + '?' + urllib.parse.urlencode(params)
    headers = {'Accept': 'application/json', 'User-Agent': 'MediaServerPublished/1.0'}
    if len(credential) == 32 and re.fullmatch(r'[a-fA-F0-9]{32}', credential):
        url += '&api_key=' + credential
    else:
        headers['Authorization'] = 'Bearer ' + credential
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
        return json.load(response)


def clean_title(name):
    name = re.sub(r'(?i)[. _-]s\d{1,2}(?:e\d{1,3})?.*$', '', name)
    name = re.sub(r'(?i)\b(?:19|20)\d{2}\b.*$', '', name)
    name = re.sub(r'(?i)\b(?:1080p|720p|2160p|bluray|web[- .]?dl|x264|x265|hevc)\b.*$', '', name)
    return re.sub(r'[._]+', ' ', name).strip(' -()[]')


def media_description(file, relative):
    name = re.sub(r'[._]+', ' ', file.stem).strip()
    episode = re.search(r'(?i)S(\d{1,2})E(\d{1,3})', name)
    if not episode:
        episode = re.search(r'(?i)S(\d{1,2})[\s._-]*EP(?:ISODE)?[\s._-]*(\d{1,3})', name)
    if not episode:
        episode = re.search(r'(?i)\b(\d{1,2})x(\d{1,3})\b', name)
    if not episode and len(relative.parts) > 1:
        numbered = re.match(r'^\s*(\d{1,3})[. _-]+\S', file.stem)
        if numbered:
            try:
                numbered_siblings = sum(1 for sibling in file.parent.iterdir()
                                        if sibling.is_file() and sibling.suffix.lower() in VIDEO_EXTENSIONS
                                        and re.match(r'^\s*\d{1,3}[. _-]+\S', sibling.stem))
            except OSError:
                numbered_siblings = 0
            if numbered_siblings >= 2:
                season = 1
                for part in reversed(relative.parts[:-1]):
                    season_match = re.search(r'(?i)\b(?:season|series|s)[. _ -]*(\d{1,2})\b', part)
                    if season_match:
                        season = int(season_match[1])
                        break
                else:
                    volume = re.search(r'(?i)\b(?:720p|1080p|2160p|vol(?:ume)?|part)[. _-]*(\d{1,2})$', relative.parts[0])
                    if volume:
                        season = int(volume[1])
                episode = re.search(r'S(\d{1,2})E(\d{1,3})', f'S{season}E{numbered[1]}')
    series_key = clean_title(relative.parts[0] if episode and len(relative.parts) > 1 else file.stem) if episode else ''
    return name, episode, series_key


def override_key(kind, item_id, series_key=''):
    if kind == 'series':
        return 'series:' + hashlib.sha256(series_key.casefold().encode()).hexdigest()[:24]
    return f'item:{item_id}'


def qbit_opener(server):
    return integrations.qbit_opener(server)


def qbit_json(opener, root, headers, path):
    with opener.open(urllib.request.Request(root + path, headers=headers), timeout=10) as response:
        return json.load(response)


def qbit_post(opener, root, headers, path, payload):
    body = urllib.parse.urlencode(payload).encode()
    with opener.open(urllib.request.Request(root + path, data=body, headers=headers), timeout=10) as response:
        if response.status != 200:
            raise RuntimeError('qBittorrent rejected the change')


def qbit_activity(server):
    return integrations.qbit_activity(server)


def vpn_state(server):
    return integrations.vpn_state(server)


def switch_vpn(server, enabled):
    return integrations.switch_vpn(server, enabled)


def add_magnet(server, link):
    return integrations.add_magnet(server, link)


def detach_qbit_files(server, targets, kind, series_key):
    """Stop qBittorrent from restoring removed files before unlinking them."""
    if not read_json(server.settings_path).get('qbittorrent_url'):
        return
    opener, root, headers = qbit_opener(server)
    torrents = qbit_json(opener, root, headers, '/api/v2/torrents/info')
    target_paths = {file.resolve() for file in targets}
    for torrent in torrents:
        torrent_files = qbit_json(opener, root, headers, '/api/v2/torrents/files?hash=' + torrent['hash'])
        matched = []
        for entry in torrent_files:
            name = Path(entry['name'])
            if name.is_absolute() or '..' in name.parts:
                continue
            content = Path(torrent.get('content_path', ''))
            candidates = {Path(torrent['save_path']) / name, content / name, content.parent / name}
            if len(torrent_files) == 1:
                candidates.add(content)
            if any(path.resolve() in target_paths for path in candidates):
                matched.append(entry)
        # A whole-series deletion also removes an unfinished torrent for that series.
        same_series = (kind == 'series' and series_key and
                       clean_title(Path(torrent.get('content_path', '')).name).casefold() == series_key.casefold())
        video_files = [entry for entry in torrent_files if Path(entry['name']).suffix.lower() in VIDEO_EXTENSIONS]
        all_videos_matched = bool(video_files) and all(entry in matched for entry in video_files)
        if same_series or all_videos_matched:
            qbit_post(opener, root, headers, '/api/v2/torrents/delete',
                      {'hashes': torrent['hash'], 'deleteFiles': 'false'})
        elif matched:
            qbit_post(opener, root, headers, '/api/v2/torrents/filePrio',
                      {'hash': torrent['hash'], 'id': '|'.join(str(entry['index']) for entry in matched), 'priority': '0'})


def sidecars_for(file):
    result = []
    for folder in (file.parent, file.parent / 'Subs', file.parent.parent / 'Subs'):
        if folder.is_dir():
            result.extend(path for path in folder.iterdir() if path.is_file() and not path.is_symlink()
                          and (path.stem.casefold() == file.stem.casefold() or
                               path.stem.casefold().startswith(file.stem.casefold() + '.'))
                          and path.suffix.lower() in {'.idx', '.sub', '.srt', '.ass', '.ssa', '.vtt', '.mks'})
    return set(result)


def delete_media(server, kind, item_id):
    files = list(Handler.files_for(server.media_root))
    target = next(((file, relative) for media_item, file, relative in files if media_item == item_id), None)
    if target is None:
        raise FileNotFoundError('Media item not found')
    _, episode, series_key = media_description(*target)
    if kind == 'series':
        if not episode:
            raise ValueError('Not a series')
        targets = [(media_item, file, relative) for media_item, file, relative in files
                   if media_description(file, relative)[2] == series_key]
    elif kind == 'episode':
        if not episode:
            raise ValueError('Not an episode')
        targets = [(media_item, file, relative) for media_item, file, relative in files if media_item == item_id]
    elif kind == 'movie':
        if episode:
            raise ValueError('Not a movie')
        targets = [(media_item, file, relative) for media_item, file, relative in files if media_item == item_id]
    else:
        raise ValueError('Invalid kind')
    deletion_plan = []
    target_paths = {file.resolve() for _, file, _ in targets}
    cleanup_folders = set()
    for _, file, _ in targets:
        folder = file.parent
        if folder == server.media_root or not folder.is_relative_to(server.media_root):
            continue
        top_folder = server.media_root / folder.relative_to(server.media_root).parts[0]
        if (top_folder.is_symlink() or top_folder.is_junction() or
                any(path.is_symlink() or path.is_junction() for path in top_folder.rglob('*'))):
            continue
        videos = {path.resolve() for path in top_folder.rglob('*')
                  if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS}
        if videos and videos <= target_paths:
            cleanup_folders.add(top_folder)
    for media_item, file, relative in targets:
        stat = file.stat()
        legacy_keys = []
        for subtitle in [None, *subtitle_tracks(file)]:
            sub_key = '' if subtitle is None else f':{subtitle["id"]}:{subtitle["source"].stat().st_mtime_ns}'
            legacy_keys.append(hashlib.sha256(f'{relative}:{stat.st_size}:{stat.st_mtime_ns}{sub_key}'.encode()).hexdigest())
        deletion_plan.append((media_item, file, stat, legacy_keys, sidecars_for(file)))
    detach_qbit_files(server, [file for _, file, _ in targets], kind, series_key)
    metadata = read_json(server.metadata_path)
    overrides = read_json(server.overrides_path)
    for media_item, file, stat, legacy_keys, sidecars in deletion_plan:
        with CONVERSION_LOCK:
            DELETED_SOURCES.add((file.resolve(), stat.st_mtime_ns, stat.st_size))
        with HLS_LOCK:
            for key, job in list(HLS_JOBS.items()):
                process, directory = job['process'], job['directory']
                if directory.name.startswith('hls-' + str(media_item) + '-'):
                    if process.poll() is None:
                        process.terminate()
                    HLS_JOBS.pop(key, None)
        for sidecar in sidecars:
            if sidecar.resolve().is_relative_to(server.media_root):
                sidecar.unlink(missing_ok=True)
        file.unlink(missing_ok=True)
        metadata.pop(str(media_item), None)
        overrides.pop(override_key('episode', media_item), None)
        for cached in server.cache_root.glob(str(media_item) + '-*.mp4'):
            cached.unlink(missing_ok=True)
        for hls_dir in server.cache_root.glob('hls-' + str(media_item) + '-*'):
            if hls_dir.is_dir():
                shutil.rmtree(hls_dir)
        for key in legacy_keys:
            for suffix in ('.mp4', '.part'):
                (server.cache_root / (key + suffix)).unlink(missing_ok=True)
                (server.cache_root / (str(media_item) + '-' + key + suffix)).unlink(missing_ok=True)
    if kind == 'series':
        overrides.pop(override_key('series', item_id, series_key), None)
    write_json(server.metadata_path, metadata)
    write_json(server.overrides_path, overrides)
    if kind == 'movie':
        for folder in cleanup_folders:
            # Recheck after deleting the movie: a newly added video or link must
            # keep the folder, and the selected library itself is never removed.
            resolved = folder.resolve()
            if resolved == server.media_root or not resolved.is_relative_to(server.media_root):
                continue
            contents = list(folder.rglob('*'))
            if (folder.is_symlink() or folder.is_junction() or
                    any(path.is_symlink() or path.is_junction() or
                        (path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)
                        for path in contents)):
                continue
            shutil.rmtree(folder)
    return len(targets)


def match_media(server, kind, item_id, tmdb_id):
    found = next(((file, relative) for media_item, file, relative in Handler.files_for(server.media_root)
                  if media_item == item_id), None)
    if found is None:
        raise FileNotFoundError('Media item not found')
    file, relative = found
    _, episode, series_key = media_description(file, relative)
    if kind == 'movie' and episode or kind in {'series', 'episode'} and not episode:
        raise ValueError('Media type does not match')
    api_kind = 'movie' if kind == 'movie' else 'tv'
    credential = read_json(server.settings_path).get('tmdb_credential', '')
    if not credential:
        raise RuntimeError('TMDb credential is missing')
    data = tmdb_request(credential, f'{api_kind}/{tmdb_id}', language='en-US')
    image = data.get('backdrop_path') or data.get('poster_path')
    year_field = 'release_date' if kind == 'movie' else 'first_air_date'
    details = {'match': 'manual', 'tmdbId': tmdb_id, 'overview': data.get('overview') or '',
               'artwork': 'https://image.tmdb.org/t/p/w780' + image if image else '',
               'year': int((data.get(year_field) or '0')[:4] or 0),
               'genre': (data.get('genres') or [{}])[0].get('name', '')}
    overrides = read_json(server.overrides_path)
    if kind == 'movie':
        details['name'] = data.get('title') or file.stem
        overrides[override_key(kind, item_id)] = details
    else:
        details.update({'series': data.get('name') or series_key,
                        'seriesOverview': data.get('overview') or '',
                        'yearEnd': int((data.get('last_air_date') or '0')[:4] or 0),
                        'totalSeasons': data.get('number_of_seasons') or 0})
        overrides[override_key('series', item_id, series_key)] = details
        if kind == 'episode':
            show_episode = tmdb_request(credential,
                                        f'tv/{tmdb_id}/season/{int(episode[1])}/episode/{int(episode[2])}',
                                        language='en-US')
            still = show_episode.get('still_path')
            overrides[override_key(kind, item_id)] = {
                'match': 'manual', 'name': show_episode.get('name') or file.stem,
                'overview': show_episode.get('overview') or '',
                'artwork': 'https://image.tmdb.org/t/p/w780' + still if still else details['artwork']}
        metadata = read_json(server.metadata_path)
        for media_item, candidate, candidate_relative in Handler.files_for(server.media_root):
            if media_description(candidate, candidate_relative)[2] == series_key:
                metadata.pop(str(media_item), None)
        write_json(server.metadata_path, metadata)
    write_json(server.overrides_path, overrides)
    if kind != 'movie':
        schedule_metadata(server)


def rename_media(server, kind, item_id, title):
    found = next(((file, relative) for media_item, file, relative in Handler.files_for(server.media_root)
                  if media_item == item_id), None)
    if found is None:
        raise FileNotFoundError('Media item not found')
    _, episode, series_key = media_description(*found)
    if kind == 'movie' and episode or kind in {'series', 'episode'} and not episode:
        raise ValueError('Media type does not match')
    key = override_key(kind, item_id, series_key)
    overrides = read_json(server.overrides_path)
    details = overrides.get(key, {})
    details['series' if kind == 'series' else 'name'] = title
    overrides[key] = details
    write_json(server.overrides_path, overrides)


def refresh_metadata(server):
    settings = read_json(server.settings_path)
    credential = settings.get('tmdb_credential', '')
    if not credential:
        return
    cache = read_json(server.metadata_path)
    overrides = read_json(server.overrides_path)
    for item_id, file, relative in Handler.files_for(server.media_root):
        if server.stopping.is_set():
            return
        key = str(item_id)
        if key in cache:
            continue
        name, episode, series_key = media_description(file, relative)
        kind = 'tv' if episode else 'movie'
        series = clean_title(relative.parts[0] if episode and len(relative.parts) > 1 else file.stem)
        if not series:
            continue
        try:
            manual = overrides.get(override_key('series' if episode else 'movie', item_id, series_key), {})
            if manual.get('match') == 'local':
                cache[key] = {'match': 'local'}
                with METADATA_LOCK:
                    write_json(server.metadata_path, cache)
                continue
            if manual.get('tmdbId'):
                match = {'id': manual['tmdbId']}
            else:
                results = tmdb_request(credential, f'search/{kind}', query=series, language='en-US').get('results', [])
                scored = sorted(((difflib.SequenceMatcher(None, series.casefold(),
                              str(hit.get('name' if episode else 'title', '')).casefold()).ratio(), hit)
                              for hit in results), key=lambda pair: pair[0], reverse=True)
                if not scored or scored[0][0] < .78:
                    cache[key] = {'match': 'unmatched'}
                    continue
                match = scored[0][1]
            data = tmdb_request(credential, f'{kind}/{match["id"]}', language='en-US')
            detail = {}
            if episode:
                detail = tmdb_request(credential, f'tv/{match["id"]}/season/{int(episode[1])}/episode/{int(episode[2])}', language='en-US')
            image = detail.get('still_path') or data.get('backdrop_path') or data.get('poster_path')
            cache[key] = {'match': 'matched', 'overview': detail.get('overview') or data.get('overview') or '',
                          'seriesOverview': data.get('overview') or '',
                          'name': detail.get('name') or data.get('title') or name,
                          'series': data.get('name') or '',
                          'artwork': 'https://image.tmdb.org/t/p/w780' + image if image else '',
                          'year': int((data.get('first_air_date' if episode else 'release_date') or '0')[:4] or 0),
                          'yearEnd': int((data.get('last_air_date') or '0')[:4] or 0) if episode else 0,
                          'totalSeasons': data.get('number_of_seasons', 0) if episode else 0,
                          'seasonEpisodes': next((s.get('episode_count', 0) for s in data.get('seasons', [])
                                                  if s.get('season_number') == int(episode[1])), 0) if episode else 0,
                          'genre': (data.get('genres') or [{}])[0].get('name', '')}
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            continue
        with METADATA_LOCK:
            write_json(server.metadata_path, cache)


def schedule_metadata(server):
    global METADATA_JOB
    with METADATA_LOCK:
        if METADATA_JOB is None or METADATA_JOB.done():
            METADATA_JOB = METADATA.submit(refresh_metadata, server)


def media_id(relative_path):
    return int.from_bytes(hashlib.sha256(str(relative_path).encode()).digest()[:6], 'big') + 10**12


def probe(file):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,pix_fmt,width,height:format=duration',
         '-of', 'json', str(file)], capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(result.stdout)
    streams = data['streams']
    video = next(stream for stream in streams if stream.get('codec_type') == 'video')
    video['duration'] = float(data.get('format', {}).get('duration') or 0)
    audio = next((stream for stream in streams if stream.get('codec_type') == 'audio'), None)
    return video, audio


def audio_tracks(file):
    if not shutil.which('ffprobe'):
        return []
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries',
         'stream=index,codec_type,codec_name,channels:stream_tags=language,title:stream_disposition=default',
         '-of', 'json', str(file)], capture_output=True, text=True, timeout=30, check=True,
    )
    tracks = []
    for ordinal, stream in enumerate(s for s in json.loads(result.stdout)['streams']
                                     if s.get('codec_type') == 'audio'):
        tags = stream.get('tags', {})
        tracks.append({'id': f'a{ordinal}', 'index': stream['index'], 'ordinal': ordinal,
                       'codec': stream.get('codec_name', ''), 'channels': stream.get('channels'),
                       'language': tags.get('language', ''), 'title': tags.get('title', ''),
                       'default': bool(stream.get('disposition', {}).get('default'))})
    for track in tracks:
        track['only'] = len(tracks) == 1
    return tracks


def subtitle_tracks(file):
    """Find embedded tracks and same-episode subtitle files, including VobSub pairs."""
    tracks = []
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'stream=index,codec_type,codec_name:stream_tags=language,title',
             '-of', 'json', str(file)], capture_output=True, text=True, timeout=30, check=True)
        for ordinal, stream in enumerate(s for s in json.loads(result.stdout)['streams']
                                         if s.get('codec_type') == 'subtitle'):
            tracks.append({'id': f'i{ordinal}', 'source': file, 'index': stream['index'],
                           'ordinal': ordinal, 'codec': stream.get('codec_name', ''),
                           'label': stream.get('tags', {}).get('title') or
                           stream.get('tags', {}).get('language') or f'Embedded {ordinal + 1}'})
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass
    candidates = []
    for folder in (file.parent, file.parent / 'Subs', file.parent.parent / 'Subs'):
        if folder.is_dir():
            candidates.extend(p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                              and (p.stem.casefold() == file.stem.casefold() or
                                   p.stem.casefold().startswith(file.stem.casefold() + '.'))
                              and p.suffix.lower() in {'.idx', '.srt', '.ass', '.ssa', '.vtt', '.mks'})
    for number, path in enumerate(sorted(set(candidates))):
        if path.suffix.lower() == '.idx' and not path.with_suffix('.sub').is_file():
            continue
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                                     'stream=index,codec_type,codec_name:stream_tags=language,title',
                                     '-of', 'json', str(path)], capture_output=True, text=True,
                                    timeout=30, check=True)
            streams = [s for s in json.loads(result.stdout)['streams'] if s.get('codec_type') == 'subtitle']
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            continue
        for ordinal, stream in enumerate(streams):
            tracks.append({'id': f'x{number}_{ordinal}', 'source': path,
                           'index': stream['index'], 'ordinal': ordinal,
                           'codec': stream.get('codec_name', ''),
                           'label': ('English - OpenSubtitles' if path.name.endswith('.en.OpenSubtitles.srt') else None) or
                           stream.get('tags', {}).get('title') or
                           stream.get('tags', {}).get('language') or f'External {number + 1}'})
    return tracks


def ffmpeg_filter_path(path):
    return str(path).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'").replace('[', '\\[').replace(']', '\\]')


def compatible_source(file, video, audio):
    return (file.suffix.lower() in {'.mp4', '.m4v'}
            and video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p'
            and (audio is None or audio.get('codec_name') in {'aac', 'mp3'}))


def convert(file, destination, video, audio, subtitle=None, audio_track=None):
    source_stat = file.stat()
    source_key = (file.resolve(), source_stat.st_mtime_ns, source_stat.st_size)
    pending = destination.with_suffix('.part')
    pending.unlink(missing_ok=True)
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(file)]
    if subtitle and subtitle['source'] != file:
        command += ['-i', str(subtitle['source'])]
    video_map = '0:v:0'
    width, height = int(video['width']), int(video['height'])
    factor = min(1, 1920 / width, 1080 / height)
    output_width = max(2, int(width * factor) // 2 * 2)
    output_height = max(2, int(height * factor) // 2 * 2)
    if subtitle:
        if subtitle['codec'] in {'dvd_subtitle', 'hdmv_pgs_subtitle', 'dvb_subtitle', 'xsub'}:
            input_number = 0 if subtitle['source'] == file else 1
            graph = f'[0:v:0][{input_number}:{subtitle["index"]}]overlay[v]'
        else:
            path = ffmpeg_filter_path(subtitle['source'])
            graph = f"[0:v:0]subtitles='{path}':si={subtitle['ordinal']}[v]"
        if factor < 1:
            graph += f';[v]scale={output_width}:{output_height}[out]'
            video_map = '[out]'
        else:
            video_map = '[v]'
        command += ['-filter_complex', graph]
    command += ['-map', video_map, '-map',
                f'0:{audio_track["index"]}' if audio_track else '0:a:0?', '-sn', '-dn']
    if not subtitle and video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p':
        command += ['-c:v', 'copy']
    else:
        command += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
                    '-threads', '2']
        if not subtitle:
            command += ['-vf', f'scale={output_width}:{output_height}']
    selected_audio = audio_track or audio
    command += ['-c:a', 'copy' if selected_audio and selected_audio.get('codec_name') == 'aac' else 'aac']
    if not selected_audio or selected_audio.get('codec_name') != 'aac':
        command += ['-b:a', '160k', '-ac', '2']
    command += ['-movflags', '+faststart', '-f', 'mp4', str(pending)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=6 * 3600)
        if result.returncode or not pending.is_file() or pending.stat().st_size == 0:
            raise RuntimeError(result.stderr[-400:] or 'Video conversion failed.')
        with CONVERSION_LOCK:
            if source_key in DELETED_SOURCES or not file.is_file():
                return
            pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)


def playable(file, relative, cache_root, subtitle=None, audio_track=None):
    if not shutil.which('ffprobe') and not subtitle and not audio_track and file.suffix.lower() in {'.mp4', '.m4v', '.webm', '.ogv'}:
        return 'ready', file
    if not shutil.which('ffprobe') and not subtitle and not audio_track and file.suffix.lower() in {'.mp4', '.m4v', '.webm', '.ogv'}:
        return 'ready', 'mp4', None, 0, 0
    try:
        video, audio = probe(file)
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
        return 'error', None
    if subtitle is None and (audio_track is None or audio_track['only']) and compatible_source(file, video, audio):
        return 'ready', file
    stat = file.stat()
    sub_key = '' if subtitle is None else f':{subtitle["id"]}:{subtitle["source"].stat().st_mtime_ns}'
    audio_key = '' if audio_track is None else f':audio:{audio_track["index"]}'
    key = hashlib.sha256(f'{relative}:{stat.st_size}:{stat.st_mtime_ns}{sub_key}{audio_key}'.encode()).hexdigest()
    destination = cache_root / (str(media_id(relative)) + '-' + key + '.mp4')
    if destination.is_file() and destination.stat().st_size:
        return 'ready', destination
    with CONVERSION_LOCK:
        job = CONVERSION_JOBS.get(key)
        if job is None:
            job = CONVERSIONS.submit(convert, file, destination, video, audio, subtitle, audio_track)
            CONVERSION_JOBS[key] = job
    if job.done():
        try:
            job.result()
        except Exception:
            return 'error', None
        return 'ready', destination if destination.is_file() else None
    return 'preparing', None


def hls_command(file, directory, video, audio, subtitle, audio_track=None, start=0):
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y']
    if start:
        command += ['-ss', str(start)]
    command += ['-i', str(file)]
    if subtitle and subtitle['source'] != file:
        if start:
            command += ['-ss', str(start)]
        command += ['-i', str(subtitle['source'])]
    width, height = int(video['width']), int(video['height'])
    factor = min(1, 1920 / width, 1080 / height)
    output_width = max(2, int(width * factor) // 2 * 2)
    output_height = max(2, int(height * factor) // 2 * 2)
    video_map = '0:v:0'
    if subtitle:
        if subtitle['codec'] in {'dvd_subtitle', 'hdmv_pgs_subtitle', 'dvb_subtitle', 'xsub'}:
            input_number = 0 if subtitle['source'] == file else 1
            graph = f'[0:v:0][{input_number}:{subtitle["index"]}]overlay[v]'
        else:
            path = ffmpeg_filter_path(subtitle['source'])
            if start:
                graph = (f"[0:v:0]setpts=PTS+{start}/TB,subtitles='{path}':si={subtitle['ordinal']},"
                         f"setpts=PTS-{start}/TB[v]")
            else:
                graph = f"[0:v:0]subtitles='{path}':si={subtitle['ordinal']}[v]"
        if factor < 1:
            graph += f';[v]scale={output_width}:{output_height}[out]'
            video_map = '[out]'
        else:
            video_map = '[v]'
        command += ['-filter_complex', graph]
    command += ['-map', video_map, '-map',
                f'0:{audio_track["index"]}' if audio_track else '0:a:0?', '-sn', '-dn']
    copy_video = (start == 0 and audio_track is not None and not subtitle
                  and compatible_source(file, video, audio))
    if copy_video:
        command += ['-c:v', 'copy']
    else:
        command += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-pix_fmt', 'yuv420p',
                    '-threads', '2', '-g', '48', '-sc_threshold', '0']
    if not subtitle and not copy_video:
        command += ['-vf', f'scale={output_width}:{output_height}']
    command += ['-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-f', 'hls',
                '-hls_time', '2', '-hls_list_size', '0', '-hls_playlist_type', 'event',
                '-hls_flags', 'independent_segments+temp_file',
                '-hls_segment_filename', str(directory / 'seg_%06d.ts'),
                str(directory / 'index.m3u8')]
    return command


def hls_state(file, relative, cache_root, subtitle=None, audio_track=None, start=0):
    if not shutil.which('ffprobe') and not subtitle and not audio_track and file.suffix.lower() in {'.mp4', '.m4v', '.webm', '.ogv'}:
        return 'ready', 'mp4', None, 0, 0
    try:
        video, audio = probe(file)
        duration = video['duration']
        start = min(max(0, int(start)), max(0, int(duration) - 5)) if duration else 0
        if subtitle is None and (audio_track is None or audio_track['only']) and compatible_source(file, video, audio):
            return 'ready', 'mp4', None, duration, 0
        stat = file.stat()
        sub_key = '' if subtitle is None else f':{subtitle["id"]}:{subtitle["source"].stat().st_mtime_ns}'
        audio_key = '' if audio_track is None else f':audio:{audio_track["index"]}'
        key = hashlib.sha256(f'{relative}:{stat.st_size}:{stat.st_mtime_ns}{sub_key}{audio_key}:start:{start}'.encode()).hexdigest()
        directory = cache_root / ('hls-' + str(media_id(relative)) + '-' + key)
        playlist = directory / 'index.m3u8'
        with HLS_LOCK:
            job = HLS_JOBS.get(key)
            if job:
                process = job['process']
                if process.poll() is not None and process.returncode:
                    HLS_JOBS.pop(key, None)
                    shutil.rmtree(directory, ignore_errors=True)
                    return 'error', 'hls', None, duration, start
                job['last_access'] = time.monotonic()
            if playlist.is_file():
                text = playlist.read_text(errors='replace')
                if '#EXTINF:' in text:
                    return 'ready', 'hls', directory, duration, start
            if job and process.poll() is not None:
                HLS_JOBS.pop(key, None)
                shutil.rmtree(directory, ignore_errors=True)
                return 'error', 'hls', None, duration, start
            if not job and directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            if key not in HLS_JOBS:
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / 'ffmpeg.log').open('wb') as log:
                    process = subprocess.Popen(hls_command(file, directory, video, audio, subtitle, audio_track, start),
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=log)
                HLS_JOBS[key] = {'process': process, 'directory': directory,
                                 'last_access': time.monotonic(), 'file': file,
                                 'relative': relative, 'cache_root': cache_root}
        return 'preparing', 'hls', directory, duration, start
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
        return 'error', 'hls', None, 0, 0


def reap_hls_jobs(server):
    while not server.stopping.wait(15):
        with HLS_LOCK:
            for key, job in list(HLS_JOBS.items()):
                process, directory, last_access = job['process'], job['directory'], job['last_access']
                if time.monotonic() - last_access <= HLS_IDLE_TIMEOUT:
                    continue
                HLS_JOBS.pop(key, None)
                if process.poll() is None:
                    process.terminate()
                shutil.rmtree(directory, ignore_errors=True)


def stream_jobs():
    with HLS_LOCK:
        result = []
        for key, job in HLS_JOBS.items():
            segments = list(job['directory'].glob('seg_*.ts'))
            returncode = job['process'].poll()
            result.append({'key': key, 'name': job['file'].stem,
                           'status': 'preparing' if returncode is None else ('ready' if returncode == 0 else 'failed'),
                           'seconds': len(segments) * 2,
                           'bytes': sum(segment.stat().st_size for segment in segments if segment.is_file())})
    return result


def stop_stream(key):
    with HLS_LOCK:
        job = HLS_JOBS.pop(key, None)
        if job is None:
            return False
        process = job['process']
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        shutil.rmtree(job['directory'], ignore_errors=True)
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        super().log_message('%s %s', self.command, urlsplit(self.path).path)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', self.server.origin)
        self.send_header('Vary', 'Origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'private, no-store')
        super().end_headers()

    def do_OPTIONS(self):
        path = urlsplit(self.path).path
        if (path not in {'/api/settings', '/api/manage', '/api/streams', '/api/torrent', '/api/torrent-link', '/api/progress'}
                and not re.fullmatch(r'/api/subtitles/\d{12,16}', path)) or self.headers.get('Origin') != self.server.origin:
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def authorized(self, scope, item=''):
        query = parse_qs(urlsplit(self.path).query)
        ticket = query.get('ticket', [''])[0]
        match = re.fullmatch(r'(\d{10})\.([a-f0-9]{64})', ticket)
        if not match or int(match[1]) < time.time() or int(match[1]) > time.time() + 12 * 3600 + 60:
            return False
        message = f'{match[1]}:{scope}:{item}'.encode()
        expected = hmac.new(self.server.secret, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, match[2])

    @staticmethod
    def files_for(root):
        for file in root.rglob('*'):
            if file.is_file() and file.suffix.lower() in VIDEO_EXTENSIONS and not file.is_symlink() and file.resolve().is_relative_to(root.resolve()):
                relative = file.relative_to(root)
                if any(part.startswith('.') for part in relative.parts):
                    continue
                yield media_id(relative), file, relative

    def files(self):
        return self.files_for(self.server.media_root)

    def do_HEAD(self):
        self.handle_get(True)

    def do_POST(self):
        if urlsplit(self.path).path == '/api/progress':
            if self.headers.get('Origin') != self.server.origin or not self.authorized('progress', 'local'):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 131072:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                if data.get('action') == 'clear':
                    with PROGRESS_LOCK:
                        write_json(self.server.progress_path, {'__clearedAt': int(time.time() * 1000)})
                else:
                    records = data.get('records')
                    if not isinstance(records, dict) or len(records) > 1000:
                        raise ValueError()
                    now = int(time.time() * 1000)
                    clean = {}
                    for item, record in records.items():
                        if not re.fullmatch(r'\d{12,16}', item) or not isinstance(record, dict):
                            raise ValueError()
                        position, duration, updated = (record.get(key) for key in ('position', 'duration', 'updated'))
                        if (not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                                    and math.isfinite(value)
                                    for value in (position, duration, updated))
                                or not 0 <= position <= duration <= 86400
                                or not now - 365 * 86400000 <= updated <= now + 300000):
                            raise ValueError()
                        clean[item] = {'position': round(position, 2), 'duration': round(duration, 2),
                                       'updated': int(updated)}
                    with PROGRESS_LOCK:
                        stored = read_json(self.server.progress_path)
                        for item, record in clean.items():
                            if (record['updated'] > stored.get('__clearedAt', 0)
                                    and record['updated'] >= stored.get(item, {}).get('updated', 0)):
                                stored[item] = record
                        write_json(self.server.progress_path, stored)
            except (OSError, ValueError, TypeError, AttributeError):
                self.send_error(400)
                return
            self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        subtitle_match = re.fullmatch(r'/api/subtitles/(\d{12,16})', urlsplit(self.path).path)
        if subtitle_match:
            item = subtitle_match[1]
            if self.headers.get('Origin') != self.server.origin or not self.authorized('stream', item):
                self.send_error(401)
                return
            found = next(((file, relative) for item_id, file, relative in self.files()
                          if str(item_id) == item), None)
            if found is None:
                self.send_error(404)
                return
            try:
                with SUBTITLE_LOCK:
                    message = download_opensubtitles(self.server, *found, item)
            except (OSError, ValueError, KeyError, IndexError, urllib.error.URLError) as error:
                self.send_error(400 if isinstance(error, ValueError) else 503, str(error))
                return
            body = json.dumps({'message': message}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if urlsplit(self.path).path == '/api/streams':
            if self.headers.get('Origin') != self.server.origin or not self.authorized('settings'):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 128:
                    raise ValueError()
                key = json.loads(self.rfile.read(size)).get('key')
                if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                self.send_error(400)
                return
            if not stop_stream(key):
                self.send_error(404)
                return
            self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if urlsplit(self.path).path == '/api/manage':
            if self.headers.get('Origin') != self.server.origin or not self.authorized('manage'):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 2048:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(size))
                action, kind = data.get('action'), data.get('kind')
                if kind not in {'movie', 'series', 'episode'} or action not in {'rename', 'match', 'delete'}:
                    raise ValueError('Invalid action')
                item_id = data.get('id')
                if not isinstance(item_id, int) or not 10**12 <= item_id < 10**16:
                    raise ValueError('Invalid media ID')
                with MANAGE_LOCK:
                    if action == 'rename':
                        title = data.get('title', '').strip()
                        if not isinstance(title, str) or not 1 <= len(title) <= 140:
                            raise ValueError('Invalid title')
                        rename_media(self.server, kind, item_id, title)
                    elif action == 'match':
                        tmdb_id = data.get('tmdbId')
                        if not isinstance(tmdb_id, int) or not 1 <= tmdb_id < 10**9:
                            raise ValueError('Invalid TMDb ID')
                        match_media(self.server, kind, item_id, tmdb_id)
                    else:
                        count = delete_media(self.server, kind, item_id)
            except FileNotFoundError:
                self.send_error(404, 'Media item no longer exists')
                return
            except (OSError, ValueError, TypeError, RuntimeError, urllib.error.URLError) as error:
                self.send_error(503 if isinstance(error, (OSError, RuntimeError, urllib.error.URLError)) else 400,
                                str(error))
                return
            body = json.dumps({'deleted': count} if action == 'delete' else {'ok': True}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if urlsplit(self.path).path == '/api/settings':
            if self.headers.get('Origin') != self.server.origin or not self.authorized('settings'):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 4096:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                action = data.get('action')
                if action == 'restart-server':
                    command_id = data.get('id', '')
                    if not isinstance(command_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', command_id):
                        raise ValueError()
                    request_path = self.server.cache_root / 'restart-request.json'
                    write_json(request_path, {'id': command_id, 'created': int(time.time() * 1000)})
                elif action == 'credential':
                    credential = data.get('credential', '').strip()
                    if not isinstance(credential, str) or len(credential) > 512:
                        raise ValueError()
                    if credential:
                        tmdb_request(credential, 'configuration')
                    settings = read_json(self.server.settings_path)
                    settings['tmdb_credential'] = credential
                    write_json(self.server.settings_path, settings)
                    write_json(self.server.metadata_path, {})
                    schedule_metadata(self.server)
                elif action == 'opensubtitles':
                    key, user, password = (data.get(name) for name in ('key', 'user', 'password'))
                    if not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in (key, user, password)):
                        raise ValueError('Invalid OpenSubtitles credentials')
                    credentials = {'key': key.strip(), 'user': user.strip(), 'password': password}
                    opensubtitles_login(credentials)
                    settings = read_json(self.server.settings_path)
                    settings['opensubtitles'] = credentials
                    write_json(self.server.settings_path, settings)
                elif action == 'refresh':
                    write_json(self.server.metadata_path, {})
                    schedule_metadata(self.server)
                elif action == 'clear-artwork':
                    cache = read_json(self.server.metadata_path)
                    for details in cache.values():
                        details.pop('artwork', None)
                    write_json(self.server.metadata_path, cache)
                elif action == 'vpn':
                    enabled = data.get('enabled')
                    if not isinstance(enabled, bool):
                        raise ValueError()
                    switch_vpn(self.server, enabled)
                else:
                    raise ValueError()
            except (OSError, ValueError, TypeError, RuntimeError, urllib.error.URLError,
                    subprocess.SubprocessError) as error:
                self.send_error(400 if isinstance(error, (ValueError, TypeError)) else 503)
                return
            self.send_response(204)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if urlsplit(self.path).path == '/api/torrent-link':
            if self.headers.get('Origin') != self.server.origin or not self.authorized('upload'):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 4096:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                add_magnet(self.server, data.get('link'))
            except (OSError, ValueError, TypeError, RuntimeError, urllib.error.URLError,
                    subprocess.SubprocessError) as error:
                self.send_error(400 if isinstance(error, (ValueError, TypeError)) else 503)
                return
            self.send_response(201)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if urlsplit(self.path).path != '/api/torrent':
            self.send_error(404)
            return
        if self.headers.get('Origin') != self.server.origin or not self.authorized('upload'):
            self.send_error(401)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_error(400)
            return
        if not 1 <= size <= 5 * 1024 * 1024:
            self.send_error(413)
            return
        payload = self.rfile.read(size)
        if len(payload) != size or not payload.startswith(b'd') or b'4:info' not in payload:
            self.send_error(400)
            return
        try:
            if vpn_state(self.server) != 'connected' or not qbit_activity(self.server)[1]:
                self.send_error(503, 'VPN or qBittorrent binding unavailable')
                return
        except (OSError, ValueError, RuntimeError, urllib.error.URLError, subprocess.SubprocessError):
            self.send_error(503, 'VPN or qBittorrent unavailable')
            return
        destination = self.server.watch_root / (secrets.token_hex(16) + '.torrent')
        try:
            with destination.open('xb') as target:
                target.write(payload)
        except OSError:
            self.send_error(503)
            return
        self.send_response(201)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        self.handle_get(False)

    def handle_get(self, head):
        path = urlsplit(self.path).path
        if path == '/api/progress':
            if not self.authorized('progress', 'local'):
                self.send_error(401)
                return
            with PROGRESS_LOCK:
                stored = read_json(self.server.progress_path)
                body = json.dumps({'account': 'local', 'clearEpoch': stored.get('__clearedAt', 0),
                                   'records': {key: value for key, value in stored.items()
                                               if re.fullmatch(r'\d{12,16}', key)}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path == '/api/streams':
            if not self.authorized('settings'):
                self.send_error(401)
                return
            body = json.dumps(stream_jobs()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path == '/api/tmdb/search':
            if not self.authorized('manage'):
                self.send_error(401)
                return
            query = parse_qs(urlsplit(self.path).query)
            kind = query.get('kind', [''])[0]
            term = query.get('q', [''])[0].strip()
            if kind not in {'movie', 'series', 'episode'} or not 2 <= len(term) <= 120:
                self.send_error(400)
                return
            credential = read_json(self.server.settings_path).get('tmdb_credential', '')
            if not credential:
                self.send_error(503, 'TMDb is not configured')
                return
            try:
                results = tmdb_request(credential, 'search/movie' if kind == 'movie' else 'search/tv',
                                       query=term, language='en-US').get('results', [])[:12]
            except (OSError, ValueError, urllib.error.URLError):
                self.send_error(503, 'TMDb search unavailable')
                return
            body = json.dumps([{'id': row['id'], 'title': row.get('title') or row.get('name') or '',
                                'year': (row.get('release_date') or row.get('first_air_date') or '')[:4],
                                'overview': row.get('overview') or '',
                                'poster': 'https://image.tmdb.org/t/p/w185' + row['poster_path']
                                if row.get('poster_path') else ''} for row in results]).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path == '/api/settings':
            if not self.authorized('settings'):
                self.send_error(401)
                return
            settings = read_json(self.server.settings_path)
            try:
                downloading, qbit_bound = qbit_activity(self.server)
            except (OSError, ValueError, RuntimeError, urllib.error.URLError):
                downloading, qbit_bound = None, False
            try:
                vpn = vpn_state(self.server)
            except (OSError, ValueError, subprocess.SubprocessError):
                vpn = 'unknown'
            body = json.dumps({'tmdbConfigured': bool(settings.get('tmdb_credential')),
                               'openSubtitlesConfigured': bool(settings.get('opensubtitles')),
                               'metadataRunning': METADATA_JOB is not None and not METADATA_JOB.done(),
                               'storageBytes': shutil.disk_usage(self.server.media_root).free,
                               'vpnState': vpn, 'downloadingCount': downloading,
                               'qbitBound': qbit_bound}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path == '/api/library':
            if not self.authorized('library'):
                self.send_error(401)
                return
            result = []
            metadata = read_json(self.server.metadata_path)
            overrides = read_json(self.server.overrides_path)
            prior_ids = {}
            for old_id, new_id in read_json(self.server.cache_root / 'replacements.json').items():
                prior_ids.setdefault(str(new_id), []).append(old_id)
            for item_id, file, relative in self.files():
                try:
                    stat = file.stat()
                except OSError:
                    continue
                name, episode, series_key = media_description(file, relative)
                year = re.search(r'\b(?:19|20)\d{2}\b', name)
                details = {**metadata.get(str(item_id), {}), **overrides.get(override_key('episode', item_id), {})}
                series_override = overrides.get(override_key('series', item_id, series_key), {}) if episode else {}
                result.append({'id': item_id, 'priorIds': prior_ids.get(str(item_id), []),
                               'name': details.get('name', name),
                               'series': series_override.get('series') or details.get('series') or (series_key if episode else name),
                               'seriesArtwork': series_override.get('artwork') or details.get('artwork', ''),
                               'match': series_override.get('match') or details.get('match', 'pending'),
                               'kind': 'tv' if episode else 'movie', 'season': int(episode[1]) if episode else None,
                               'episode': int(episode[2]) if episode else None,
                               'year': series_override.get('year') or details.get('year') or (int(year[0]) if year else None),
                               'yearEnd': series_override.get('yearEnd') or details.get('yearEnd'),
                               'totalSeasons': series_override.get('totalSeasons') or details.get('totalSeasons'),
                               'seasonEpisodes': details.get('seasonEpisodes'), 'size': stat.st_size,
                               'overview': details.get('overview', ''),
                               'seriesOverview': series_override.get('seriesOverview') or details.get('seriesOverview', ''),
                               'artwork': details.get('artwork') or series_override.get('artwork', ''),
                               'genre': series_override.get('genre') or details.get('genre', ''),
                               'added_at': int(stat.st_mtime), 'progress': 0, 'duration': 0})
            result.sort(key=lambda item: item['added_at'], reverse=True)
            if (any(str(item['id']) not in metadata for item in result)
                    and read_json(self.server.settings_path).get('tmdb_credential')):
                schedule_metadata(self.server)
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        hls_match = re.fullmatch(r'/api/hls/(\d{12,16})/([a-f0-9]{64})/(index\.m3u8|seg_\d{6}\.ts)', path)
        if hls_match:
            item, key, name = hls_match.groups()
            if not self.authorized('stream', item):
                self.send_error(401)
                return
            if not any(str(item_id) == item for item_id, _, _ in self.files()):
                self.send_error(404)
                return
            directory = self.server.cache_root / ('hls-' + item + '-' + key)
            source = directory / name
            with HLS_LOCK:
                job = HLS_JOBS.get(key)
                if job:
                    job['last_access'] = time.monotonic()
            if not source.is_file():
                self.send_error(404)
                return
            if name == 'index.m3u8':
                ticket = parse_qs(urlsplit(self.path).query)['ticket'][0]
                body = '\n'.join(line + '?ticket=' + urllib.parse.quote(ticket)
                                 if re.fullmatch(r'seg_\d{6}\.ts', line) else line
                                 for line in source.read_text().splitlines()).encode()
                mime = 'application/vnd.apple.mpegurl'
            else:
                body = source.read_bytes()
                mime = 'video/mp2t'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        match = re.fullmatch(r'/api/(playable|stream|subtitles|audio)/(\d{12,16})', path)
        if not match:
            self.send_error(404)
            return
        action, item = match.groups()
        if not self.authorized('stream', item):
            self.send_error(401)
            return
        found = next(((file, relative) for item_id, file, relative in self.files() if str(item_id) == item), None)
        if found is None:
            self.send_error(404)
            return
        file, relative = found
        query = parse_qs(urlsplit(self.path).query)
        tracks = subtitle_tracks(file) if action == 'subtitles' or 'sub' in query else []
        if action == 'audio' or 'audio' in query:
            try:
                audio = audio_tracks(file)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                self.send_error(500)
                return
        else:
            audio = []
        if action == 'audio':
            body = json.dumps([{key: t[key] for key in ('id', 'language', 'title', 'codec', 'channels', 'default')}
                               for t in audio]).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if action == 'subtitles':
            body = json.dumps([{'id': t['id'], 'label': t['label']} for t in tracks]).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        selected = None
        if 'sub' in query:
            selected = next((t for t in tracks if t['id'] == query['sub'][0]), None)
            if selected is None:
                self.send_error(400)
                return
        selected_audio = None
        if 'audio' in query:
            selected_audio = next((t for t in audio if t['id'] == query['audio'][0]), None)
            if selected_audio is None:
                self.send_error(400)
                return
        if action == 'playable':
            try:
                start = float(query.get('start', ['0'])[0])
                if not 0 <= start <= 86400:
                    raise ValueError()
            except ValueError:
                self.send_error(400)
                return
            state, mode, directory, duration, actual_start = hls_state(
                file, relative, self.server.cache_root, selected, selected_audio, start)
            body = json.dumps({'status': state, 'format': mode,
                               'hlsKey': directory.name.rsplit('-', 1)[-1] if directory else None,
                               'duration': duration, 'start': actual_start}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if parse_qs(urlsplit(self.path).query).get('web') == ['1']:
            state, converted = playable(file, relative, self.server.cache_root, selected, selected_audio)
            if state != 'ready' or converted is None:
                self.send_error(503)
                return
            file = converted
        try:
            size = file.stat().st_size
            start, end = 0, size - 1
            requested = self.headers.get('Range')
            if requested:
                range_match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                if not range_match or not any(range_match.groups()):
                    self.send_error(416)
                    return
                left, right = range_match.groups()
                if left:
                    start = int(left)
                    if right:
                        end = min(int(right), end)
                else:
                    start = max(0, size - int(right))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            self.send_response(206 if requested else 200)
            self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if requested:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            if not head:
                with file.open('rb') as source:
                    source.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


