const mediaMenu=document.createElement('div');
mediaMenu.id='media-menu';
mediaMenu.hidden=true;
mediaMenu.innerHTML='<button data-action="search">Search on TMDb</button><button data-action="rename">Rename title</button><button data-action="subtitles">Download subtitles</button><button data-action="delete">Delete from server</button>';
document.body.append(mediaMenu);

const manageDialog=document.createElement('dialog');
manageDialog.id='manage-dialog';
manageDialog.innerHTML='<button class="manage-close" aria-label="Close">×</button><h2 id="manage-title"></h2><div id="manage-body"></div><p id="manage-status" role="status"></p>';
document.body.append(manageDialog);
manageDialog.querySelector('.manage-close').onclick=()=>manageDialog.close();

const manageNotices=document.createElement('div');
manageNotices.id='manage-notices';
manageNotices.setAttribute('aria-live','polite');
document.body.append(manageNotices);
const deletesInFlight=new Set();
function deletionNotice(title,initial='Deleting from local media server…'){
    const notice=document.createElement('div');
    notice.className='manage-notice';
    const copy=document.createElement('div');
    const heading=document.createElement('strong');
    heading.textContent=title;
    const status=document.createElement('span');
    status.textContent=initial;
    copy.append(heading,status);
    const dismiss=document.createElement('button');
    dismiss.type='button';
    dismiss.setAttribute('aria-label','Dismiss notification');
    dismiss.textContent='×';
    dismiss.onclick=()=>notice.remove();
    notice.append(copy,dismiss);
    manageNotices.prepend(notice);
    return (message,failed=false)=>{
        status.textContent=message;
        notice.classList.toggle('failed',failed);
        if(!failed)setTimeout(()=>notice.remove(),5000);
    };
}

const previousRender=render;
render=function(){
    previousRender();
    const hero=content.querySelector('.detail-hero');
    if(hero&&selected){
        const kind=selected.show?'series':'movie';
        hero.dataset.manageKind=kind;
        hero.dataset.id=String(selected.id);
        hero.insertAdjacentHTML('beforeend',`<button class="detail-more" data-manage-kind="${kind}" data-id="${selected.id}" aria-label="More actions">⋯</button>`);
    }
};

let manageTarget=null,longPress=null,downX=0,downY=0,suppressClickUntil=0,searchSerial=0;
const targetNode=node=>node?.closest?.('[data-manage-kind][data-id]');
function closeMediaMenu(){mediaMenu.hidden=true}
function openMediaMenu(node,x,y){
    const element=targetNode(node);
    if(!element)return;
    const id=Number(element.dataset.id),kind=element.dataset.manageKind;
    if(!items.some(item=>item.id===id)||!['movie','series','episode'].includes(kind))return;
    manageTarget={id,kind};
    mediaMenu.querySelector('[data-action="subtitles"]').hidden=kind==='series';
    mediaMenu.hidden=false;
    const width=200,height=kind==='series'?144:190;
    mediaMenu.style.left=Math.max(8,Math.min(x,innerWidth-width-8))+'px';
    mediaMenu.style.top=Math.max(8,Math.min(y,innerHeight-height-8))+'px';
}
content.addEventListener('click',event=>{
    const more=event.target.closest('.detail-more');
    if(!more)return;
    event.preventDefault();event.stopImmediatePropagation();
    const box=more.getBoundingClientRect();
    openMediaMenu(more,box.left,box.bottom+5);
},true);
document.addEventListener('contextmenu',event=>{
    const node=targetNode(event.target);
    if(!node)return;
    event.preventDefault();openMediaMenu(node,event.clientX,event.clientY);
});
document.addEventListener('pointerdown',event=>{
    if(!mediaMenu.contains(event.target))closeMediaMenu();
    if(!['touch','pen'].includes(event.pointerType))return;
    const node=targetNode(event.target);
    if(!node)return;
    downX=event.clientX;downY=event.clientY;
    longPress=setTimeout(()=>{
        openMediaMenu(node,downX,downY);
        suppressClickUntil=Date.now()+900;
        longPress=null;
    },550);
});
document.addEventListener('pointermove',event=>{
    if(longPress&&Math.hypot(event.clientX-downX,event.clientY-downY)>12){clearTimeout(longPress);longPress=null}
});
for(const name of ['pointerup','pointercancel'])document.addEventListener(name,()=>{
    if(longPress){clearTimeout(longPress);longPress=null}
});
document.addEventListener('click',event=>{
    if(Date.now()<suppressClickUntil&&targetNode(event.target)){
        event.preventDefault();event.stopImmediatePropagation();
    }
},true);
document.addEventListener('scroll',closeMediaMenu,true);

function currentItem(){return items.find(item=>item.id===manageTarget?.id)}
function managedTitle(item){return manageTarget.kind==='series'?item.series:item.name}
async function manageTicket(){
    const response=await fetch('/api/media-ticket?scope=manage');
    if(!response.ok)throw Error('Sign in again.');
    return encodeURIComponent((await response.json()).ticket);
}
async function managePost(payload){
    const ticket=await manageTicket();
    const response=await fetch(base+'/api/manage?ticket='+ticket,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
    });
    if(!response.ok)throw Error(response.status===404?'This title is no longer on local media server.':'local media server could not make that change.');
    return response.json();
}
async function finishManage(){
    manageDialog.close();selected=null;season=null;
    await load();
}
function showManageDialog(title,markup){
    manageDialog.querySelector('#manage-title').textContent=title;
    manageDialog.querySelector('#manage-body').innerHTML=markup;
    manageDialog.querySelector('#manage-status').textContent='';
    manageDialog.showModal();
}
function setManageStatus(message){manageDialog.querySelector('#manage-status').textContent=message}
function searchInitial(item){
    const name=manageTarget.kind==='movie'?item.name:item.series;
    return name.replace(/\b(?:19|20)\d{2}\b.*$/,'').replace(/\b(?:2160p|1080p|720p|bluray|webrip|web-dl|x264|x265)\b.*$/i,'').replace(/[._]+/g,' ').trim();
}
async function searchTmdb(term){
    const serial=++searchSerial;
    const results=manageDialog.querySelector('#manage-results');
    setManageStatus('Searching TMDb…');results.innerHTML='';
    try{
        const ticket=await manageTicket();
        const url=base+'/api/tmdb/search?ticket='+ticket+'&kind='+manageTarget.kind+'&q='+encodeURIComponent(term);
        const response=await fetch(url);
        if(!response.ok)throw Error('TMDb search is unavailable.');
        const matches=await response.json();
        if(serial!==searchSerial||!manageDialog.open)return;
        results.innerHTML=matches.length?matches.map(match=>`<button class="tmdb-result" data-tmdb-id="${match.id}">${match.poster?`<img src="${escape(match.poster)}" alt="">`:''}<span><strong>${escape(match.title)}</strong><small>${escape(match.year)}</small><span>${escape(match.overview)}</span></span></button>`).join(''):'<p class="notice">No matches. Try a shorter title.</p>';
        setManageStatus('Choose the correct title.');
    }catch(error){if(serial===searchSerial&&manageDialog.open)setManageStatus(error.message)}
}
mediaMenu.onclick=async event=>{
    const action=event.target.closest('[data-action]')?.dataset.action;
    if(!action||!manageTarget)return;
    closeMediaMenu();
    const item=currentItem();if(!item)return;
    const target={kind:manageTarget.kind,id:manageTarget.id};
    if(action==='subtitles'){
        if(target.kind==='series')return;
        const updateNotice=deletionNotice(item.name,'Downloading English subtitles…');
        try{
            const ticketResponse=await fetch('/api/media-ticket?scope=stream&id='+target.id);
            if(!ticketResponse.ok)throw Error('Sign in again.');
            const ticket=encodeURIComponent((await ticketResponse.json()).ticket);
            const response=await fetch(base+'/api/subtitles/'+target.id+'?ticket='+ticket,{method:'POST'});
            if(!response.ok)throw Error(response.status===400?'No matching subtitles found, or configure OpenSubtitles in Settings.':'Subtitle download failed. Try again later.');
            updateNotice('English subtitles saved. Reopen the video to select them.');
        }catch(error){updateNotice(error.message,true)}
    }else if(action==='rename'){
        showManageDialog('Rename title',`<form id="rename-form"><label>Title<input id="rename-input" maxlength="140" required value="${escape(managedTitle(item))}"></label><button type="submit" class="primary">Save title</button></form><p class="notice">Changes the library title; the media file stays in place.</p>`);
        manageDialog.querySelector('#rename-form').onsubmit=async event=>{
            event.preventDefault();
            const title=manageDialog.querySelector('#rename-input').value.trim();
            if(!title)return;
            setManageStatus('Saving…');
            try{await managePost({...target,action:'rename',title});await finishManage()}catch(error){setManageStatus(error.message)}
        };
        manageDialog.querySelector('#rename-input').focus();
    }else if(action==='search'){
        showManageDialog('Search on TMDb','<form id="tmdb-form"><label>Search title<input id="tmdb-query" maxlength="120" required></label><button type="submit" class="primary">Search</button></form><div id="manage-results"></div>');
        const input=manageDialog.querySelector('#tmdb-query');input.value=searchInitial(item);input.focus();
        manageDialog.querySelector('#tmdb-form').onsubmit=event=>{event.preventDefault();searchTmdb(input.value.trim())};
        manageDialog.querySelector('#manage-results').onclick=async event=>{
            const result=event.target.closest('[data-tmdb-id]');if(!result)return;
            setManageStatus('Saving TMDb match…');
            try{await managePost({...target,action:'match',tmdbId:Number(result.dataset.tmdbId)});await finishManage()}catch(error){setManageStatus(error.message)}
        };
        if(input.value.length>=2)searchTmdb(input.value);
    }else if(action==='delete'){
        const key=target.kind+':'+target.id;
        if(deletesInFlight.has(key))return;
        const count=target.kind==='series'?items.filter(row=>row.kind==='tv'&&row.series===item.series).length:1;
        const title=managedTitle(item);
        if(!confirm(`Delete ${title} from local media server? ${count} video file${count===1?'':'s'} and matching subtitles will be removed. This cannot be undone.`))return;
        deletesInFlight.add(key);
        const updateNotice=deletionNotice(title);
        try{
            await managePost({...target,action:'delete'});
            const removed=target.kind==='series'?items.filter(row=>row.kind==='tv'&&row.series===item.series):[item];
            const removedIds=new Set(removed.map(row=>row.id));
            for(const row of removed)delete history[row.id];
            localStorage.setItem('media-history',JSON.stringify(history));
            items=items.filter(row=>!removedIds.has(row.id));
            if(selected?.show){
                const card=showCards(items).find(show=>show.series===selected.series);
                if(card){
                    selected={...card,show:true};
                    if(!items.some(row=>row.series===selected.series&&row.season===season))season=null;
                }else{selected=null;season=null}
            }else if(selected&&removedIds.has(selected.id))selected=null;
            const scrollY=window.scrollY;
            document.querySelector('#connection').textContent=items.length+' titles';
            render();
            window.scrollTo({top:scrollY,behavior:'instant'});
            updateNotice('Deleted from local media server');
        }catch(error){updateNotice(error.message,true)}
        finally{deletesInFlight.delete(key)}
    }
};
