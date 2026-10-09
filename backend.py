"""Loopback-only desktop backend. No personal configuration ships with the app."""
import argparse
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import credentials
import integrations
import media_backend as media

DEFAULTS = {'library_path': '', 'ffmpeg_path': '', 'qbittorrent_url': '',
            'qbittorrent_username': '', 'qbittorrent_password': '',
            'vpn_provider': 'external', 'vpn_interface': '',
            'preferences': {'resume': True, 'volume': 80, 'subtitles': False}}
CONFIG_LOCK = threading.RLock()
raw_read, raw_write = media.read_json, media.write_json


def read_json(path):
    value = raw_read(path)
    return credentials.transform(value, True) if path.name == 'settings.json' else value


def write_json(path, value):
    raw_write(path, credentials.transform(value) if path.name == 'settings.json' else value)


media.read_json, media.write_json = read_json, write_json


def configure(server, config):
    path = config.get('library_path', '')
    root = Path(path).expanduser().resolve() if path else server.data_root/'empty-library'
    if path and not root.is_dir():
        raise ValueError('The media folder does not exist or is unavailable.')
    if not path:
        root.mkdir(parents=True, exist_ok=True)
    cache = server.data_root/'libraries'/hashlib.sha256(str(root).casefold().encode()).hexdigest()[:24]
    cache.mkdir(parents=True, exist_ok=True)
    server.view = SimpleNamespace(media_root=root, cache_root=cache, settings_path=server.data_root/'settings.json',
        metadata_path=cache/'metadata.json', overrides_path=cache/'overrides.json', progress_path=cache/'progress.json',
        origin=server.origin, secret=server.secret, stopping=server.stopping)
    tools = config.get('ffmpeg_path', '')
    if tools:
        folder = Path(tools).expanduser().resolve()
        if not (folder/'ffmpeg.exe').is_file() or not (folder/'ffprobe.exe').is_file():
            raise ValueError('Choose the folder containing ffmpeg.exe and ffprobe.exe.')
        os.environ['PATH'] = str(folder)+os.pathsep+server.initial_path
    else:
        os.environ['PATH'] = server.initial_path


def public_config(server):
    config = {**DEFAULTS, **read_json(server.data_root/'settings.json')}
    return {key: config[key] for key in ('library_path', 'ffmpeg_path', 'qbittorrent_url',
        'qbittorrent_username', 'vpn_provider', 'vpn_interface', 'preferences')} | {
        'qbittorrentConfigured': bool(config.get('qbittorrent_url') and config.get('qbittorrent_password')),
        'libraryAvailable': bool(config['library_path'] and Path(config['library_path']).expanduser().is_dir()),
        'tmdbConfigured': bool(config.get('tmdb_credential')),
        'openSubtitlesConfigured': bool(config.get('opensubtitles')),
        'ffmpegConfigured': bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))}


class Handler(media.Handler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://image.tmdb.org data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super(media.Handler, self).end_headers()

    def session(self):
        if self.headers.get('Host') != urlsplit(self.server.origin).netloc:
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return hmac.compare_digest(cookie['media-session'].value, self.server.session_token)
        except (KeyError, ValueError):
            return False

    def respond(self, body, mime='application/json', status=200, head=False):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_error(403)

    def handle_get(self, head=False):
        server = self.server
        path = unquote(urlsplit(self.path).path)
        if self.headers.get('Host') != urlsplit(server.origin).netloc:
            return self.send_error(403)
        if path == '/bootstrap':
            token = parse_qs(urlsplit(self.path).query).get('token', [''])[0]
            if not server.bootstrap_token or not hmac.compare_digest(token, server.bootstrap_token):
                return self.send_error(403)
            server.bootstrap_token = ''
            self.send_response(302)
            self.send_header('Set-Cookie', f'media-session={server.session_token}; HttpOnly; SameSite=Strict; Path=/')
            self.send_header('Location', '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if not self.session():
            return self.send_error(401)
        if path == '/api/media-ticket':
            query = parse_qs(urlsplit(self.path).query)
            scope = query.get('scope', [''])[0]
            item = query.get('id', [''])[0] if scope == 'stream' else 'local' if scope == 'progress' else ''
            if scope not in {'stream', 'progress', 'library', 'settings', 'manage', 'upload'}:
                return self.send_error(400)
            if scope == 'stream' and not item.isdigit():
                return self.send_error(400)
            expiry = str(int(time.time())+12*3600)
            signature = hmac.new(server.secret, f'{expiry}:{scope}:{item}'.encode(), hashlib.sha256).hexdigest()
            return self.respond({'ticket': expiry+'.'+signature}, head=head)
        if path == '/api/config':
            return self.respond(public_config(server), head=head)
        if path == '/config.js':
            config = public_config(server)
            code = 'window.MEDIA_FIRST_RUN='+json.dumps(not config['libraryAvailable'])+';\n'
            code += "localStorage.setItem('media-preferences',"+json.dumps(json.dumps(config['preferences']))+");\n"
            code += "localStorage.removeItem('media-history');\n"
            return self.respond(code.encode(), 'text/javascript', head=head)
        if path == '/api/settings':
            view = server.view
            self.server = view
            if not self.authorized('settings'):
                self.server = server
                return self.send_error(401)
            self.server = server
            data = public_config(server)
            data.update(storageBytes=shutil.disk_usage(view.media_root).free,
                        metadataRunning=media.METADATA_JOB is not None and not media.METADATA_JOB.done(),
                        downloadingCount=None, qbitBound=False, vpnState='unknown')
            if data['qbittorrentConfigured']:
                try:
                    data['downloadingCount'], data['qbitBound'] = integrations.qbit_activity(view)
                    data['vpnState'] = integrations.vpn_state(view)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    pass
            return self.respond(data, head=head)
        if path.startswith('/api/'):
            self.server = server.view
            if path.startswith('/api/playable/') and not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
                query = parse_qs(urlsplit(self.path).query)
                item = path.rsplit('/', 1)[-1]
                if not self.authorized('stream', item):
                    return self.send_error(401)
                file = next((file for item_id, file, _ in self.files() if str(item_id) == item), None)
                if file is None:
                    return self.send_error(404)
                if file.suffix.lower() not in {'.mp4', '.m4v', '.webm', '.ogv'} or query.get('sub') or query.get('audio'):
                    return self.respond({'status': 'error', 'error': 'Choose an FFmpeg folder in Settings to play this file or track.'}, head=head)
            return super().handle_get(head)
        root = server.public_root.resolve()
        file = (root/(path.lstrip('/') or 'index.html')).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            return self.send_error(404)
        return self.respond(file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream', head=head)

    def do_POST(self):
        server = self.server
        if not self.session() or self.headers.get('Origin') != server.origin:
            return self.send_error(401)
        path = urlsplit(self.path).path
        self.server = server.view
        if path == '/api/config':
            if not self.authorized('settings'):
                return self.send_error(401)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 8192:
                    raise ValueError('Invalid settings size.')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Settings must be a JSON object.')
                with CONFIG_LOCK:
                    config = {**DEFAULTS, **read_json(server.view.settings_path)}
                    if data.get('action') == 'bind':
                        integrations.bind_qbit(server.view)
                    elif data.get('action') == 'preferences':
                        prefs = data.get('preferences', {})
                        if (not isinstance(prefs.get('resume'), bool) or not isinstance(prefs.get('subtitles'), bool)
                                or not isinstance(prefs.get('volume'), (float, int)) or not 0 <= prefs['volume'] <= 100):
                            raise ValueError('Invalid playback settings.')
                        config['preferences'] = {key: prefs[key] for key in ('resume', 'volume', 'subtitles')}
                        write_json(server.view.settings_path, config)
                    else:
                        for key in ('library_path', 'ffmpeg_path', 'qbittorrent_url', 'qbittorrent_username',
                                    'qbittorrent_password', 'vpn_provider', 'vpn_interface'):
                            if key in data:
                                if not isinstance(data[key], str) or len(data[key]) > 1024 or any(c in data[key] for c in '\r\n\0'):
                                    raise ValueError('Invalid connection setting.')
                                config[key] = data[key].strip() if key != 'qbittorrent_password' else data[key]
                        if config['qbittorrent_url']:
                            integrations.validate_qbit_url(config['qbittorrent_url'])
                        if config['vpn_provider'] not in {'external', 'mullvad'}:
                            raise ValueError('Invalid VPN provider.')
                        if config['library_path'] and not Path(config['library_path']).expanduser().is_dir():
                            raise ValueError('The media folder does not exist or is unavailable.')
                        if config['ffmpeg_path'] and not all((Path(config['ffmpeg_path'])/name).is_file() for name in ('ffmpeg.exe', 'ffprobe.exe')):
                            raise ValueError('Choose the folder containing ffmpeg.exe and ffprobe.exe.')
                        write_json(server.view.settings_path, config)
                        configure(server, config)
                self.server = server
                return self.respond(public_config(server))
            except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
                return self.respond({'error': str(error)}, status=400)
        if path == '/api/torrent':
            if not self.authorized('upload'):
                return self.send_error(401)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 5*1024*1024:
                    raise ValueError('Choose a .torrent file under 5 MB.')
                integrations.add_torrent(server.view, self.rfile.read(size))
                return self.respond({}, status=201)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                return self.respond({'error': str(error)}, status=400)
        if path == '/api/settings':
            with CONFIG_LOCK:
                return super().do_POST()
        return super().do_POST()


def create_server(data, public, token):
    data = Path(data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.data_root, server.public_root = data, Path(public).resolve()
    server.origin = 'http://127.0.0.1:'+str(server.server_port)
    server.bootstrap_token, server.session_token, server.secret = token, secrets.token_hex(32), secrets.token_bytes(32)
    server.stopping = threading.Event()
    server.initial_path = os.environ.get('PATH', '')
    config = {**DEFAULTS, **read_json(data/'settings.json')}
    if not (data/'settings.json').exists():
        write_json(data/'settings.json', config)
    try:
        configure(server, config)
    except ValueError:
        config = {**config}
        config['library_path'], config['ffmpeg_path'] = '', ''
        configure(server, config)
    return server


def stop(server):
    server.stopping.set()
    for key in list(media.HLS_JOBS):
        media.stop_stream(key)
    server.shutdown()
    server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--public', type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.pop('MEDIA_PUBLISHED_BOOTSTRAP', '')
    if len(token) != 64:
        parser.error('Launch the backend through the desktop app.')
    server = create_server(args.data, args.public, token)
    # Keep media subprocesses hidden when launched from the Windows desktop app.
    def hidden_run(*args, **kwargs):
        kwargs.setdefault('creationflags', getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return subprocess.run(*args, **kwargs)
    def hidden_popen(*args, **kwargs):
        kwargs.setdefault('creationflags', getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return subprocess.Popen(*args, **kwargs)
    media.subprocess = SimpleNamespace(**{name: getattr(subprocess, name) for name in dir(subprocess) if not name.startswith('_')})
    media.subprocess.run, media.subprocess.Popen = hidden_run, hidden_popen
    def watch_parent():
        sys.stdin.readline()
        stop(server)
    threading.Thread(target=watch_parent, daemon=True).start()
    threading.Thread(target=media.reap_hls_jobs, args=(server.view,), daemon=True).start()
    print('MEDIA_PUBLISHED_READY='+server.origin, flush=True)
    server.serve_forever()
    os._exit(0)
