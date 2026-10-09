# MediaServer Published

[Download the Windows installer or portable ZIP](https://github.com/iconicJose/MediaServer-Published/releases/latest).

Windows x64 desktop app for a user's own media library. The installer includes the desktop runtime and Python backend; Python and Node.js are not required to run it. No media, account credentials, server addresses, or VPN subscriptions are included.

## Set up

1. Install `MediaServer-Published-Setup-1.0.0.exe`, or extract the portable ZIP and run `MediaServer Published.exe`.
2. In **Settings → Media library**, choose your media folder and press **Save folder**. Subfolders are included. Files are indexed in place.
3. MP4 and WebM files supported by Chromium can play directly. For MKV, other formats, audio switching, or embedded subtitles, install an FFmpeg build containing `libx264`, then select the folder containing **both** `ffmpeg.exe` and `ffprobe.exe` in Settings. [FFmpeg downloads](https://ffmpeg.org/download.html).
4. Metadata and subtitle downloads are optional. Enter your own TMDb or OpenSubtitles credentials in Settings.

## qBittorrent and VPN

Install your own qBittorrent and VPN application. Enable qBittorrent's Web UI on the same PC, then enter its local URL, username, and password in Settings. A URL such as `http://127.0.0.1:8080` connects to the user's own PC.

Enter the installed VPN adapter's name or ID, save connections, connect the VPN in its application, and press **Bind qBittorrent to VPN**. The app checks the selected adapter and qBittorrent's configured interface before accepting magnet links or torrent files. Mullvad users can select Mullvad control to use its installed command-line client. Other VPNs are connected through their own applications. Adapter and binding checks do not constitute a network leak test.

Downloads submitted through this app use the selected media folder. Previously queued downloads remain governed by their existing qBittorrent settings. Playback works independently of qBittorrent and VPN setup.

## Local settings and source

Settings are stored in `%LOCALAPPDATA%\MediaServerPublished\data\settings.json`. Folder paths and connections can also be edited there while the app is closed. Windows credentials are encrypted with the current user's DPAPI key. To change passwords or API keys, use the app's Settings controls. Watch history and cached metadata are separated by library folder. Desktop preferences are stored separately from other editions of the app.

The backend listens only on the current PC. This release does not provide a hosted media service or configure remote access.

To edit and rebuild the application on Windows, install Python 3.12 and Node.js, then run:

```powershell
python -m pip install -r requirements.txt
npm ci
npm run check
npm run build
```

`npm start` runs the source version. The editable interface is in `public/`; the desktop shell is in `desktop/`; connection handling is in `integrations.py`. Build output is in `dist/`.

## Distribution notes

The installer is unsigned. The portable ZIP should be fully extracted before launching the executable. FFmpeg, qBittorrent, and VPN clients are separate, user-installed dependencies. See `THIRD_PARTY_NOTICES.md` and the bundled license files.
