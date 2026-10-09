const genreChoices=['All','Action','Adventure','Animation','Comedy','Crime','Drama','Science Fiction','Fantasy','Horror','Thriller','Romance','Documentary','Uncategorized'];
const activeGenres={'Home':'All','Movies':'All','TV Shows':'All'};
let sortMode='recent',watchFilter='all';
const sortNames={recent:'Recently added',title:'Title A–Z',year:'Release year'};
const filterNames={all:'All titles',progress:'In progress',unwatched:'Unwatched'};

function sortedLibrary(rows){
    return [...rows].sort((a,b)=>sortMode==='title'
        ?titleKey(a.series||a.name).localeCompare(titleKey(b.series||b.name))
        :sortMode==='year'?(Number(b.year)||0)-(Number(a.year)||0)
        :(b.added_at||0)-(a.added_at||0));
}
function matchesWatch(x){
    const p=progress(x);
    return watchFilter==='all'||(watchFilter==='progress'&&p>0&&p<.95)
        ||(watchFilter==='unwatched'&&p===0&&!x.watched);
}
function matchingLibrary(rows){
    const q=search.value.trim().toLocaleLowerCase();
    return sortedLibrary(rows.filter(x=>(!q||`${x.name||''} ${x.series||''} ${x.genre||''} ${x.year||''}`.toLocaleLowerCase().includes(q))&&matchesWatch(x)));
}
function genreMatches(x){const selectedGenre=activeGenres[page]||'All';return selectedGenre==='All'||(x.genre||'Uncategorized').toLocaleLowerCase()===selectedGenre.toLocaleLowerCase()}
function genreBar(kind){
    const links=kind==='Home'?'<button class="category-link" data-nav="TV Shows">TV Shows</button><button class="category-link" data-nav="Movies">Movies</button>':'';
    return `<div class="genre-strip" aria-label="Browse genres">${links}${genreChoices.map(name=>`<button class="genre-chip ${activeGenres[page]===name?'active':''}" data-genre="${escape(name)}" aria-pressed="${activeGenres[page]===name}">${escape(name)}</button>`).join('')}</div>`;
}
function movieCard(x){
    const detail=[x.year,x.genre,bytesLabel(x.size)].filter(Boolean).join(' · ');
    return `<button class="library-card movie-card" data-id="${x.id}" data-manage-kind="movie">${x.artwork?`<img loading="lazy" src="${escape(artwork(x))}" alt="">`:''}<span class="card-copy"><strong>${escape(x.name)}</strong><small>${escape(detail)}</small><span>${escape(x.overview||'')}</span></span><span class="progress"><i style="width:${Math.min(100,Math.max(0,progress(x)*100))}%"></i></span></button>`;
}
function seriesCard(x){
    const years=x.year?(x.yearEnd&&x.yearEnd!==x.year?`${x.year}–${x.yearEnd}`:x.year):'';
    const seasonCount=x.totalSeasons?`${x.seasons}/${x.totalSeasons} seasons in library`:`${x.seasons} season${x.seasons===1?'':'s'} in library`;
    const remaining=x.totalSeasons>x.seasons?` · ${x.totalSeasons-x.seasons} remaining`:'';
    return `<button class="library-card series-card" data-id="${x.id}" data-manage-kind="series" data-show="true">${x.artwork?`<img loading="lazy" src="${escape(artwork(x))}" alt="">`:''}<span class="card-copy"><strong>${escape(x.series)}</strong><small>${escape([years,seasonCount].filter(Boolean).join(' · ')+remaining)}</small><span>${escape(x.seriesOverview||x.overview||'')}</span></span><span class="progress"><i style="width:${Math.min(100,Math.max(0,progress(x)*100))}%"></i></span></button>`;
}
function miniCard(x,show=false){
    const title=show?x.series:x.kind==='tv'?x.series:x.name;
    const meta=show?`${x.seasons}/${x.totalSeasons||'?'} seasons`:x.kind==='tv'?`S${x.season} E${x.episode}`:x.year||'';
    return `<button class="library-card mini-card" data-id="${x.id}" data-manage-kind="${show?'series':x.kind==='tv'?'episode':'movie'}" ${show?'data-show="true"':''}>${x.artwork?`<img loading="lazy" src="${escape(artwork(x))}" alt="">`:''}<span class="card-copy"><strong>${escape(title)}</strong><small>${escape(meta)}</small></span><span class="progress"><i style="width:${Math.min(100,Math.max(0,progress(x)*100))}%"></i></span></button>`;
}
function libraryRow(title,rows,renderer,see){
    if(!rows.length)return '';
    return `<section class="library-section"><div class="section-head"><h2>${escape(title)}</h2>${see?`<button class="see-all" data-see="${escape(see)}" aria-label="Show all ${escape(title)}">See all <span aria-hidden="true">›</span></button>`:''}</div><div class="shelf">${rows.map(renderer).join('')}</div></section>`;
}
function heroCard(x,resuming){
    const title=x.kind==='tv'?x.series:x.name;
    const detail=[x.year,x.kind==='tv'?`S${x.season} E${x.episode}`:x.genre].filter(Boolean).join(' · ');
    return `<div class="hero featured">${x.artwork?`<img src="${escape(artwork(x))}" alt="">`:''}<div><small>${resuming?'CONTINUE WATCHING':'FROM YOUR LIBRARY'}</small><h1>${escape(title)}</h1><p class="hero-meta">${escape(detail)}</p>${x.overview?`<p class="hero-overview">${escape(x.overview)}</p>`:''}${resuming?`<span class="hero-progress"><i style="width:${Math.min(100,Math.max(0,progress(x)*100))}%"></i></span>`:''}<button class="primary" data-play="${x.id}">${resuming?'Resume':'Play'}</button></div></div>`;
}
function libraryHeading(title,count){return `<div class="page-heading"><div><h1>${title}</h1><p>${count} ${title==='Movies'?'movies':'shows'} in your library</p></div></div>`}

const previousLibraryRender=render;
render=function(){
    document.querySelectorAll('[data-page]').forEach(button=>button.classList.toggle('active',button.dataset.page===page));
    document.querySelector('.toolbar').hidden=page==='Settings'||!!selected;
    if(page==='Settings'||selected){previousLibraryRender();return}
    const filtered=matchingLibrary(items),sectionItems=filtered.filter(genreMatches);
    if(page==='Home'){
        const resume=sectionItems.find(x=>progress(x)>0&&progress(x)<.95);
        const feature=resume||sectionItems[0];
        const watched=sectionItems.filter(x=>history[x.id]||x.watched).sort((a,b)=>(history[b.id]?.updated||b.watched_at||0)-(history[a.id]?.updated||a.watched_at||0));
        content.innerHTML=`${feature?heroCard(feature,!!resume):'<div class="empty-library">No titles match this view.</div>'}<div class="browse-label">Browse your library</div>${genreBar('Home')}`
            +libraryRow('Jump back in',sectionItems.filter(x=>progress(x)>0&&progress(x)<.95).slice(0,12),x=>miniCard(x))
            +libraryRow('Recently added',sectionItems.slice(0,18),x=>miniCard(x))
            +libraryRow('Recently watched',watched.slice(0,12),x=>miniCard(x));
        return;
    }
    if(page==='Movies'){
        const movies=sectionItems.filter(x=>x.kind==='movie');
        const groups=genreGroups(movies).map(group=>({...group,items:sortedLibrary(group.items)}));
        const watched=movies.filter(x=>history[x.id]||x.watched).sort((a,b)=>(history[b.id]?.updated||b.watched_at||0)-(history[a.id]?.updated||a.watched_at||0));
        content.innerHTML=libraryHeading('Movies',filtered.filter(x=>x.kind==='movie').length)+genreBar('Movies')
            +libraryRow('Recently watched',watched.slice(0,12),x=>miniCard(x))
            +(groups.length?groups.map(group=>libraryRow(group.name,group.items,movieCard,group.name)).join(''):'<p class="empty-library">No movies match this view.</p>');
        return;
    }
    const shows=showCards(filtered).filter(genreMatches);
    const groups=genreGroups(shows).map(group=>({...group,items:sortedLibrary(group.items)}));
    const resumed=shows.filter(x=>progress(x)>0&&progress(x)<.95);
    content.innerHTML=libraryHeading('TV Shows',showCards(filtered).length)+genreBar('TV Shows')
        +libraryRow('Continue watching',resumed.slice(0,10),seriesCard)
        +(groups.length?groups.map(group=>libraryRow(group.name,group.items,seriesCard,group.name)).join(''):'<p class="empty-library">No TV shows match this view.</p>');
};

content.addEventListener('click',event=>{
    const navButton=event.target.closest('[data-nav]');
    if(navButton){page=navButton.dataset.nav;selected=null;season=null;render();return}
    const genreButton=event.target.closest('[data-genre]');
    if(genreButton){activeGenres[page]=genreButton.dataset.genre;render();return}
    const see=event.target.closest('[data-see]');
    if(see){activeGenres[page]=see.dataset.see;render();content.scrollIntoView({block:'start',behavior:'smooth'})}
},true);

const searchPanel=document.querySelector('#search-panel'),searchToggle=document.querySelector('#search-toggle');
function closeSearch(){searchPanel.hidden=true;searchToggle.setAttribute('aria-expanded','false')}
searchToggle.onclick=()=>{const opening=searchPanel.hidden;searchPanel.hidden=!opening;searchToggle.setAttribute('aria-expanded',String(opening));if(opening)search.focus()};
document.querySelector('#search-close').onclick=closeSearch;
search.oninput=()=>render();

const toolbarMenu=document.querySelector('#toolbar-menu');
function closeToolbar(){toolbarMenu.hidden=true;document.querySelectorAll('.toolbar button').forEach(button=>button.setAttribute('aria-expanded','false'))}
function openToolbar(button,kind){
    const opening=toolbarMenu.hidden||toolbarMenu.dataset.kind!==kind;
    closeToolbar();if(!opening)return;
    toolbarMenu.dataset.kind=kind;toolbarMenu.hidden=false;button.setAttribute('aria-expanded','true');
    const options=kind==='sort'?sortNames:filterNames,selectedValue=kind==='sort'?sortMode:watchFilter;
    toolbarMenu.innerHTML=Object.entries(options).map(([value,label])=>`<button data-value="${value}" class="${selectedValue===value?'selected':''}">${escape(label)}</button>`).join('');
    const rect=button.getBoundingClientRect();toolbarMenu.style.top=`${rect.bottom+8}px`;toolbarMenu.style.left=`${Math.max(8,Math.min(rect.right-190,innerWidth-198))}px`;
}
document.querySelector('#sort-toggle').onclick=event=>openToolbar(event.currentTarget,'sort');
document.querySelector('#filter-toggle').onclick=event=>openToolbar(event.currentTarget,'filter');
toolbarMenu.onclick=event=>{const choice=event.target.closest('[data-value]');if(!choice)return;if(toolbarMenu.dataset.kind==='sort')sortMode=choice.dataset.value;else watchFilter=choice.dataset.value;closeToolbar();render()};
document.addEventListener('pointerdown',event=>{if(!toolbarMenu.contains(event.target)&&!event.target.closest('.toolbar'))closeToolbar()});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeSearch();closeToolbar()}});
