const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');
const root=path.join(__dirname,'..');
async function createApp(){
  const dom=new JSDOM(fs.readFileSync(path.join(root,'public/index.html'),'utf8'),{url:'http://127.0.0.1:9090/',runScripts:'outside-only',pretendToBeVisual:true});
  const dispose=dom.window.close.bind(dom.window);
  const state={library_path:'',ffmpeg_path:'',qbittorrent_url:'',qbittorrent_username:'',vpn_provider:'external',vpn_interface:'',preferences:{resume:true,subtitles:false,volume:80},storageBytes:1024,ffmpegConfigured:false,tmdbConfigured:false,openSubtitlesConfigured:false,qbittorrentConfigured:false,vpnState:'unknown',qbitBound:false};
  const requests=[];
  dom.window.fetch=async(url,options={})=>{
    const parsed=new URL(url,dom.window.location.href);
    requests.push({url:parsed.pathname,options});
    let result={};
    if(parsed.pathname==='/api/media-ticket')result={ticket:'test-ticket'};
    if(parsed.pathname==='/api/config'&&options.method==='POST')Object.assign(state,JSON.parse(options.body));
    if(['/api/config','/api/settings'].includes(parsed.pathname))result={...state};
    if(parsed.pathname==='/api/library')result=[];
    if(parsed.pathname==='/api/progress')result={records:{}};
    return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
  };
  dom.window.desktop={chooseFolder:async()=> 'C:\\User-selected folder',minimize(){},maximize(){},close(){}};
  dom.window.MEDIA_FIRST_RUN=true;
  dom.window.HTMLMediaElement.prototype.pause=function(){};
  dom.window.HTMLMediaElement.prototype.load=function(){};
  dom.window.eval("localStorage.setItem('media-preferences',JSON.stringify({resume:true,volume:80,subtitles:false}));");
  try{
    for(const file of ['app.js','design.js','manage.js','settings.js'])vm.runInContext(fs.readFileSync(path.join(root,'public',file),'utf8'),dom.getInternalVMContext(),{filename:file});
  }catch(error){dispose();throw error;}
  await new Promise(resolve=>setTimeout(resolve,50));
  return {dom,state,requests,dispose};
}
test('first launch renders editable settings with no selected media or credentials',async()=>{
  const {dom,dispose}=await createApp();
  try{
    const doc=dom.window.document;
    for(const id of ['library-path','ffmpeg-path','qbit-url','qbit-user','qbit-password','vpn-interface'])assert.equal(doc.getElementById(id).value,'');
    assert.equal(doc.querySelector('#desktop-titlebar span').textContent,'MediaServer Published');
    assert.equal(doc.getElementById('add-magnet').disabled,true);
    assert.equal(doc.querySelector('h1').textContent,'Settings');
  }finally{dispose();}
});

test('movie deletion confirmation describes folder removal and cancellation sends no delete',async()=>{
  const {dom,requests,dispose}=await createApp();
  try{
    let warning='';
    dom.window.confirm=message=>{warning=message;return false;};
    vm.runInContext("items=[{id:1234567890123,kind:'movie',name:'Movie'}]; manageTarget={kind:'movie',id:1234567890123};",dom.getInternalVMContext());
    const menu=dom.window.document.getElementById('media-menu');
    await menu.onclick({target:menu.querySelector('[data-action="delete"]')});
    assert.match(warning,/folder and remaining files/);
    assert.match(warning,/no other videos/);
    assert.equal(requests.filter(request=>request.url==='/api/manage').length,0);
  }finally{dispose();}
});
test('native folder selection and saving sends chosen path',async()=>{
  const {dom,state,dispose}=await createApp();
  try{
    dom.window.document.getElementById('choose-library').click();
    await new Promise(resolve=>setTimeout(resolve,20));
    dom.window.document.getElementById('save-library').click();
    await new Promise(resolve=>setTimeout(resolve,50));
    assert.equal(state.library_path,'C:\\User-selected folder');
    assert.equal(dom.window.document.getElementById('library-path').value,state.library_path);
  }finally{dispose();}
});
test('connection settings submit user values and clear password field',async()=>{
  const {dom,state,dispose}=await createApp();
  try{
    const doc=dom.window.document;
    doc.getElementById('qbit-url').value='http://127.0.0.1:8080';
    doc.getElementById('qbit-user').value='my-user';
    doc.getElementById('qbit-password').value='my-test-password';
    doc.getElementById('vpn-interface').value='My VPN';
    doc.getElementById('save-connections').click();
    await new Promise(resolve=>setTimeout(resolve,50));
    assert.equal(state.qbittorrent_username,'my-user');
    assert.equal(state.vpn_interface,'My VPN');
    assert.equal(state.qbittorrent_password,'my-test-password');
    assert.equal(doc.getElementById('qbit-password').value,'');
  }finally{dispose();}
});
test('saved playback preferences use the backend',async()=>{
  const {dom,state,requests,dispose}=await createApp();
  try{
    const checkbox=dom.window.document.getElementById('resume');
    checkbox.checked=false;
    checkbox.dispatchEvent(new dom.window.Event('change'));
    await new Promise(resolve=>setTimeout(resolve,30));
    assert.equal(state.preferences.resume,false);
    assert.ok(requests.some(request=>request.options.body?.includes('preferences')));
  }finally{dispose();}
});
