const {app, BrowserWindow, dialog, ipcMain, shell} = require('electron');
const {spawn} = require('node:child_process');
const {randomBytes} = require('node:crypto');
const path = require('node:path');
const fs = require('node:fs');
let backend, window, localOrigin;
app.setName('MediaServer Published');
const profilePath=path.join(app.getPath('appData'), 'MediaServerPublished');
fs.mkdirSync(profilePath,{recursive:true});
app.setPath('userData', profilePath);
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {if(window){window.restore();window.show();window.focus();}});
  app.whenReady().then(start).catch(error => {
    dialog.showErrorBox('MediaServer Published', error.message);
    app.quit();
  });
}
function trusted(event) {
  try {return window && event.sender === window.webContents && new URL(event.senderFrame.url).origin === localOrigin;}
  catch {return false;}
}
ipcMain.handle('choose-folder', async event => {
  if (!trusted(event)) throw Error('Folder selection unavailable.');
  const selection = await dialog.showOpenDialog(window, {properties: ['openDirectory']});
  return selection.canceled ? '' : selection.filePaths[0];
});
ipcMain.on('window-minimize', event => {if(trusted(event))window.minimize();});
ipcMain.on('window-maximize', event => {if(trusted(event))window.isMaximized()?window.unmaximize():window.maximize();});
ipcMain.on('window-close', event => {if(trusted(event))window.close();});
async function start() {
  const root = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..');
  const token = randomBytes(32).toString('hex');
  const executable = app.isPackaged ? path.join(root, 'backend', 'media-backend.exe') : (process.env.PYTHON || 'python');
  const args = [...(app.isPackaged ? [] : [path.join(root, 'backend.py')]),
    '--data', path.join(process.env.LOCALAPPDATA || app.getPath('userData'), 'MediaServerPublished', 'data'),
    '--public', path.join(root, 'public')];
  backend = spawn(executable, args, {windowsHide:true, stdio:['pipe','pipe','pipe'], env:{...process.env, MEDIA_PUBLISHED_BOOTSTRAP:token}});
  let detail = '';
  backend.stderr.on('data', data => {detail=(detail+data.toString()).slice(-2000);});
  localOrigin = await new Promise((resolve,reject) => {
    const timer=setTimeout(() => reject(Error('The local media server did not start.')), 30000);
    let output='';
    backend.once('error', error => {clearTimeout(timer);reject(error);});
    backend.once('exit', () => {clearTimeout(timer);reject(Error('The local media server stopped. '+detail));});
    backend.stdout.on('data', data => {
      output+=data.toString();
      const line=output.split('\n').slice(0,-1).find(row => row.startsWith('MEDIA_PUBLISHED_READY='));
      if(line){clearTimeout(timer);resolve(line.split('=')[1].trim());}
    });
  });
  window = new BrowserWindow({width:1320,height:880,minWidth:760,minHeight:540,
    title:'MediaServer Published',backgroundColor:'#080808',frame:false,
    webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true}});
  window.webContents.setWindowOpenHandler(({url}) => {
    if(url === 'https://ffmpeg.org/download.html')shell.openExternal(url);
    return {action:'deny'};
  });
  window.webContents.on('will-navigate', (event,url) => {
    if(new URL(url).origin !== localOrigin)event.preventDefault();
  });
  window.webContents.session.setPermissionRequestHandler((_contents, permission, callback) => callback(permission==='clipboard-sanitized-write'));
  window.webContents.session.setPermissionCheckHandler(() => false);
  await window.loadURL(`${localOrigin}/bootstrap?token=${token}`);
  backend.once('exit', () => {if(!app.isQuitting){dialog.showErrorBox('MediaServer Published','The local media server stopped. Reopen the app.');app.quit();}});
}
app.on('before-quit', () => {app.isQuitting=true;if(backend?.stdin.writable)backend.stdin.end('shutdown\n');});
app.on('window-all-closed', () => app.quit());
