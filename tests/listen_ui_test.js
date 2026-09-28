const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Container {
  constructor(){
    this.innerHTML = ''; this.listeners = {};
    this.drawer={open:false,summary:{textContent:''},querySelector(selector){return selector === '#listening-summary' ? this.summary : null;}};
  }
  closest(selector){ return selector === 'details' ? this.drawer : null; }
  addEventListener(name, handler){ this.listeners[name] = handler; }
  querySelector(selector){ return selector === '[data-listen-options]' ? this.disclosure : null; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(){ for(let i=0;i<8;i++) await tick(); }
function click(container, action){
  container.listeners.click({target:{closest:selector => selector === '[data-listen-action]' ? {dataset:{listenAction:action}} : null}});
}
function change(container,field,value){ container.listeners.change({target:{dataset:{listenField:field},value}}); }
function speed(container,value){ container.listeners.change({target:{dataset:{listenSpeed:''},value}}); }
const book = {id:'book-9',revision:1,chapters:[{id:'chapter-1'},{id:'chapter-2'}],segments:[
  {id:'segment-1',chapter_id:'chapter-1',start:0,end:11,text:'Mara spoke.',speaker_id:'mara',direction:'Whisper.',audio:{url:'/enhanced.wav',duration:1,asset_id:'enhanced'}},
  {id:'segment-2',chapter_id:'chapter-1',start:12,end:25,text:'Elio replied.',speaker_id:'elio',direction:'Louder.'},
  {id:'segment-3',chapter_id:'chapter-2',start:0,end:6,text:'Dawn.'}
],characters:[{id:'mara',voice:'Puck'}],scenes:[{id:'scene-1',tone:'Tense'}]};
const status = {has_api_key:true,providers:[{id:'system',available:true},{id:'gemini',available:true}],
  system_voices:[{id:'Samantha',name:'Samantha',locale:'en-US'}],tts_models:['gemini-3.8-flash-tts','gemini-3.8-flash-lite-tts'],tts_model:'gemini-3.8-flash-tts'};
const simpleAudio = {url:'/api/books/book-9/listen/audio/simple',duration:2,available:true,asset_id:'simple',mode:'simple'};
const session = {id:'session-1',provider:'system',voice:'',model:'macos-say'};
function environment(handler, prior={}){
  const calls=[];
  const storage=new Map(Object.entries(prior));
  const scope={window:{},setTimeout:(fn,_ms) => setImmediate(fn),localStorage:{
    getItem:key => storage.get(key) || null,setItem:(key,value)=>storage.set(key,value)},fetch:async(url,options={})=>{
      const call={url,method:options.method || 'GET',body:options.body ? JSON.parse(options.body):null};
      calls.push(call);
      const value=await handler(call);
      return {ok:value.ok !== false,status:value.status || 200,json:async()=>value.data};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/listen.js'),'utf8'),scope);
  return {api:scope.window.BardicListen,calls,storage};
}
function options(extra={}){ return {status,chapterId:'chapter-1',segmentId:'segment-1',...extra}; }
function ordinary(call){
  if(call.url.endsWith('/cancel')) return {data:{status:'cancelled'}};
  if(call.method==='POST') return {data:{session,job:{id:'job-1',status:'queued'}}};
  if(call.url.startsWith('/api/jobs?')) return {data:[{id:'job-1',status:'completed',audio:simpleAudio}]};
  if(call.url.includes('/takes?')) return {data:{session,takes:[{segment_id:'segment-1',audio:simpleAudio}]}};
  throw new Error(`Unexpected request ${call.url}`);
}

(async()=>{
  const env=environment(ordinary), container=new Container();
  const before=JSON.stringify(book);
  let playId,stops=0,updates=0,jobs=[];
  const hooks=options({onPlay:id=>{playId=id;},onStop:()=>{stops++;},onChange:()=>{updates++;},onJob:job=>jobs.push(job)});
  await env.api.render(container,book,hooks);
  assert.equal(env.calls.length,0,'Rendering controls never queues a narration');
  assert.equal(container.drawer.open,false,'Rendering leaves the reader foremost');
  assert.match(container.drawer.summary.textContent,/Full cast selected.*Default device voice.*Device.*free on this device/);
  assert.equal(env.api.enabled(book),false);
  assert.equal(env.api.resolve(book,book.segments[0]),book.segments[0].audio);
  assert.ok(container.innerHTML.includes('Start simple listening'));
  assert.ok(container.innerHTML.includes('keeps going through the book'),'Continuous listening is the default');
  assert.ok(container.innerHTML.includes('data-listen-field="continuous" checked'));
  assert.ok(container.innerHTML.includes('aria-label="Simple narrator voice"'));
  assert.match(container.innerHTML, /data-listen-options >(?:\s*)<summary data-listen-summary>More listening options/,
    'Advanced controls start collapsed');
  const advancedStart=container.innerHTML.indexOf('<details');
  assert.ok(container.innerHTML.indexOf('data-listen-action="start"') < advancedStart,
    'The explicit start action is visible without opening advanced settings');
  assert.ok(container.innerHTML.indexOf('Device narration stays') < advancedStart,
    'Provider and privacy disclosures stay beside the start action');
  assert.ok(container.innerHTML.indexOf('data-listen-action="prepare-chapter"') > advancedStart,
    'Chapter preparation remains available in the advanced disclosure');
  container.disclosure={open:true};
  await env.api.render(container,book,hooks);
  assert.match(container.innerHTML, /data-listen-options open>/, 'Redraws preserve an opened disclosure');
  container.disclosure.open=false;
  await env.api.render(container,book,hooks);
  assert.match(container.innerHTML, /data-listen-options >/,'Redraws preserve a closed disclosure');
  assert.equal(env.calls.length,0,'Opening or closing advanced options never requests audio');
  click(container,'start');
  assert.equal(env.api.enabled(book),true);
  assert.equal(playId,'segment-1');
  assert.equal(env.api.resolve(book,book.segments[0]),null,'Simple mode must not mix in the enhanced take');
  assert.equal(env.calls.length,0,'Only parent playback ensure may submit the request');
  const first=env.api.ensure(book,book.segments[0]);
  const second=env.api.ensure(book,book.segments[0]);
  const [audio,duplicate]=await Promise.all([first,second]);
  assert.equal(audio.asset_id,'simple'); assert.equal(duplicate.asset_id,'simple');
  assert.equal(env.calls.filter(call=>call.method==='POST').length,1,'Repeated Play during preparation coalesces');
  assert.deepEqual(env.calls[0].body,{provider:'system',voice:'',model:'macos-say',segment_id:'segment-1'});
  assert.equal(jobs.at(-1).status,'completed');
  assert.equal(env.api.resolve(book,book.segments[0]).url,simpleAudio.url);
  assert.equal(await env.api.ensure(book,book.segments[0]),audio);
  assert.equal(env.calls.filter(call=>call.method==='POST').length,1,'A saved simple take is reused');
  assert.equal(JSON.stringify(book),before,'Listening does not mutate enhanced casting, scenes or audio');
  assert.equal(env.api.allowsAdvance(book,book.segments[0],book.segments[1]),true);
  assert.equal(env.api.allowsAdvance(book,book.segments[1],book.segments[2]),true,'Continuous listening crosses chapters');
  container.listeners.change({target:{dataset:{listenField:'continuous'},checked:false}});
  assert.equal(env.api.allowsAdvance(book,book.segments[1],book.segments[2]),false,'Turning continuous off restores the chapter stop');
  assert.equal(JSON.parse(env.storage.get('bardic:listen:book-9')).continuous,false,'The choice is remembered for the book');
  assert.ok(container.innerHTML.includes('stops at the end of this chapter'));
  change(container,'mode','enhanced');
  assert.equal(env.api.resolve(book,book.segments[0]),book.segments[0].audio);
  assert.equal(env.api.allowsAdvance(book,book.segments[1],book.segments[2]),true);
  assert.ok(stops>=2 && updates>=2);
  change(container,'provider','gemini');
  change(container,'voice','Leda');
  change(container,'model','gemini-3.8-flash-lite-tts');
  assert.ok(container.innerHTML.includes('Google bills each request, including examples'));
  assert.ok(container.innerHTML.includes('value="Leda" selected'));
  assert.match(container.drawer.summary.textContent,/Full cast selected.*Leda.*Gemini · paid/);
  assert.equal(container.drawer.open,false,'Changing narrator settings does not force open the drawer');
  const persisted=JSON.parse(env.storage.get('bardic:listen:book-9'));
  assert.equal(persisted.provider,'gemini');
  assert.equal(persisted.voices.gemini,'Leda');
  assert.equal(persisted.model,'gemini-3.8-flash-lite-tts');

  // Audition controls are passive until clicked, use the contextual source, and
  // do not turn an enhanced reader into simple listening or start its queue.
  const auditions=environment(()=>{throw new Error('An audition callback must not prepare listening audio');});
  const auditionContainer=new Container();
  const previews=[],rates=[];
  let toggles=0,auditionStops=0,auditionPlay;
  const auditionHooks=options({playbackRate:2.5,onPreview:value=>previews.push(JSON.parse(JSON.stringify(value))),
    onRateChange:rate=>rates.push(rate),onToggle:()=>toggles++,onStop:()=>auditionStops++,onPlay:id=>{auditionPlay=id;}});
  await auditions.api.render(auditionContainer,book,auditionHooks);
  assert.match(auditionContainer.innerHTML,/Hear example/);
  assert.match(auditionContainer.innerHTML,/value="2.5" selected>2.5×/);
  click(auditionContainer,'preview');
  assert.deepEqual(previews.at(-1),{provider:'system',voice:'',model:'macos-say',segment_id:'segment-1'});
  assert.equal(auditions.api.enabled(book),false);
  assert.equal(auditionStops,0);
  assert.equal(auditions.calls.length,0);
  await auditions.api.render(auditionContainer,book,{...auditionHooks,segmentId:'missing',chapterId:'chapter-2'});
  click(auditionContainer,'preview');
  assert.equal(previews.at(-1).segment_id,'segment-3','Missing current passage falls back only within the selected chapter');
  await auditions.api.render(auditionContainer,book,{...auditionHooks,segmentId:'missing',chapterId:'missing'});
  click(auditionContainer,'preview');
  assert.equal(previews.at(-1).segment_id,null,'A context without source requests generic demo text');
  change(auditionContainer,'provider','gemini');
  change(auditionContainer,'voice','Leda');
  change(auditionContainer,'model','gemini-3.8-flash-lite-tts');
  await auditions.api.render(auditionContainer,book,auditionHooks);
  click(auditionContainer,'preview');
  assert.deepEqual(previews.at(-1),{provider:'gemini',voice:'Leda',model:'gemini-3.8-flash-lite-tts',segment_id:'segment-1'});
  assert.deepEqual(JSON.parse(JSON.stringify(auditions.api.getSelection(book))),{provider:'gemini',voice:'Leda',model:'gemini-3.8-flash-lite-tts',mode:'enhanced'});
  const selection=auditions.api.getSelection(book);selection.voice='Puck';
  assert.equal(auditions.api.getSelection(book).voice,'Leda','Selection snapshots cannot mutate narrator state');
  assert.equal(auditions.api.getSelection({id:'absent'}),null);

  // Both speed selectors and transport surfaces delegate to the single player.
  const stopsBeforeSpeed=auditionStops;
  speed(auditionContainer,'2.25');speed(auditionContainer,'100');speed(auditionContainer,'invalid');
  assert.deepEqual(rates,[2.25]);
  assert.equal(auditionStops,stopsBeforeSpeed,'A speed change cannot cancel listening or an audition');
  assert.equal(auditions.calls.length,0);
  assert.equal(auditions.api.enabled(book),false);
  click(auditionContainer,'start');
  assert.equal(auditionPlay,'segment-1','Starting from enhanced mode activates simple playback');
  assert.equal(toggles,0);
  await auditions.api.render(auditionContainer,book,{...auditionHooks,playing:true,playbackRate:2.25});
  assert.match(auditionContainer.innerHTML,/aria-label="Pause simple listening">Pause/);
  assert.match(auditionContainer.innerHTML,/value="2.25" selected>2.25×/);
  click(auditionContainer,'start');
  assert.equal(toggles,1);
  await auditions.api.render(auditionContainer,book,{...auditionHooks,playing:false,preparing:true});
  assert.match(auditionContainer.innerHTML,/aria-label="Stop preparing narration">Preparing…/);
  click(auditionContainer,'start');
  assert.equal(toggles,2,'The same control can stop a warmup through the shared transport');
  await auditions.api.render(auditionContainer,book,{...auditionHooks,playing:false,previewing:true});
  assert.match(auditionContainer.innerHTML,/data-listen-action="preview"[^>]*disabled/);
  assert.match(auditionContainer.innerHTML,/aria-label="Play simple listening">Play/);
  assert.equal(previews.length,4,'Passive renders never restart an audition');
  // Gemini simple mode may read job status and a local chapter preview, but
  // rendering never submits narration or a chapter job.
  assert.deepEqual(auditions.calls.filter(call=>call.method==='POST'&&!call.url.endsWith('/listen/chapter/preview')),[]);
  assert.ok(auditions.calls.every(call=>call.method==='GET'||call.url.endsWith('/listen/chapter/preview')));

  // A persisted session only reads its local take index, once per revision.
  const savedConfig={mode:'simple',provider:'system',voices:{system:'',gemini:'Kore'},model:'gemini-3.8-flash-tts',sessionId:'session-1',sessionKey:JSON.stringify(['system','','macos-say'])};
  const restore=environment(ordinary,{'spintails:listen:book-9':JSON.stringify(savedConfig)});
  const restoredContainer=new Container();
  await restore.api.render(restoredContainer,book,options());
  assert.equal(restore.calls.length,1);
  assert.equal(restore.calls[0].method,'GET');
  assert.ok(restore.calls[0].url.endsWith('/takes?session_id=session-1'));
  await restore.api.render(restoredContainer,book,options());
  assert.equal(restore.calls.length,1);
  assert.equal(restore.api.take(book,book.segments[0]).asset_id,'simple');

  // A legacy narrator/session selection survives the rename; new choices take precedence.
  assert.equal(restore.storage.get('spintails:listen:book-9'),JSON.stringify(savedConfig));
  const changedLegacy=environment(ordinary,{'spintails:listen:book-9':JSON.stringify(savedConfig)});
  const changedLegacyContainer=new Container();
  await changedLegacy.api.render(changedLegacyContainer,book,options());
  change(changedLegacyContainer,'voice','Samantha');
  assert.equal(JSON.parse(changedLegacy.storage.get('bardic:listen:book-9')).voices.system,'Samantha');
  assert.equal(changedLegacy.storage.get('spintails:listen:book-9'),JSON.stringify(savedConfig),'Legacy preferences remain untouched');
  const newerConfig={...savedConfig,mode:'enhanced',voices:{system:'Samantha',gemini:'Leda'},sessionId:null,sessionKey:null};
  const preferred=environment(ordinary,{
    'spintails:listen:book-9':JSON.stringify(savedConfig),
    'bardic:listen:book-9':JSON.stringify(newerConfig),
  });
  const preferredContainer=new Container();
  await preferred.api.render(preferredContainer,book,options());
  assert.equal(preferred.api.enabled(book),false);
  assert.ok(preferredContainer.innerHTML.includes('value="Samantha" selected'));
  assert.equal(preferred.calls.length,0,'Restoring preferences must not start narration');

  // Stop while POST is pending cancels its late job and cannot return playable audio.
  let pendingPost;
  const stopped=environment(call=>call.url.endsWith('/cancel') ? ordinary(call) : new Promise(resolve=>{pendingPost=resolve;}));
  const stoppedContainer=new Container();
  await stopped.api.render(stoppedContainer,book,options());
  change(stoppedContainer,'mode','simple');
  const pending=stopped.api.ensure(book,book.segments[0]);
  await tick();
  stopped.api.stop(book);
  pendingPost({data:{session,job:{id:'late-job',status:'queued'}}});
  assert.equal(await pending,null);
  await settle();
  assert.ok(stopped.calls.some(call=>call.url==='/api/jobs/late-job/cancel'));
  assert.equal(stopped.api.resolve(book,book.segments[0]),null);

  // Stop or book selection while a job response is in flight cannot resume audio.
  for(const action of ['stop','book']){
    let pendingJob;
    const racing=environment(call=>call.url.startsWith('/api/jobs?') ? new Promise(resolve=>{pendingJob=resolve;}) : ordinary(call));
    const racingContainer=new Container();
    await racing.api.render(racingContainer,book,options());
    change(racingContainer,'mode','simple');
    const waiting=racing.api.ensure(book,book.segments[0]);
    while(!pendingJob) await tick();
    if(action==='stop') racing.api.stop(book);
    else await racing.api.render(racingContainer,{...book,id:'other-book'},options());
    pendingJob({data:[{id:'job-1',status:'completed',audio:simpleAudio}]});
    assert.equal(await waiting,null);
    assert.equal(racing.api.resolve(book,book.segments[0]),null);
  }

  // Provider failures are surfaced once and do not queue following passages.
  const failed=environment(call=>call.url.startsWith('/api/jobs?') ? {data:[{id:'job-1',status:'failed',error:'Quota exhausted.'}]} : ordinary(call));
  const failedContainer=new Container();
  await failed.api.render(failedContainer,book,options());
  change(failedContainer,'mode','simple');
  await assert.rejects(()=>failed.api.ensure(book,book.segments[0]),/Quota exhausted/);
  assert.ok(failedContainer.innerHTML.includes('Quota exhausted.'));
  assert.equal(failed.calls.filter(call=>call.method==='POST').length,1);
  assert.equal(failed.api.resolve(book,book.segments[1]),null);
  assert.equal(failedContainer.drawer.open,true,'New failures reveal their error and explicit retry');
  assert.match(failedContainer.drawer.summary.textContent,/Preparation paused.*One narrator/);
  failedContainer.drawer.open=false;
  await failed.api.render(failedContainer,book,options());
  assert.equal(failedContainer.drawer.open,false,'Repeated renders respect closing the same error');
  change(failedContainer,'voice','Samantha');
  await assert.rejects(()=>failed.api.ensure(book,book.segments[0]),/Quota exhausted/);
  assert.equal(failedContainer.drawer.open,true,'A new explicit attempt reveals a repeated failure again');

  // Lookahead has no parent toast or job callback when its POST fails. The
  // outer disclosure must still expose its error without requesting a retry.
  let backgroundJobs=0;
  const background=environment(call=>{
    if(call.body?.segment_id === 'segment-2') throw new Error('Synthetic connection failure.');
    return {data:{session,audio:{...simpleAudio,duration:20}}};
  });
  const backgroundContainer=new Container();
  await background.api.render(backgroundContainer,book,options({onJob:()=>backgroundJobs++}));
  change(backgroundContainer,'mode','simple');
  await background.api.prepare(book,book.segments[0]);
  assert.equal(backgroundContainer.drawer.open,false,'Ordinary warmup does not open setup');
  background.api.updatePlayback(book,book.segments[0]);
  await settle();
  assert.equal(backgroundJobs,0);
  assert.equal(backgroundContainer.drawer.open,true);
  assert.match(backgroundContainer.drawer.summary.textContent,/Preparation paused/);
  assert.match(backgroundContainer.innerHTML,/Synthetic connection failure/);
  assert.equal(background.calls.filter(call=>call.method==='POST').length,2,'Revealing the error never retries generation');

  // A closed drawer still tells the user which explicit chapter job is running.
  let releaseChapter,chapterPlays=0;
  const preparingChapter=environment(call=>{
    if(call.method==='POST' && call.body?.segment_id==='segment-1') return new Promise(resolve=>{releaseChapter=()=>resolve(ordinary(call));});
    return ordinary(call);
  });
  const chapterContainer=new Container();
  await preparingChapter.api.render(chapterContainer,book,options({onPlay:()=>chapterPlays++}));
  click(chapterContainer,'prepare-chapter');
  while(!releaseChapter) await tick();
  assert.match(chapterContainer.drawer.summary.textContent,/Preparing chapter: 0 of 2 passages.*One narrator/);
  assert.equal(chapterContainer.drawer.open,false,'Chapter status is exposed in the summary without forcing the drawer open');
  releaseChapter();
  await settle();
  assert.equal(chapterPlays,0,'Preparing a chapter does not autoplay');

  // New source text invalidates a cached take, while an enhanced profile change does not.
  const refreshed={...book,revision:2,characters:[{id:'mara',voice:'Orus'}]};
  await restore.api.render(restoredContainer,refreshed,options());
  assert.equal(restore.api.resolve(refreshed,refreshed.segments[0]).asset_id,'simple');
  const edited={...refreshed,segments:refreshed.segments.map((segment,index)=>index===0 ? {...segment,text:'Changed source'} : segment)};
  await restore.api.render(restoredContainer,edited,options());
  assert.equal(restore.api.resolve(edited,edited.segments[0]),null);

  // Device-unavailable defaults to a visible cloud choice but never submits automatically.
  const offline=environment(ordinary),offlineContainer=new Container();
  await offline.api.render(offlineContainer,book,options({status:{has_api_key:false,providers:[{id:'system',available:false},{id:'gemini',available:false}]}}));
  assert.equal(offline.calls.length,0);
  assert.ok(offlineContainer.innerHTML.includes('Add a Gemini API key'));
  assert.ok(offlineContainer.innerHTML.includes('data-listen-action="start" disabled'));
  assert.equal(offline.api.enabled(book),false);
  console.log('Simple listening UI checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
