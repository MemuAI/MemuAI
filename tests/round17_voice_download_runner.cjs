
const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const source = fs.readFileSync(path.join(__dirname,'../app/pgstatic/voice-player.js'),'utf8')
  .replace('return { html, stash, hydrate, closeMenu };','return { html, stash, hydrate, closeMenu, downloadAudio };');
const bytes = Buffer.from('RIFF\0\0\0\0WAVEfmt fixture PCM recording'), fixture = new Blob([bytes],{type:'audio/wav'});
let profile, chat, sourceUrl, connected, saved, notified, response, readerHook, saveHook, fetchHook;
class Reader {
  readAsDataURL(blob) { blob.arrayBuffer().then(array => { readerHook?.();this.result='data:'+blob.type+';base64,'+Buffer.from(array).toString('base64');this.onload(); }).catch(() => this.onerror()); }
}
const context = vm.createContext({URL, Blob, FileReader:Reader, PG_LANG:'en',
  location:{href:'http://127.0.0.1:8173/',origin:'http://127.0.0.1:8173'},
  document:{addEventListener(){}},window:{addEventListener(){}},
  activeChatProfileId:()=>profile,S:{currentId:'fixture-chat'},profileSwitching:false,
  fetch:async()=>{fetchHook?.();return response;},
  saveToDownloads:async payload=>{saved.push(payload);saveHook?.();return {ok:true,name:payload.name,path:'fixture-path'};},
  savedToast:result=>notified.push(result)});
vm.runInContext(source+'\nglobalThis.player=MemuVoicePlayer',context);
const root={dataset:{profile:'default',chat:'fixture-chat',mime:'audio/wav'},get isConnected(){return connected;}},
      audio={getAttribute:()=>sourceUrl};
function reset() {
  profile='default';context.S.currentId='fixture-chat';context.profileSwitching=false;connected=true;saved=[];notified=[];readerHook=saveHook=fetchHook=null;
  sourceUrl='/api/voice/clips/'+'a'.repeat(64)+'.wav?profile_id=default';
  response={ok:true,blob:async()=>fixture};
}
(async()=>{
  reset();await context.player.downloadAudio(root,audio);
  assert.equal(saved.length,1);assert.equal(notified.length,1);assert.equal(saved[0].name,'MemuAI-voice.wav');
  assert.deepEqual(Buffer.from(saved[0].url.split(',')[1],'base64'),bytes,'download bridge receives actual audio bytes');
  reset();sourceUrl='blob:http://127.0.0.1:8173/fixture-id';response={ok:true,blob:async()=>new Blob([bytes],{type:'audio/ogg'})};
  await context.player.downloadAudio(root,audio);assert.equal(saved[0].name,'MemuAI-voice.ogg','temporary clips infer format');
  for(const hook of ['fetch','blob','reader']){
    reset();if(hook==='fetch')fetchHook=()=>profile='other';if(hook==='blob')response.blob=async()=>{profile='other';return fixture;};if(hook==='reader')readerHook=()=>profile='other';
    await context.player.downloadAudio(root,audio);assert.equal(saved.length,0,'profile switch during '+hook+' prevents saving');assert.equal(notified.length,0);
  }
  reset();profile='other';await context.player.downloadAudio(root,audio);assert.equal(saved.length,0,'old profile cannot start export');
  reset();context.profileSwitching=true;await context.player.downloadAudio(root,audio);assert.equal(saved.length,0,'switch in progress blocks export');
  reset();context.S.currentId='other-chat';await context.player.downloadAudio(root,audio);assert.equal(saved.length,0,'chat switch blocks old player');
  reset();connected=false;await context.player.downloadAudio(root,audio);assert.equal(saved.length,0,'removed player cannot export');
  reset();saveHook=()=>profile='other';await context.player.downloadAudio(root,audio);assert.equal(notified.length,0,'completion never notifies another profile');
  reset();response.ok=false;await assert.rejects(context.player.downloadAudio(root,audio),/Could not download/);assert.equal(saved.length,0);
  reset();response.blob=async()=>new Blob([]);await assert.rejects(context.player.downloadAudio(root,audio),/Invalid audio/);
  reset();sourceUrl='/api/voice/clips/'+'a'.repeat(64)+'.wav?profile_id=other';await assert.rejects(context.player.downloadAudio(root,audio),/Audio unavailable/);
  reset();sourceUrl='https://external.example/audio.wav';await assert.rejects(context.player.downloadAudio(root,audio),/Audio unavailable/);
  reset();context.saveToDownloads=async()=>({ok:false});await assert.rejects(context.player.downloadAudio(root,audio),/Could not save/);
  console.log('PASS: production voice download receives actual Blob bytes, exports temporary recordings, rejects errors/external sources, guards profile/chat switches at every await, no stale completion toast');
})().catch(error=>{console.error(error);process.exitCode=1;});
