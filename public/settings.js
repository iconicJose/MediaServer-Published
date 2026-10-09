const titlebar=document.createElement('div');
titlebar.id='desktop-titlebar';
titlebar.innerHTML='<span>MediaServer Published</span><div><button id="window-minimize" aria-label="Minimize">−</button><button id="window-maximize" aria-label="Maximize">□</button><button id="window-close" aria-label="Close">×</button></div>';
document.body.prepend(titlebar);
for(const action of ['minimize','maximize','close'])titlebar.querySelector('#window-'+action).onclick=()=>window.desktop?.[action]();
let connectionConfig={},settingsSerial=0;
const renderBeforeSettings=render;
render=function(){
    if(page!=='Settings')return renderBeforeSettings();
    document.querySelectorAll('[data-page]').forEach(button=>button.classList.toggle('active',button.dataset.page===page));
    document.querySelector('.toolbar').hidden=true;
    const serial=++settingsSerial;
    content.innerHTML=`<h1>Settings</h1><div class="settings-grid">
      <section class="panel"><h2>Media library</h2>
        <label for="library-path">Media folder</label><div class="path-row"><input id="library-path" type="text" autocomplete="off"><button id="choose-library">Choose folder</button></div>
        <button id="save-library">Save folder</button><p id="storage-status" class="notice"></p>
        <div class="settings-actions"><button id="rescan">Rescan library</button><button id="refresh-metadata">Refresh metadata</button><button id="clear-artwork">Clear artwork cache</button><button id="clear-history">Clear watch history</button></div>
        <label for="ffmpeg-path">FFmpeg folder</label><div class="path-row"><input id="ffmpeg-path" type="text" autocomplete="off"><button id="choose-ffmpeg">Choose folder</button></div>
        <button id="save-ffmpeg">Save FFmpeg folder</button><a class="functional-link" href="https://ffmpeg.org/download.html" target="_blank" rel="noreferrer">Download FFmpeg</a><p id="ffmpeg-status" class="notice"></p>
        <p id="config-status" class="notice" role="status"></p>
      </section>
      <section class="panel"><h2>Downloads and VPN</h2>
        <label for="qbit-url">qBittorrent Web UI URL</label><input id="qbit-url" type="url" autocomplete="off">
        <label for="qbit-user">qBittorrent username</label><input id="qbit-user" type="text" autocomplete="off">
        <label for="qbit-password">qBittorrent password</label><input id="qbit-password" type="password" autocomplete="new-password">
        <label for="vpn-provider">VPN control</label><select id="vpn-provider"><option value="external">Use my VPN application</option><option value="mullvad">Mullvad</option></select>
        <label for="vpn-interface">VPN adapter name or ID</label><input id="vpn-interface" type="text" autocomplete="off">
        <div class="settings-actions"><button id="save-connections">Save connections</button><button id="bind-vpn">Bind qBittorrent to VPN</button><button id="toggle-vpn" hidden>Connect VPN</button></div>
        <p id="vpn-status" class="notice"></p><p id="download-status" class="notice"></p><p id="vpn-safety" class="notice" role="status"></p>
        <div class="torrent-inputs"><label for="magnet-link">Magnet link</label><div class="torrent-row"><input id="magnet-link" type="text" autocomplete="off" spellcheck="false"><button id="add-magnet" disabled>Add link</button></div>
        <label for="torrent-file">Torrent file</label><div class="torrent-row"><input id="torrent-file" type="file" accept=".torrent,application/x-bittorrent"><button id="add-torrent-file" disabled>Add file</button></div><p id="torrent-status" class="notice" role="status"></p></div>
      </section>
      <section class="panel"><h2>API keys</h2><label for="tmdb-key">TMDb API key or read access token</label><input id="tmdb-key" type="password" autocomplete="new-password"><button id="save-metadata">Save TMDb credential</button><p id="metadata-status" class="notice"></p>
        <p class="notice">This product uses the TMDb API but is not endorsed or certified by TMDb.</p>
        <label for="opensubtitles-key">OpenSubtitles API key</label><input id="opensubtitles-key" type="password" autocomplete="new-password">
        <label for="opensubtitles-user">OpenSubtitles username</label><input id="opensubtitles-user" type="text" autocomplete="off">
        <label for="opensubtitles-password">OpenSubtitles password</label><input id="opensubtitles-password" type="password" autocomplete="new-password"><button id="save-opensubtitles">Save OpenSubtitles credentials</button><p id="opensubtitles-status" class="notice" role="status"></p>
      </section>
      <section class="panel"><h2>Playback</h2><label><input id="resume" type="checkbox"> Resume playback</label><label><input id="prefer-subtitles" type="checkbox"> Prefer subtitles</label><label for="volume">Starting volume</label><input id="volume" type="range" min="0" max="100"><span id="volume-value"></span></section>
    </div>`;
    wirePublishedSettings(serial);
};
function settingsMessage(message){const output=document.querySelector('#config-status');if(output)output.textContent=message;}
async function publishedPost(payload){
    const ticket=await settingsTicket();
    const response=await timedFetch(base+'/api/config?ticket='+ticket,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)},20000);
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'Could not save settings.');
    return data;
}
savePrefs=function(){
    localStorage.setItem('media-preferences',JSON.stringify(prefs));
    publishedPost({action:'preferences',preferences:prefs}).catch(error=>settingsMessage(error.message));
};
function showConnectionState(state){
    document.querySelector('#storage-status').textContent=state.library_path?(state.libraryAvailable?(state.storageBytes/1073741824).toFixed(1)+' GiB free':'Media folder unavailable'):'';
    document.querySelector('#ffmpeg-status').textContent=state.ffmpegConfigured?'FFmpeg available':'FFmpeg not configured';
    document.querySelector('#metadata-status').textContent=state.tmdbConfigured?(state.metadataRunning?'Refreshing metadata…':'Credential configured'):'';
    document.querySelector('#opensubtitles-status').textContent=state.openSubtitlesConfigured?'Credentials configured':'';
    document.querySelector('#vpn-status').textContent=state.vpn_interface?'VPN adapter: '+(state.vpnState==='connected'?'Active':state.vpnState==='disconnected'?'Inactive':'Unavailable'):'';
    document.querySelector('#download-status').textContent=state.qbittorrentConfigured?'Downloading: '+(Number.isInteger(state.downloadingCount)?state.downloadingCount:'Unavailable'):'';
    const ready=Boolean(state.libraryAvailable)&&state.vpnState==='connected'&&state.qbitBound;
    document.querySelector('#add-magnet').disabled=!ready;
    document.querySelector('#add-torrent-file').disabled=!ready;
    document.querySelector('#vpn-safety').textContent=state.qbittorrentConfigured&&!state.qbitBound?'qBittorrent binding to the selected VPN adapter is unverified.':'';
    const toggle=document.querySelector('#toggle-vpn');
    toggle.hidden=state.vpn_provider!=='mullvad';
    toggle.disabled=state.vpnState==='unknown'||(state.vpnState==='connected'&&!state.qbitBound);
    toggle.textContent=state.vpnState==='connected'?'Disconnect VPN':'Connect VPN';
    toggle.dataset.enabled=state.vpnState==='connected'?'false':'true';
}
async function wirePublishedSettings(serial){
    const field=id=>document.querySelector('#'+id);
    async function refresh(){
        const ticket=await settingsTicket();
        const response=await timedFetch(base+'/api/settings?ticket='+ticket,{},25000);
        if(!response.ok)throw Error('Could not read connection settings.');
        const state=await response.json();
        if(page!=='Settings'||serial!==settingsSerial)return;
        connectionConfig=state;
        showConnectionState(state);
    }
    async function action(operation){try{settingsMessage('');await operation();}catch(error){settingsMessage(error.message);}}
    const response=await fetch('/api/config');
    if(!response.ok){settingsMessage('Could not read settings.');return;}
    const config=await response.json();
    if(page!=='Settings'||serial!==settingsSerial)return;
    field('library-path').value=config.library_path;
    field('ffmpeg-path').value=config.ffmpeg_path;
    field('qbit-url').value=config.qbittorrent_url;
    field('qbit-user').value=config.qbittorrent_username;
    field('vpn-provider').value=config.vpn_provider;
    field('vpn-interface').value=config.vpn_interface;
    for(const [button,input] of [['choose-library','library-path'],['choose-ffmpeg','ffmpeg-path']])field(button).onclick=()=>action(async()=>{
        const selectedFolder=await window.desktop.chooseFolder();if(selectedFolder)field(input).value=selectedFolder;
    });
    field('save-library').onclick=()=>action(async()=>{
        await publishedPost({library_path:field('library-path').value});
        progressPending.clear();progressAccess=null;
        for(const key of Object.keys(history))delete history[key];
        items=[];selected=null;season=null;saveLocalHistory();await load();await refresh();
    });
    field('save-ffmpeg').onclick=()=>action(async()=>{await publishedPost({ffmpeg_path:field('ffmpeg-path').value});await refresh();});
    field('save-connections').onclick=()=>action(async()=>{
        const data={qbittorrent_url:field('qbit-url').value,qbittorrent_username:field('qbit-user').value,
            vpn_provider:field('vpn-provider').value,vpn_interface:field('vpn-interface').value};
        if(field('qbit-password').value)data.qbittorrent_password=field('qbit-password').value;
        await publishedPost(data);field('qbit-password').value='';await refresh();
    });
    field('bind-vpn').onclick=()=>action(async()=>{await publishedPost({action:'bind'});await refresh();});
    field('toggle-vpn').onclick=()=>action(async()=>{
        const ticket=await settingsTicket();
        const result=await timedFetch(base+'/api/settings?ticket='+ticket,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'vpn',enabled:field('toggle-vpn').dataset.enabled==='true'})},40000);
        if(!result.ok)throw Error('Could not change Mullvad state.');await refresh();
    });
    field('resume').checked=prefs.resume;field('prefer-subtitles').checked=prefs.subtitles;field('volume').value=prefs.volume;field('volume-value').textContent=prefs.volume+'%';
    field('resume').onchange=event=>{prefs.resume=event.target.checked;savePrefs();};
    field('prefer-subtitles').onchange=event=>{prefs.subtitles=event.target.checked;savePrefs();};
    let volumeSave;
    field('volume').oninput=event=>{prefs.volume=Number(event.target.value);field('volume-value').textContent=prefs.volume+'%';clearTimeout(volumeSave);volumeSave=setTimeout(savePrefs,300);};
    field('rescan').onclick=()=>action(()=>load());
    field('clear-history').onclick=()=>action(async()=>{if(!confirm('Clear watch history for this library?'))return;await progressPost(null,'clear');progressPending.clear();for(const key of Object.keys(history))delete history[key];saveLocalHistory();});
    for(const [button,command] of [['refresh-metadata','refresh'],['clear-artwork','clear-artwork']])field(button).onclick=()=>action(async()=>{await settingsRequest(await settingsTicket(),command);await load();await refresh();});
    field('save-metadata').onclick=()=>action(async()=>{await settingsRequest(await settingsTicket(),'credential',field('tmdb-key').value.trim());field('tmdb-key').value='';await refresh();});
    field('save-opensubtitles').onclick=()=>action(async()=>{
        const result=await timedFetch(base+'/api/settings?ticket='+await settingsTicket(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'opensubtitles',key:field('opensubtitles-key').value,user:field('opensubtitles-user').value,password:field('opensubtitles-password').value})},40000);
        if(!result.ok)throw Error('OpenSubtitles rejected the credentials.');
        field('opensubtitles-key').value='';field('opensubtitles-password').value='';await refresh();
    });
    async function upload(path,body,type){
        field('torrent-status').textContent='';
        try{
            const ticket=(await (await fetch('/api/media-ticket?scope=upload')).json()).ticket;
            const result=await timedFetch(base+path+'?ticket='+encodeURIComponent(ticket),{method:'POST',headers:{'Content-Type':type},body},30000);
            if(!result.ok){let data;try{data=await result.json();}catch{}throw Error(data?.error||'The VPN adapter and qBittorrent binding must be active.');}
            return true;
        }catch(error){field('torrent-status').textContent=error.message;return false;}
    }
    field('add-magnet').onclick=async()=>{if(await upload('/api/torrent-link',JSON.stringify({link:field('magnet-link').value}),'application/json'))field('magnet-link').value='';};
    field('add-torrent-file').onclick=async()=>{const file=field('torrent-file').files[0];if(!file)return;if(await upload('/api/torrent',file,'application/x-bittorrent'))field('torrent-file').value='';};
    await action(refresh);
    const timer=setInterval(()=>{if(page!=='Settings'||serial!==settingsSerial){clearInterval(timer);return;}refresh().catch(()=>{});},20000);
}
if(window.MEDIA_FIRST_RUN){page='Settings';render();}
