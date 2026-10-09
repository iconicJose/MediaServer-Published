"""Connections configured by the person running the app."""
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request


def settings(server):
    import media_backend
    return media_backend.read_json(server.settings_path)


def validate_qbit_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
        raise ValueError('Use the local qBittorrent Web UI URL, for example http://127.0.0.1:8080.')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('Invalid qBittorrent port.')
    return url.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('qBittorrent redirected to an unexpected address.')


def qbit_opener(server):
    config = settings(server)
    root = validate_qbit_url(config.get('qbittorrent_url', ''))
    password = config.get('qbittorrent_password', '')
    if not password:
        raise RuntimeError('Configure the qBittorrent Web UI username and password in Settings.')
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    headers = {'Referer': root + '/'}
    data = urllib.parse.urlencode({'username': config.get('qbittorrent_username', ''), 'password': password}).encode()
    with opener.open(urllib.request.Request(root+'/api/v2/auth/login', data=data, headers=headers), timeout=5) as response:
        if response.read().strip() != b'Ok.':
            raise RuntimeError('qBittorrent login failed.')
    return opener, root, headers


def qbit_json(opener, root, headers, endpoint):
    with opener.open(urllib.request.Request(root+endpoint, headers=headers), timeout=5) as response:
        return json.load(response)


def qbit_post(opener, root, headers, endpoint, data):
    request = urllib.request.Request(root+endpoint, headers=headers, data=urllib.parse.urlencode(data).encode())
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError('qBittorrent rejected the change.')


def adapters():
    if os.name != 'nt':
        return []
    command = ('Get-NetAdapter -IncludeHidden | Select-Object Name,InterfaceGuid,Status,InterfaceDescription,HardwareInterface '
               '| ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=8, check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    data = json.loads(result.stdout or '[]')
    return [data] if isinstance(data, dict) else data


def selected_adapter(config):
    interface = config.get('vpn_interface', '').casefold().strip('{}')
    if not interface:
        return None
    return next((row for row in adapters() if interface in {
        str(row.get('Name', '')).casefold(), str(row.get('InterfaceGuid', '')).casefold().strip('{}')}), None)


def adapter_is_vpn(row):
    return row and not row.get('HardwareInterface', True) and re.search(
        r'wireguard|wintun|tap|vpn|tunnel|mullvad|nord|proton|openvpn',
        str(row.get('InterfaceDescription', ''))+' '+str(row.get('Name', '')), re.I)


def interface_matches(current, config, row=None):
    values = {config.get('vpn_interface', '')}
    if row:
        values.update({row.get('Name', ''), row.get('InterfaceGuid', '')})
    actual = str(current).strip('{}').casefold()
    return bool(actual) and actual in {str(value).strip('{}').casefold() for value in values if value}


def qbit_activity(server):
    opener, root, headers = qbit_opener(server)
    config = settings(server)
    row = selected_adapter(config)
    preferences = qbit_json(opener, root, headers, '/api/v2/app/preferences')
    downloading = qbit_json(opener, root, headers, '/api/v2/torrents/info?filter=downloading')
    bound = bool(adapter_is_vpn(row)) and interface_matches(preferences.get('current_network_interface'), config, row)
    return len(downloading), bound


def mullvad_executable():
    return shutil.which('mullvad') or next((str(path) for path in [
        Path(os.environ.get('ProgramFiles', ''))/'Mullvad VPN'/'resources'/'mullvad.exe',
        Path(os.environ.get('ProgramFiles', ''))/'Mullvad VPN'/'mullvad.exe'] if path.is_file()), None)


def vpn_state(server):
    config = settings(server)
    row = selected_adapter(config)
    if not adapter_is_vpn(row):
        return 'unknown'
    if config.get('vpn_provider') == 'mullvad':
        executable = mullvad_executable()
        if not executable:
            return 'unknown'
        result = subprocess.run([executable, 'status', '--json'], capture_output=True, text=True,
                                timeout=8, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        state = json.loads(result.stdout).get('state', 'unknown')
        return state if state != 'connected' or row.get('Status') == 'Up' else 'unknown'
    return 'connected' if row.get('Status') == 'Up' else 'disconnected'


def switch_vpn(server, enabled):
    if settings(server).get('vpn_provider') != 'mullvad':
        raise ValueError('Connect or disconnect this VPN in its own application.')
    executable = mullvad_executable()
    if not executable:
        raise RuntimeError('Mullvad is not installed.')
    if not enabled and not qbit_activity(server)[1]:
        raise RuntimeError('Bind qBittorrent to the selected VPN adapter before disconnecting.')
    subprocess.run([executable, 'connect' if enabled else 'disconnect'], capture_output=True,
                   timeout=30, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def require_download_connection(server):
    path = settings(server).get('library_path', '')
    if not path or not Path(path).is_dir() or Path(path).resolve() != server.media_root:
        raise RuntimeError('Choose an available media folder first.')
    if vpn_state(server) != 'connected' or not qbit_activity(server)[1]:
        raise RuntimeError('The selected VPN adapter must be active and qBittorrent must be bound to it.')


def bind_qbit(server):
    config = settings(server)
    row = selected_adapter(config)
    if not adapter_is_vpn(row):
        raise ValueError('Select an installed VPN adapter.')
    opener, root, headers = qbit_opener(server)
    available = qbit_json(opener, root, headers, '/api/v2/app/networkInterfaceList')
    choices = available if isinstance(available, list) else []
    chosen = next((entry.get('value', entry.get('name', '')) if isinstance(entry, dict) else entry
                   for entry in choices
                   if any(interface_matches(value, config, row) for value in
                          (entry.get('name', ''), entry.get('value', '')) if isinstance(entry, dict))
                   or (not isinstance(entry, dict) and interface_matches(entry, config, row))), None)
    if not chosen:
        raise RuntimeError('qBittorrent could not identify this VPN adapter.')
    qbit_post(opener, root, headers, '/api/v2/app/setPreferences', {'json': json.dumps({'current_network_interface': chosen})})
    confirmed = qbit_json(opener, root, headers, '/api/v2/app/preferences')
    if not interface_matches(confirmed.get('current_network_interface'), config, row):
        raise RuntimeError('qBittorrent did not confirm the VPN binding.')


def add_magnet(server, link):
    if not isinstance(link, str) or len(link) > 4096:
        raise ValueError('Invalid magnet link.')
    parsed = urllib.parse.urlsplit(link.strip())
    hashes = urllib.parse.parse_qs(parsed.query).get('xt', [])
    if parsed.scheme != 'magnet' or not any(re.fullmatch(r'urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', value) for value in hashes):
        raise ValueError('Invalid magnet link.')
    require_download_connection(server)
    opener, root, headers = qbit_opener(server)
    qbit_post(opener, root, headers, '/api/v2/torrents/add',
              {'urls': link.strip(), 'savepath': str(server.media_root), 'autoTMM': 'false'})


def add_torrent(server, payload):
    if not 1 <= len(payload) <= 5*1024*1024 or not payload.startswith(b'd') or b'4:info' not in payload:
        raise ValueError('Choose a valid .torrent file under 5 MB.')
    require_download_connection(server)
    opener, root, headers = qbit_opener(server)
    import secrets
    boundary = secrets.token_hex(24)
    data = (f'--{boundary}\r\nContent-Disposition: form-data; name="savepath"\r\n\r\n{server.media_root}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="autoTMM"\r\n\r\nfalse\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="upload.torrent"\r\n'
            'Content-Type: application/x-bittorrent\r\n\r\n').encode()+payload+f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(root+'/api/v2/torrents/add', data=data,
        headers={**headers, 'Content-Type': 'multipart/form-data; boundary='+boundary})
    with opener.open(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError('qBittorrent rejected the torrent.')
