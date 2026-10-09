import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

import backend
import integrations
import media_backend


class PublishedAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = backend.create_server(self.root/'data', Path(__file__).resolve().parents[1]/'public', 'a'*64)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, headers, _ = self.request('/bootstrap?token='+'a'*64, authenticated=False)
        self.assertEqual(status, 302)
        self.cookie = headers['Set-Cookie'].split(';')[0]

    def tearDown(self):
        backend.stop(self.server)
        self.thread.join(timeout=3)
        os.environ['PATH'] = self.server.initial_path
        self.temp.cleanup()

    def request(self, path, body=None, headers=None, authenticated=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        all_headers = {'Origin': self.server.origin}
        if authenticated:
            all_headers['Cookie'] = self.cookie
        all_headers.update(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            all_headers['Content-Type'] = 'application/json'
        connection.request('POST' if body is not None else 'GET', path, body, all_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def ticket(self, scope, item=None):
        status, _, body = self.request('/api/media-ticket?scope='+scope+('&id='+str(item) if item else ''))
        self.assertEqual(status, 200)
        return urllib.parse.quote(json.loads(body)['ticket'])

    def config(self, data):
        return self.request('/api/config?ticket='+self.ticket('settings'), body=data)

    def choose(self, folder):
        folder.mkdir(exist_ok=True)
        status, _, body = self.config({'library_path': str(folder)})
        self.assertEqual(status, 200, body)

    def library(self):
        status, _, body = self.request('/api/library?ticket='+self.ticket('library'))
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_first_launch_is_empty_and_no_private_defaults(self):
        config = json.loads(self.request('/api/config')[2])
        self.assertEqual(config['library_path'], '')
        self.assertEqual(config['qbittorrent_url'], '')
        self.assertEqual(config['vpn_interface'], '')
        self.assertEqual(self.library(), [])

    def test_bootstrap_is_one_time_and_cookie_is_http_only(self):
        self.assertEqual(self.request('/bootstrap?token='+'a'*64, authenticated=False)[0], 403)
        self.assertEqual(self.request('/api/config', authenticated=False)[0], 401)

    def test_wrong_host_and_origin_rejected(self):
        self.assertEqual(self.request('/api/config', headers={'Host': 'unexpected.example'})[0], 403)
        self.assertEqual(self.request('/api/config?ticket='+self.ticket('settings'), body={'library_path':''},
                                      headers={'Origin':'https://unexpected.example'})[0], 401)

    def test_tickets_required_and_scope_bound(self):
        self.assertEqual(self.request('/api/library')[0], 401)
        self.assertEqual(self.request('/api/library?ticket='+self.ticket('manage'))[0], 401)

    def test_selected_folder_recurses_and_excludes_unselected_files(self):
        library = self.root/'my library'
        self.choose(library)
        (library/'Series'/'Season 1').mkdir(parents=True)
        (library/'Series'/'Season 1'/'Series.S01E02.mp4').write_bytes(b'own file')
        (self.root/'unselected.mp4').write_bytes(b'not shared')
        items = self.library()
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]['kind'], items[0]['season'], items[0]['episode']), ('tv',1,2))

    def test_original_file_unchanged_by_byte_range_stream(self):
        library = self.root/'videos'
        self.choose(library)
        file = library/'Video.mp4'
        file.write_bytes(b'0123456789')
        item = self.library()[0]['id']
        status, headers, body = self.request(f'/api/stream/{item}?ticket='+self.ticket('stream', item), headers={'Range':'bytes=2-5'})
        self.assertEqual((status, body), (206, b'2345'))
        self.assertEqual(headers['Content-Range'], 'bytes 2-5/10')
        self.assertEqual(file.read_bytes(), b'0123456789')

    def test_missing_ffmpeg_keeps_native_file_and_reports_mkv_dependency(self):
        library = self.root/'videos'
        self.choose(library)
        for extension in ('mp4','mkv'):
            (library/('Video.'+extension)).write_bytes(b'video')
        with patch('shutil.which', return_value=None):
            for item in self.library():
                status, _, body = self.request(f"/api/playable/{item['id']}?ticket="+self.ticket('stream', item['id']))
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['status'], 'ready' if item['name'].endswith('Video') and item['id']==media_backend.media_id(Path('Video.mp4')) else 'error')

    def test_progress_separate_for_each_library(self):
        first, second = self.root/'one', self.root/'two'
        self.choose(first)
        first_progress = self.server.view.progress_path
        backend.write_json(first_progress, {'1234567890123': {'position':10,'duration':100,'updated':int(time.time()*1000)}})
        self.choose(second)
        self.assertNotEqual(self.server.view.progress_path, first_progress)
        self.assertFalse(self.server.view.progress_path.exists())
        self.choose(first)
        body = self.request('/api/progress?ticket='+self.ticket('progress'))[2]
        self.assertIn('1234567890123', json.loads(body)['records'])

    def test_invalid_folder_does_not_change_settings(self):
        original = backend.read_json(self.server.data_root/'settings.json')
        self.assertEqual(self.config({'library_path':str(self.root/'missing')})[0], 400)
        self.assertEqual(backend.read_json(self.server.data_root/'settings.json'), original)

    def test_credentials_are_protected_and_not_returned(self):
        secret = 'test-password-do-not-publish'
        self.assertEqual(self.config({'qbittorrent_url':'http://127.0.0.1:8080','qbittorrent_username':'person','qbittorrent_password':secret})[0], 200)
        stored = (self.server.data_root/'settings.json').read_text()
        if os.name == 'nt':
            self.assertNotIn(secret, stored)
            self.assertIn('dpapi', stored)
        self.assertEqual(backend.read_json(self.server.data_root/'settings.json')['qbittorrent_password'], secret)
        self.assertNotIn(secret.encode(), self.request('/api/config')[2])

    def test_remote_qbit_and_embedded_credentials_rejected(self):
        for url in ['http://192.0.2.1:8080', 'http://user:password@127.0.0.1:8080', 'file:///tmp/qbit']:
            self.assertEqual(self.config({'qbittorrent_url':url})[0], 400)

    def test_downloads_blocked_when_vpn_off_or_unbound(self):
        self.choose(self.root/'videos')
        for state, bound in [('disconnected',True), ('connected',False), ('unknown',True)]:
            with patch('integrations.vpn_state', return_value=state), patch('integrations.qbit_activity', return_value=(0,bound)), patch('integrations.qbit_opener') as login:
                with self.assertRaises(RuntimeError):
                    integrations.add_magnet(self.server.view, 'magnet:?xt=urn:btih:'+'a'*40)
                login.assert_not_called()

    def test_non_vpn_interface_cannot_pass_binding_check(self):
        self.assertFalse(integrations.adapter_is_vpn({'Name':'Ethernet','InterfaceDescription':'Intel Ethernet','HardwareInterface':True,'Status':'Up'}))

    def test_binding_uses_qbit_interface_value_and_confirms_it(self):
        row = {'Name':'My VPN','InterfaceGuid':'test-guid','HardwareInterface':False,'InterfaceDescription':'WireGuard Tunnel','Status':'Up'}
        backend.write_json(self.server.view.settings_path, {'vpn_interface':'My VPN'})
        with patch('integrations.selected_adapter', return_value=row), patch('integrations.qbit_opener',return_value=(None,'http://127.0.0.1:8080',{})), patch('integrations.qbit_json',side_effect=[[{'name':'My VPN','value':'{test-guid}'}],{'current_network_interface':'{test-guid}'}]), patch('integrations.qbit_post') as post:
            integrations.bind_qbit(self.server.view)
            self.assertEqual(json.loads(post.call_args.args[-1]['json']), {'current_network_interface':'{test-guid}'})

    def test_unavailable_library_remains_saved_after_reopening(self):
        missing = str(self.root/'unplugged-drive')
        backend.write_json(self.server.data_root/'settings.json', {**backend.DEFAULTS,'library_path':missing})
        other = backend.create_server(self.server.data_root,self.server.public_root,'b'*64)
        try:
            self.assertEqual(backend.public_config(other)['library_path'], missing)
            self.assertFalse(backend.public_config(other)['libraryAvailable'])
            self.assertEqual(backend.read_json(other.data_root/'settings.json')['library_path'], missing)
        finally:
            other.server_close()

    def test_preferences_survive_fresh_server(self):
        prefs = {'resume':False,'volume':42,'subtitles':True}
        self.assertEqual(self.config({'action':'preferences','preferences':prefs})[0],200)
        second = backend.create_server(self.server.data_root,self.server.public_root,'b'*64)
        try:
            self.assertEqual(backend.public_config(second)['preferences'],prefs)
        finally:
            second.server_close()


if __name__ == '__main__':
    unittest.main()
