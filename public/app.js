const content=document.querySelector('#content'),search=document.querySelector('#search');
const base=location.origin;
let items=[],page=window.MEDIA_FIRST_RUN?'Settings':'Home',selected=null,season=null;
const prefs=JSON.parse(localStorage.getItem('media-preferences')||'{"resume":true,"volume":80,"subtitles":false}');
const history=JSON.parse(localStorage.getItem('media-history')||'{}');
let progressAccess=null,progressFlushTimer=null,progressSending=false;
const progressPending=new Map();
function saveLocalHistory(){localStorage.setItem('media-history',JSON.stringify(history))}
async function timedFetch(url,options={},milliseconds=4000){
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),milliseconds);
    try{return await fetch(url,{...options,signal:controller.signal})}
    finally{clearTimeout(timeout)}
}
async function progressTicket(){
    if(progressAccess&&Date.now()<progressAccess.expires)return progressAccess.ticket;
    const response=await timedFetch('/api/media-ticket?scope=progress',{cache:'no-store'});
    if(!response.ok)throw Error('Account session expired.');
    const data=await response.json();
    progressAccess={ticket:encodeURIComponent(data.ticket),expires:Date.now()+10*60*1000};
    return progressAccess.ticket;
}
async function progressPost(records,action){
    const ticket=await progressTicket();
    const response=await timedFetch(base+'/api/progress?ticket='+ticket,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(action?{action}:{records}),keepalive:true},8000);
    if(!response.ok)throw Error('Could not sync watch progress.');
}
async function syncProgress(){
    try{
        const ticket=await progressTicket();
        const response=await timedFetch(base+'/api/progress?ticket='+ticket,{cache:'no-store'});
        if(!response.ok)throw Error();
        const data=await response.json();
        const remote=data.records||{};
        const upload={};
        let changed=false;
        for(const [id,record] of Object.entries(history)){
            if((record?.updated||0)<=Number(data.clearEpoch||0)){
                delete history[id];changed=true;
            }
        }
        for(const [id,record] of Object.entries(remote)){
            if(!history[id]||record.updated>history[id].updated){history[id]=record;changed=true}
        }
        for(const [id,record] of Object.entries(history)){
            if(!remote[id]&&record?.updated&&record?.duration)upload[id]=record;
            else if(remote[id]&&record?.updated>remote[id].updated)upload[id]=record;
        }
        if(changed)saveLocalHistory();
        if(Object.keys(upload).length)progressPost(upload).catch(()=>{});
        return changed;
    }catch{return false}
}
async function flushProgress(){
    if(progressSending||!progressPending.size)return;
    progressSending=true;
    const records=Object.fromEntries(progressPending);
    progressPending.clear();
    try{await progressPost(records)}
    catch{for(const [id,record] of Object.entries(records))if(!progressPending.has(id))progressPending.set(id,record)}
    finally{progressSending=false;if(progressPending.size){clearTimeout(progressFlushTimer);progressFlushTimer=setTimeout(flushProgress,5000)}}
}
function queueProgress(id,record){
    progressPending.set(String(id),record);
    clearTimeout(progressFlushTimer);
    progressFlushTimer=setTimeout(flushProgress,1000);
}
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const artwork=x=>x.artwork?.startsWith('/api/')?base+x.artwork:x.artwork;
const icons=['M3 11 12 3 21 11M5 10v11h14V10M10 21v-6h4v6','M3 5h18v14H3zM8 5v14M16 5v14M3 10h5M3 14h5M16 10h5M16 14h5','M3 4h18v13H3zM9 21h6M12 17v4','M4 6h16M4 12h16M4 18h16M9 3v6M15 9v6M9 15v6'];
document.querySelector('nav').innerHTML=['Home','Movies','TV Shows','Settings'].map((p,i)=>`<button data-page="${p}" aria-label="${p}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="${icons[i]}"/></svg><span>${p}</span></button>`).join('');
document.querySelector('nav').onclick=e=>{const b=e.target.closest('[data-page]');if(b){page=b.dataset.page;selected=null;season=null;render()}};
search.oninput=render;
function progress(x){const h=history[x.id];return h?.duration?h.position/h.duration:x.duration?x.progress/x.duration:0}
function tile(x,show=false){const title=show?x.series:x.name,detail=show?`${x.year||''} • ${x.seasons} seasons`:[x.year,x.kind==='tv'?`S${x.season} E${x.episode}`:'Movie'].filter(Boolean).join(' • ');return `<button class="tile" data-id="${x.id}" data-manage-kind="${show?'series':x.kind==='tv'?'episode':'movie'}" ${show?'data-show="true"':''}>${x.artwork?`<img loading="lazy" src="${escape(artwork(x))}" alt="">`:''}<span class="caption"><strong>${escape(title)}</strong><small>${escape(detail)}</small>${show?`<p>${escape(x.seriesOverview||x.overview)}</p>`:''}</span><span class="progress"><i style="width:${Math.min(100,Math.max(0,progress(x)*100))}%"></i></span></button>`}
const titleKey=s=>String(s||'').toLowerCase().replace(/^(the|a) /,'');
const bytesLabel=n=>n>=1e9?(n/1e9).toFixed(1)+' GB':n>=1e6?(n/1e6).toFixed(0)+' MB':'';
function genreGroups(rows){const groups=new Map();for(const row of rows){const name=row.genre||'Uncategorized';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(row)}return [...groups].sort((a,b)=>a[0].localeCompare(b[0])).map(([name,list])=>({name,items:list.sort((a,b)=>titleKey(a.series||a.name).localeCompare(titleKey(b.series||b.name)))}))}
function showCards(rows){const byName=new Map();for(const row of rows.filter(x=>x.kind==='tv')){if(!byName.has(row.series))byName.set(row.series,[]);byName.get(row.series).push(row)}return [...byName].map(([name,episodes])=>{episodes.sort((a,b)=>a.season-b.season||a.episode-b.episode);const lead=episodes.find(x=>progress(x)>0&&progress(x)<.95)||episodes[0];const art=episodes.find(x=>x.seriesArtwork||x.artwork)||lead;const seasons=new Set(episodes.map(x=>x.season));const total=Math.max(...episodes.map(x=>x.totalSeasons||0));return {...lead,id:lead.id,series:name,artwork:art.seriesArtwork||art.artwork,seriesOverview:art.seriesOverview||art.overview,year:art.year,yearEnd:art.yearEnd,genre:art.genre||'Uncategorized',seasons:seasons.size,totalSeasons:total,size:episodes.reduce((sum,x)=>sum+(x.size||0),0),playId:lead.id}})}
function showTile(x){const years=x.year?(x.yearEnd&&x.yearEnd!==x.year?`${x.year}–${x.yearEnd}`:x.year):'',detail=[years,`${x.seasons}/${x.totalSeasons||'?'} Seasons`,bytesLabel(x.size)].filter(Boolean).join(' • ');return `<button class="show-tile" data-id="${x.id}" data-manage-kind="series" data-show="true">${x.artwork?`<img loading="lazy" src="${escape(artwork(x))}" alt="">`:''}<span class="show-caption"><strong>${escape(x.series)}</strong><small>${escape(detail)}</small><i></i><span>${escape(x.seriesOverview||'No series description available')}</span></span><span class="show-play" aria-hidden="true"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 12 7-12 7z"/></svg></span></button>`}
function episodeTile(x){const code=`S${String(x.season||0).padStart(2,'0')} E${String(x.episode||0).padStart(2,'0')}`;return `<button class="episode-tile" data-id="${x.id}" data-manage-kind="episode">${x.artwork?`<img loading="lazy" src="${escape(artwork(x))}" alt="">`:''}<span class="episode-caption"><small>${code}</small><strong>${escape(x.name||'Episode '+x.episode)}</strong><span>${escape(x.overview||'No episode description available')}</span></span><span class="progress"><i style="width:${Math.min(100,Math.max(0,progress(x)*100))}%"></i></span></button>`}
function section(title,rows){return `<h2>${title}</h2><div class="row">${rows.length?rows.map(x=>tile(x)).join(''):'<p class="notice">Nothing here yet</p>'}</div>`}
function render(){document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));const query=search.value.toLowerCase(),filtered=items.filter(x=>(x.name+' '+x.series).toLowerCase().includes(query));
if(selected){const x=selected,episodes=x.show?items.filter(v=>v.series===x.series).sort((a,b)=>a.season-b.season||a.episode-b.episode):[],seasons=[...new Set(episodes.map(e=>e.season))].sort((a,b)=>a-b);if(season===null)season=seasons[0];const visible=episodes.filter(e=>e.season===season&&(e.name+' '+e.series).toLowerCase().includes(query));const seasonTotal=episodes.find(e=>e.season===season)?.seasonEpisodes||'?';const meta=x.show?[x.yearEnd&&x.yearEnd!==x.year?`${x.year}–${x.yearEnd}`:x.year,x.genre,`${seasons.length}/${x.totalSeasons||'?'} Seasons`,bytesLabel(x.size)]:[x.year,x.genre,bytesLabel(x.size)];content.innerHTML=`<div class="hero detail-hero ${x.show?'show-detail':'movie-detail'}">${x.artwork?`<img src="${escape(artwork(x))}" alt="">`:''}<div><button class="back-link" data-back>← &nbsp;Back to ${x.show?'TV Shows':'Movies'}</button>${x.show?'':'<small>FROM YOUR LIBRARY</small>'}<h1>${escape(x.show?x.series:x.name)}</h1><p class="detail-meta">${escape(meta.filter(Boolean).join(' • '))}</p><p class="detail-overview">${escape(x.show?x.seriesOverview||x.overview:x.overview||'No description available')}</p><button class="primary" data-play="${x.playId||x.id}">${x.show?`Play S${x.season} E${x.episode}`:'Play'}</button></div></div>${x.show?`<div class="chips">${seasons.map(s=>`<button class="chip ${season===s?'active':''}" data-season="${s}">Season ${s}</button>`).join('')}</div><p class="season-summary">${visible.length}/${seasonTotal} episodes</p><div class="grid episode-grid">${visible.length?visible.map(episodeTile).join(''):'<p class="notice">No episodes found for this season</p>'}</div>`:''}`;return}
if(page==='Home')content.innerHTML=(filtered.length?`<div class="hero featured">${filtered[0].artwork?`<img src="${escape(artwork(filtered[0]))}" alt="">`:''}<div><small>FEATURED FROM YOUR LIBRARY</small><h1>${escape(filtered[0].name)}</h1><p>${escape(filtered[0].year||'')}</p><button class="primary" data-play="${filtered[0].id}">Play</button></div></div>`:'')+section('Continue Watching',filtered.filter(x=>progress(x)>0&&progress(x)<.95).slice(0,20))+section('Recently Added',filtered.slice(0,30))+section('Recently Watched',filtered.filter(x=>history[x.id]||x.watched).sort((a,b)=>(history[b.id]?.updated||b.watched_at)-(history[a.id]?.updated||a.watched_at)).slice(0,20));
if(page==='Movies'){const movies=filtered.filter(x=>x.kind==='movie');content.innerHTML='<h1>Movies</h1>'+(movies.length?genreGroups(movies).map(group=>`<section class="genre-group"><h2>${escape(group.name)}</h2><div class="grid movie-grid">${group.items.map(x=>tile(x)).join('')}</div></section>`).join(''):'<p class="notice">No movies found</p>')}
if(page==='TV Shows'){const shows=showCards(filtered);content.innerHTML='<h1>TV Shows</h1><p class="page-subtitle">Choose a series from your library</p>'+(shows.length?genreGroups(shows).map(group=>`<section class="genre-group"><h2>${escape(group.name)}</h2><div class="grid show-grid">${group.items.map(showTile).join('')}</div></section>`).join(''):'<p class="notice">No TV series found in your library</p>')}
}
content.onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-back')){selected=null;season=null;render()}else if(b.dataset.season){season=Number(b.dataset.season);render()}else if(b.dataset.play){play(Number(b.dataset.play))}else if(b.dataset.id){const x=items.find(x=>x.id===Number(b.dataset.id));if(!x)return;if(selected?.show&&!b.dataset.show)play(x.id);else{selected=b.dataset.show?{...showCards(items).find(show=>show.series===x.series),show:true}:{...x,show:false};season=null;render()}}};
function savePrefs(){localStorage.setItem('media-preferences',JSON.stringify(prefs))}
async function settingsTicket(){const response=await timedFetch('/api/media-ticket?scope=settings');if(!response.ok)throw Error('Sign in again.');return encodeURIComponent((await response.json()).ticket)}
async function settingsRequest(ticket,action,credential){const response=await fetch(base+'/api/settings?ticket='+ticket,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,credential})});if(!response.ok)throw Error(action==='credential'?'Credential was rejected by TMDb.':'Server could not update settings.')}
const video=document.querySelector('video'),dialog=document.querySelector('#player'),subtitle=document.querySelector('#subtitle'),audio=document.querySelector('#audio');
const audioSelections=JSON.parse(localStorage.getItem('media-audio-tracks')||'{}');
let current,playRequest=0,streamTicket='',resumeAt=0,hlsClient=null,streamMode='mp4',hlsKeepalive=null;
let streamKey=null,pendingSeek=null,seekTimer=null;
let streamStart=0,streamDuration=0;
const stage=document.querySelector('#video-stage'),playPause=document.querySelector('#play-pause');
const seekControl=document.querySelector('#video-seek'),timeLabel=document.querySelector('#video-time');
const trackMenu=document.querySelector('#track-menu'),subtitleToggle=document.querySelector('#subtitle-toggle');
const volumeControl=document.querySelector('#video-volume'),muteButton=document.querySelector('#video-mute');
let controlsTimer=null,pendingTap=null,scrubbing=false;
function clockTime(seconds){
    if(!Number.isFinite(seconds))return '0:00';
    const total=Math.floor(Math.max(0,seconds));
    const hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),remainder=String(total%60).padStart(2,'0');
    return hours?`${hours}:${String(minutes).padStart(2,'0')}:${remainder}`:`${minutes}:${remainder}`;
}
function updateTimeline(){
    const duration=streamDuration||((Number.isFinite(video.duration)&&video.duration>0)?video.duration:0);
    const position=pendingSeek??(streamMode==='hls'?streamStart+(video.currentTime||0):video.currentTime||0);
    timeLabel.textContent=clockTime(position)+' / '+clockTime(duration);
    seekControl.disabled=!duration;
    seekControl.max=String(duration||100);
    if(!scrubbing)seekControl.value=String(Math.min(position,duration||100));
}
function showControls(){
    stage.classList.remove('controls-hidden');
    clearTimeout(controlsTimer);
    if(!video.paused&&trackMenu.hidden)controlsTimer=setTimeout(()=>stage.classList.add('controls-hidden'),3000);
}
function syncPlaybackButton(){
    playPause.classList.toggle('is-playing',!video.paused);
    playPause.setAttribute('aria-label',video.paused?'Play':'Pause');
    playPause.title=video.paused?'Play':'Pause';
    showControls();
}
function togglePlayback(){
    closeTrackMenu();
    if(!video.currentSrc)return;
    if(video.paused)video.play().catch(()=>{});
    else video.pause();
    showControls();
}
function seekBy(seconds){
    if(!current||!dialog.open||(!video.currentSrc&&pendingSeek===null))return;
    const duration=streamDuration||video.duration;
    if(!Number.isFinite(duration)||duration<=0)return;
    const position=pendingSeek??((streamMode==='hls'?streamStart:0)+(video.currentTime||0));
    seekTo(Math.max(0,Math.min(duration-.5,position+seconds)));
    updateTimeline();
    showControls();
}
function seekTo(position){
    if(streamMode==='hls'){
        const relative=position-streamStart;
        const available=video.seekable.length?video.seekable.end(video.seekable.length-1):0;
        if(pendingSeek!==null||relative<0||relative>available-1){
            pendingSeek=position;
            updateTimeline();
            clearTimeout(seekTimer);
            seekTimer=setTimeout(()=>restartAt(pendingSeek),180);
            return;
        }
        video.currentTime=relative;
    }else video.currentTime=position;
}
async function stopReplacedStream(key){
    if(!key)return;
    try{
        const ticket=await settingsTicket();
        await timedFetch(base+'/api/streams?ticket='+ticket,{method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify({key})},4000);
    }catch{}
}
function cancelPendingTap(){if(pendingTap){clearTimeout(pendingTap.timer);pendingTap=null}}
stage.addEventListener('click',event=>{
    if(event.target.closest('button,input,select,.track-menu')){cancelPendingTap();return}
    event.preventDefault();
    const bounds=stage.getBoundingClientRect(),position=(event.clientX-bounds.left)/bounds.width;
    const side=position<.4?'left':position>.6?'right':'center';
    const now=performance.now();
    if(pendingTap){
        const previous=pendingTap;
        cancelPendingTap();
        if(side===previous.side&&side!=='center'&&now-previous.when<350){seekBy(side==='left'?-10:10);return}
        togglePlayback();
    }
    pendingTap={side,when:now,timer:setTimeout(()=>{pendingTap=null;togglePlayback()},350)};
});
stage.addEventListener('dblclick',event=>event.preventDefault());
stage.addEventListener('pointermove',showControls);
stage.addEventListener('keydown',event=>{
    if(event.target!==stage)return;
    if(event.key===' '||event.key==='Enter'){event.preventDefault();togglePlayback()}
    if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
        event.preventDefault();seekBy(event.key==='ArrowLeft'?-10:10);
    }
});
playPause.onclick=togglePlayback;
document.querySelector('#skip-back').onclick=()=>seekBy(-10);
document.querySelector('#skip-forward').onclick=()=>seekBy(10);
seekControl.addEventListener('input',()=>{scrubbing=true;timeLabel.textContent=clockTime(Number(seekControl.value))+' / '+clockTime(streamDuration||video.duration)});
seekControl.addEventListener('change',()=>{
    if(Number.isFinite(streamDuration||video.duration))try{seekTo(Number(seekControl.value))}catch{}
    scrubbing=false;updateTimeline();showControls();
});
function closeTrackMenu(){
    trackMenu.hidden=true;
    subtitleToggle.setAttribute('aria-expanded','false');
    showControls();
}
subtitleToggle.onclick=()=>{
    if(!trackMenu.hidden){closeTrackMenu();return}
    trackMenu.hidden=false;
    subtitleToggle.setAttribute('aria-expanded','true');
    showControls();
};
function dismissTrackMenu(event){
    if(!dialog.open||trackMenu.hidden||event.target.closest('#track-menu,#subtitle-toggle'))return;
    closeTrackMenu();
}
document.addEventListener('pointerdown',dismissTrackMenu);
document.addEventListener('click',dismissTrackMenu);
volumeControl.value=String(prefs.volume);
volumeControl.oninput=()=>{video.volume=Number(volumeControl.value)/100;video.muted=false};
muteButton.onclick=()=>{video.muted=!video.muted};
video.addEventListener('volumechange',()=>{
    muteButton.classList.toggle('is-muted',video.muted||video.volume===0);
    muteButton.setAttribute('aria-label',video.muted?'Unmute':'Mute');
    volumeControl.value=String(Math.round(video.volume*100));
});
video.addEventListener('timeupdate',updateTimeline);
video.addEventListener('durationchange',updateTimeline);
video.addEventListener('loadedmetadata',updateTimeline);
video.addEventListener('play',syncPlaybackButton);
video.addEventListener('pause',syncPlaybackButton);
video.addEventListener('ended',syncPlaybackButton);
function collapseExpandedPlayer(){
    stage.classList.remove('expanded');
    document.body.classList.remove('player-expanded');
    document.querySelector('#video-fullscreen').setAttribute('aria-label','Expand player');
}
document.querySelector('#video-fullscreen').onclick=async()=>{
    if(stage.classList.contains('expanded')){collapseExpandedPlayer();return}
    if(document.fullscreenElement===stage){await document.exitFullscreen();return}
    if(!/iPhone|iPad|iPod/.test(navigator.userAgent)&&stage.requestFullscreen){
        try{await stage.requestFullscreen();showControls();return}catch{}
    }
    stage.classList.add('expanded');
    document.body.classList.add('player-expanded');
    document.querySelector('#video-fullscreen').setAttribute('aria-label','Shrink player');
    showControls();
};
document.addEventListener('fullscreenchange',showControls);
function audioLabel(track,number){
    const names={eng:'English',en:'English',spa:'Spanish',es:'Spanish',rus:'Russian',ru:'Russian',fra:'French',fre:'French',fr:'French',deu:'German',ger:'German',de:'German',ita:'Italian',it:'Italian',jpn:'Japanese',ja:'Japanese',por:'Portuguese',pt:'Portuguese'};
    const language=names[(track.language||'').toLowerCase()]||track.language?.toUpperCase()||'Unknown language';
    const title=track.title?.trim();
    return (title&&title.toLowerCase()!==language.toLowerCase()?language+' · '+title:language)+' · Track '+number;
}
async function play(id){
    const request=++playRequest;
    current=items.find(x=>x.id===id);
    if(!current)return;
    clearTimeout(seekTimer);
    pendingSeek=null;streamKey=null;
    resumeAt=0;
    streamStart=0;streamDuration=0;streamMode='mp4';
    cancelPendingTap();
    closeTrackMenu();
    updateTimeline();
    showControls();
    document.querySelector('#playing').textContent=current.name;
    document.querySelector('#play-error').textContent='';
    subtitle.innerHTML='<option value="">Off</option>';
    audio.innerHTML='<option value="">Checking audio…</option>';
    dialog.showModal();
    try{
        await syncProgress();
        if(request!==playRequest||!dialog.open)return;
        resumeAt=prefs.resume?(history[id]?.position||current.progress||0):0;
        if(!base)throw Error();
        const response=await fetch('/api/media-ticket?scope=stream&id='+id);
        if(!response.ok)throw Error();
        streamTicket=encodeURIComponent((await response.json()).ticket);
        const [tracksResponse,audioResponse]=await Promise.all([
            fetch(base+'/api/subtitles/'+id+'?ticket='+streamTicket),
            fetch(base+'/api/audio/'+id+'?ticket='+streamTicket)
        ]);
        if(!tracksResponse.ok||!audioResponse.ok)throw Error();
        const [tracks,audioTracks]=await Promise.all([tracksResponse.json(),audioResponse.json()]);
        if(request!==playRequest||!dialog.open)return;
        for(const track of tracks)subtitle.add(new Option(track.label,track.id));
        subtitle.value=prefs.subtitles&&tracks.length?tracks[0].id:'';
        audio.innerHTML='';
        for(const [index,track] of audioTracks.entries())audio.add(new Option(audioLabel(track,index+1),track.id));
        if(audioTracks.length){
            const saved=audioTracks.find(track=>track.id===audioSelections[id]);
            audio.value=(saved||audioTracks.find(track=>track.default)||audioTracks[0]).id;
            audio.disabled=audioTracks.length===1;
        }else{
            audio.add(new Option('No audio tracks',''));
            audio.disabled=true;
        }
        await prepareVideo(request);
    }catch(error){
        if(request===playRequest)document.querySelector('#play-error').textContent=
            error.message==='This video or selected track could not be prepared.'?error.message:'The media server is unavailable.';
    }
}
async function prepareVideo(request){
    const id=current.id,sub=subtitle.value,selectedAudio=audio.value;
    const query='?ticket='+streamTicket+(sub?'&sub='+encodeURIComponent(sub):'')+
        (selectedAudio?'&audio='+encodeURIComponent(selectedAudio):'')+
        '&start='+encodeURIComponent(Math.floor(resumeAt||0));
    while(request===playRequest&&dialog.open){
        const check=await fetch(base+'/api/playable/'+id+query);
        if(!check.ok)throw Error();
        const state=await check.json();
        if(request!==playRequest||!dialog.open)return;
        streamKey=state.format==='hls'?state.hlsKey:null;
        if(state.status==='error')throw Error(state.error||'This video or selected track could not be prepared.');
        if(state.status==='ready'){
            streamMode=state.format;
            streamStart=state.format==='hls'?(state.start||0):0;
            streamDuration=Number(state.duration)||0;
            if(state.format==='hls')resumeAt=0;
            pendingSeek=null;
            video.volume=prefs.volume/100;
            if(state.format==='hls'){
                clearInterval(hlsKeepalive);
                hlsKeepalive=setInterval(()=>{
                    if(request===playRequest&&dialog.open)fetch(base+'/api/playable/'+id+query,{cache:'no-store'}).catch(()=>{});
                },60000);
                const url=base+'/api/hls/'+id+'/'+state.hlsKey+'/index.m3u8?ticket='+streamTicket;
                if(video.canPlayType('application/vnd.apple.mpegurl')&&/iPhone|iPad|iPod/.test(navigator.userAgent)){
                    video.src=url;
                    video.play().catch(()=>{});
                }else if(window.Hls?.isSupported()){
                    const mobile=/Mobi|Android|iPhone|iPad|iPod/.test(navigator.userAgent);
                    hlsClient=new Hls({startPosition:0,maxBufferLength:600,maxMaxBufferLength:600,
                        maxBufferSize:(mobile?256:768)*1024*1024,backBufferLength:30});
                    let started=false;
                    const startWhenBuffered=()=>{
                        if(started||request!==playRequest||!dialog.open)return;
                        tryResume();
                        let ahead=0;
                        for(let n=0;n<video.buffered.length;n++){
                            if(video.buffered.start(n)<=video.currentTime+.5&&video.buffered.end(n)>video.currentTime){
                                ahead=video.buffered.end(n)-video.currentTime;
                                break;
                            }
                        }
                        const finished=hlsClient.levels[0]?.details?.live===false;
                        const needed=finished&&Number.isFinite(video.duration)?Math.min(8,Math.max(1,video.duration-.2)):8;
                        if(ahead>=needed){
                            started=true;
                            document.querySelector('#play-error').textContent='';
                            video.play().catch(()=>{});
                        }else{
                            document.querySelector('#play-error').textContent=`Buffering on this device: ${Math.floor(ahead)}/${Math.ceil(needed)} seconds`;
                        }
                    };
                    hlsClient.on(Hls.Events.MANIFEST_PARSED,startWhenBuffered);
                    hlsClient.on(Hls.Events.FRAG_BUFFERED,startWhenBuffered);
                    hlsClient.on(Hls.Events.ERROR,(_,data)=>{
                        if(data.fatal)document.querySelector('#play-error').textContent='The stream stopped. Close and reopen the player to retry.';
                    });
                    hlsClient.loadSource(url);
                    hlsClient.attachMedia(video);
                }else if(video.canPlayType('application/vnd.apple.mpegurl')){
                    video.src=url;
                    video.play().catch(()=>{});
                }else throw Error('This browser cannot play the media stream.');
            }else{
                video.src=base+'/api/stream/'+id+query;
                if(video.readyState>=1){tryResume();video.play().catch(()=>{})}
                else video.addEventListener('loadedmetadata',()=>{
                    if(request!==playRequest||!dialog.open)return;
                    tryResume();video.play().catch(()=>{});
                },{once:true});
            }
            if(!hlsClient)document.querySelector('#play-error').textContent='';
            return;
        }
        document.querySelector('#play-error').textContent='Starting stream on local media server…';
        await new Promise(resolve=>setTimeout(resolve,3000));
    }
}
async function changeTrack(){
    if(!current)return;
    const request=++playRequest;
    clearTimeout(seekTimer);
    resumeAt=pendingSeek??((streamMode==='hls'?streamStart:0)+(video.currentTime||0)||resumeAt||0);
    pendingSeek=resumeAt;
    const oldKey=streamKey;
    streamKey=null;
    video.pause();
    clearInterval(hlsKeepalive);
    hlsKeepalive=null;
    if(hlsClient){hlsClient.destroy();hlsClient=null}
    video.removeAttribute('src');
    video.load();
    await stopReplacedStream(oldKey);
    if(request!==playRequest||!dialog.open)return;
    try{await prepareVideo(request)}
    catch{document.querySelector('#play-error').textContent='This audio or subtitle track could not be prepared.'}
}
async function restartAt(position){
    if(!current||!dialog.open)return;
    clearTimeout(seekTimer);
    const request=++playRequest;
    resumeAt=position;
    pendingSeek=position;
    const oldKey=streamKey;
    streamKey=null;
    video.pause();
    clearInterval(hlsKeepalive);hlsKeepalive=null;
    if(hlsClient){hlsClient.destroy();hlsClient=null}
    video.removeAttribute('src');video.load();
    document.querySelector('#play-error').textContent='Starting near '+clockTime(position)+' on local media server…';
    updateTimeline();
    await stopReplacedStream(oldKey);
    if(request!==playRequest||!dialog.open)return;
    try{await prepareVideo(request)}
    catch{if(request===playRequest)document.querySelector('#play-error').textContent='Could not start playback at that position.'}
}
subtitle.onchange=changeTrack;
audio.onchange=()=>{
    if(current){audioSelections[current.id]=audio.value;localStorage.setItem('media-audio-tracks',JSON.stringify(audioSelections))}
    changeTrack();
};
function tryResume(){
    if(!resumeAt)return;
    if(streamMode==='hls')return;
    const available=video.duration;
    if(available>resumeAt+1){video.currentTime=resumeAt;resumeAt=0}
}
video.onloadedmetadata=tryResume;
video.ondurationchange=tryResume;
video.oncanplay=tryResume;
video.onerror=()=>document.querySelector('#play-error').textContent='This video could not play. The server may be offline, or this file needs conversion to a browser-supported format.';
let lastSave=0;
function recordProgress(force=false){
    tryResume();
    if(pendingSeek!==null||!current||!video.currentSrc||!video.readyState||(!force&&Date.now()-lastSave<5000))return;
    const duration=streamDuration||video.duration;
    if(!Number.isFinite(duration)||duration<=0)return;
    const position=Math.min(duration,(streamMode==='hls'?streamStart:0)+(video.currentTime||0));
    if(!Number.isFinite(position)||position<0)return;
    lastSave=Date.now();
    history[current.id]={position,duration,updated:lastSave};
    saveLocalHistory();
    queueProgress(current.id,history[current.id]);
}
video.ontimeupdate=()=>recordProgress();
video.addEventListener('pause',()=>{recordProgress(true);flushProgress()});
video.addEventListener('ended',()=>{recordProgress(true);flushProgress()});
window.addEventListener('pagehide',()=>{recordProgress(true);flushProgress()});
function close(){
    recordProgress(true);
    flushProgress();
    playRequest++;
    clearTimeout(seekTimer);
    pendingSeek=null;streamKey=null;
    cancelPendingTap();
    clearTimeout(controlsTimer);
    closeTrackMenu();
    collapseExpandedPlayer();
    if(document.fullscreenElement===stage)document.exitFullscreen().catch(()=>{});
    video.pause();
    clearInterval(hlsKeepalive);
    hlsKeepalive=null;
    if(hlsClient){hlsClient.destroy();hlsClient=null}
    video.removeAttribute('src');
    video.load();
    render();
}
document.querySelector('#close').onclick=()=>dialog.close();
dialog.onclose=close;
let loadingLibrary=false;
async function load(background=false){
    if(loadingLibrary)return;
    loadingLibrary=true;
    const wasOffline=document.querySelector('#connection').textContent==='media server offline';
    try{
        if(!base)throw Error();
        const ticketResponse=await timedFetch('/api/media-ticket?scope=library');
        if(ticketResponse.status===401){location.replace('/login.html');return}
        if(!ticketResponse.ok)throw Error();
        const ticket=(await ticketResponse.json()).ticket;
        const response=await timedFetch(base+'/api/library?ticket='+encodeURIComponent(ticket),{cache:'no-store'},15000);
        if(!response.ok)throw Error();
        const nextItems=await response.json();
        const changed=JSON.stringify(nextItems)!==JSON.stringify(items);
        const progressChanged=await syncProgress();
        if(background&&!changed&&!progressChanged){
            document.querySelector('#connection').textContent=items.length+' titles';
            if(wasOffline&&page==='Settings')render();
            return;
        }
        items=nextItems;
        if(page==='Settings'){
            const count=document.querySelector('#library-count');
            if(count)count.textContent=items.length+' videos on server';
        }
        let moved=false;
        for(const item of items){
            for(const oldId of item.priorIds||[]){
                if(history[oldId]){
                    if(!history[item.id])history[item.id]=history[oldId];
                    delete history[oldId];moved=true;
                }
                if(selected?.id===Number(oldId))selected={...item,show:selected.show};
            }
        }
        if(moved){saveLocalHistory();syncProgress()}
        if(selected){
            const fresh=items.find(item=>item.id===selected.id)
                ||(selected.show?items.find(item=>item.kind==='tv'&&item.series===selected.series):null);
            if(fresh){
                const card=selected.show?showCards(items).find(show=>show.series===fresh.series):fresh;
                if(card)selected={...card,show:selected.show};
            }else selected=null;
        }
        document.querySelector('#connection').textContent=items.length+' titles';
        if(page!=='Settings'||!background||wasOffline)render();
    }catch{
        document.querySelector('#connection').textContent='media server offline';
        if(!background&&!items.length){
            content.innerHTML='<h1>MediaServer Published</h1><p class="notice">The media server is unavailable. Please try again shortly.</p><button id="retry">Retry</button>';
            document.querySelector('#retry').onclick=()=>load();
        }
    }finally{
        loadingLibrary=false;
    }
}
setInterval(()=>{
    if(!document.hidden&&!dialog.open)load(true);
},30000);
document.addEventListener('visibilitychange',()=>{
    if(!document.hidden&&!dialog.open)load(true);
});
load();
