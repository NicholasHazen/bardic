/* Execute the production player's real functions with fake media and UI surfaces. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../bardic/static/app.js'),'utf8');
function between(start,end){ const a=source.indexOf(start),b=source.indexOf(end,a+start.length); assert.ok(a>=0&&b>a,`${start} boundaries`); return source.slice(a,b); }
const functions=[
  between('function saveProgress(', 'function stopAudio('),
  between('function stopAudio(', 'function renderLibrary('),
  between('function renderReader(', 'function castVoiceBlock('),
  between('function setTab(', 'function updateHighlight('),
  between('async function startSegment(', 'function updateProviderHint('),
  between("$('#cast-grid').addEventListener('click'", "$('#analysis-progress').addEventListener"),
  between("audio.addEventListener('loadedmetadata'", "window.addEventListener('pagehide'"),
  between("\n$('#playback-speed').value = String(audio.playbackRate);", "audio.addEventListener('loadedmetadata'")
].join('\n');
const helpers=source.split('\n').filter(line=>/^const (currentChapter|chapterSegments|segmentById|characterById|playable|simpleActive|listeningAudio|listeningReady|busyJob|progressKey) =/.test(line)).join('\n');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function until(predicate){for(let i=0;i<50;i++){if(predicate())return;await tick();}assert.fail('Expected async player transition did not occur');}
const initialBook=()=>({id:'book',title:'The lamp',chapters:[{id:'c1',title:'First',kind:'chapter',narrative_order:1,text:'One. Two.'},{id:'c2',title:'Second',kind:'chapter',narrative_order:2,text:'Three.'}],characters:[{id:'narrator',name:'Narrator'}],segments:[
  {id:'s1',chapter_id:'c1',speaker_id:'narrator',text:'One.',start:0,end:4,audio:{url:'/enhanced-one.wav',duration:1,provider:'gemini'}},
  {id:'s2',chapter_id:'c1',speaker_id:'narrator',text:'Two.',start:5,end:9,audio:{url:'/enhanced-two.wav',duration:1,provider:'gemini'}},
  {id:'s3',chapter_id:'c2',speaker_id:'narrator',text:'Three.',start:0,end:6,audio:{url:'/enhanced-three.wav',duration:1,provider:'gemini'}}]});
function environment(ensure,previewRequest){
  const book=initialBook(),nodes=new Map(),calls={plays:0,stops:0,ensures:[],prepares:[],updates:[],toasts:[],saves:0,preloads:[],diagnostics:[],forgotten:[],positions:[],toastErrors:[],previews:[]},events={},storage=new Map();
  let simple=true, readerHooks;
  const takes=new Map();
  const state={book,chapterId:'c1',segmentId:'s1',audioSegmentId:null,pendingOffset:0,selectionVersion:1,jobs:[],tab:'read',status:{tts_model:'tts-test'},lastSave:0};
  const audio={src:'',paused:true,currentTime:0,duration:2,playbackRate:1,defaultPlaybackRate:1,
    pause(){this.paused=true;},play(){calls.plays++;this.paused=false;return Promise.resolve();},load(){this.currentTime=0;this.error=null;this.playbackRate=this.defaultPlaybackRate;},
    removeAttribute(name){if(name==='src')this.src='';},getAttribute(name){return name==='src'?this.src:null;},addEventListener(name,fn){events[name]=fn;}};
  const listen={isSimple:()=>simple,resolve:(_book,segment)=>simple?takes.get(segment?.id):segment?.audio,
    ensure:async(book,segment)=>{calls.ensures.push(segment.id);const value=await ensure(book,segment);if(value)takes.set(segment.id,value);return value;},
    prepare:async(book,segment,options)=>{calls.prepares.push({id:segment.id,...options});return listen.ensure(book,segment);},
    updatePlayback:(_book,segment,options)=>calls.updates.push({id:segment.id,...options}),
    forgetAudio:(_book,segment)=>{calls.forgotten.push(segment.id);takes.delete(segment.id);},
    stop:()=>{calls.stops++;},render:(_node,_book,hooks)=>{readerHooks=hooks;},allowsAdvance:(_book,a,b)=>a.chapter_id===b.chapter_id};
  const $=selector=>{if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',hidden:false,disabled:false,listeners:{},addEventListener(name,handler){this.listeners[name]=handler;},classList:{toggle(){}},setAttribute(){}});return nodes.get(selector);};
  const context={state,audio,window:{BardicListen:listen,BardicDiagnostics:{record:(event,details)=>calls.diagnostics.push({event,...details})}},$, $$:()=>[],icon:name=>name,formatTime:value=>String(value),
    setTimeout:fn=>setImmediate(fn),fetch:async(url,options={})=>{
      const call={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null};calls.previews.push(call);
      if(!previewRequest)throw new Error('Unexpected voice preview request');
      const response=await previewRequest(call);
      return {ok:response.ok!==false,status:response.status||200,json:async()=>response.data};
    },
    Audio:class {constructor(){this.src='';}load(){calls.preloads.push(this.src);}removeAttribute(){this.src='';}},
    safeRead:(key,fallback)=>key==='bardic:speed'?2.5:fallback,safeWrite:(key,value)=>{storage.set(key,value);if(key.startsWith('bardic:progress:')){calls.saves++;calls.positions.push({segmentId:value.segmentId,currentTime:value.currentTime});}},
    escapeHTML:value=>String(value??''),toast:(message,error)=>{calls.toasts.push(message);calls.toastErrors.push(Boolean(error));},
    updateHighlight:()=>{},renderStudio:()=>{},renderJob:()=>{},pollJobs:()=>{}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/voice-preview.js'),'utf8'),context);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/voices.js'),'utf8'),context);
  vm.runInNewContext(`let playGeneration=0,preparingListen=false,previewEnhanced=false,mediaBuffering=false; const listeningPreloads=new Map();\n${between('audio.preload =', 'let toastTimer;')}\n${helpers}\n${functions}\n`+
    'setupVoicePreviews(); globalThis.player={startSegment,togglePlayback,stopAudio,renderReader,updatePlayer,moveSegment,simpleActive,setPlaybackRate,beginVoicePreview,playVoicePreview,finishVoicePreview,auditionCharacter,auditionPassage,startVoicePreview,get preparing(){return preparingListen;},get preview(){return previewEnhanced;},get buffering(){return mediaBuffering;}};',context);
  return {state,audio,calls,nodes,events,player:context.player,takes,storage,listen,voicePreview:context.window.BardicVoicePreview,setSimple:value=>{simple=value;},get hooks(){return readerHooks;}};
}
(async()=>{
  // Pause while awaiting simple audio must never resume when that request finishes.
  let finish;
  const paused=environment(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=paused.player.startSegment('s1');
  await tick();
  assert.equal(paused.player.preparing,true);
  assert.equal(paused.nodes.get('#play-button').innerHTML,'pause');
  await paused.player.togglePlayback();
  assert.equal(paused.player.preparing,false);
  finish({url:'/simple-one.wav',duration:1});await pending;
  assert.equal(paused.calls.plays,0);
  assert.equal(paused.audio.src,'');

  // A changed book/selection cannot inherit an earlier request's playback.
  let oldBook;
  const switched=environment(()=>new Promise(resolve=>{oldBook=resolve;}));
  const old=switched.player.startSegment('s1');await tick();
  switched.player.stopAudio({clear:true});
  switched.state.book={...initialBook(),id:'different'};switched.state.selectionVersion++;
  oldBook({url:'/wrong-book.wav'});await old;
  assert.equal(switched.calls.plays,0);

  // Repeated clicks don't cancel and pay for the same in-flight passage twice.
  let ready;
  const duplicate=environment(()=>new Promise(resolve=>{ready=resolve;}));
  const original=duplicate.player.startSegment('s1');await tick();
  await duplicate.player.startSegment('s1');
  assert.equal(duplicate.calls.ensures.length,1);
  ready({url:'/simple-one.wav',duration:1});await original;
  assert.equal(duplicate.calls.plays,1);

  // A studio preview interrupts pending simple playback and ends without generating the next line.
  let simpleReady;
  const preview=environment(()=>new Promise(resolve=>{simpleReady=resolve;}));
  const waiting=preview.player.startSegment('s1');await tick();
  await preview.player.startSegment('s1',{enhanced:true});
  assert.equal(preview.audio.src,'/enhanced-one.wav');
  assert.equal(preview.player.preview,true);
  assert.ok(preview.calls.stops>=1);
  preview.player.renderReader();
  preview.hooks.onChange();
  assert.equal(preview.player.preview,true,'A saved-cache refresh must not switch an enhanced preview to simple');
  simpleReady({url:'/late-simple.wav'});await waiting;
  assert.equal(preview.audio.src,'/enhanced-one.wav');
  await preview.events.ended();
  assert.equal(preview.calls.ensures.length,1,'Studio preview must not start paid continuation');
  assert.equal(preview.state.segmentId,'s1');
  assert.equal(preview.player.preview,false);

  // Explicit mode/config changes stop the pending request through the installed reader callback.
  let oldConfig;
  const configured=environment(()=>new Promise(resolve=>{oldConfig=resolve;}));
  configured.player.renderReader();
  const configWaiting=configured.player.startSegment('s1');await tick();
  configured.hooks.onStop();configured.setSimple(false);configured.hooks.onChange();
  oldConfig({url:'/old-voice.wav'});await configWaiting;
  assert.equal(configured.calls.plays,0);

  // Simple playback follows passages in one chapter and never starts the next chapter automatically.
  const flowing=environment(async(_book,segment)=>({url:`/simple-${segment.id}.wav`,duration:1}));
  await flowing.player.startSegment('s1');
  assert.equal(flowing.audio.defaultPlaybackRate,2.5,'Saved speed is restored as the media default');
  assert.equal(flowing.audio.playbackRate,2.5,'Loading a new passage preserves the selected 2.5× speed');
  assert.equal(flowing.calls.prepares[0].playbackRate,2.5,'Warmup uses the actual high playback speed');
  assert.equal(flowing.calls.updates.at(-1).playbackRate,2.5,'Rolling queue receives playback rate');
  const stopsBeforeAdvance=flowing.calls.stops;
  await flowing.events.ended();
  assert.equal(flowing.calls.stops,stopsBeforeAdvance,'Automatic advance must retain lookahead intent');
  assert.equal(flowing.state.segmentId,'s2');
  assert.deepEqual(flowing.calls.ensures,['s1','s2']);
  await flowing.events.ended();
  assert.equal(flowing.state.segmentId,'s2');
  assert.deepEqual(flowing.calls.ensures,['s1','s2']);
  assert.ok(flowing.calls.toasts.at(-1).startsWith('Chapter complete'));
  assert.equal(flowing.calls.stops,stopsBeforeAdvance+1,'Chapter completion cancels further preparation');

  // Warmup applies to an already cached starting passage too, and pause/resume
  // preserves the media offset while restarting a speed-aware buffer.
  const resumed=environment(async(_book,segment)=>({url:`/simple-${segment.id}.wav`,duration:2}));
  resumed.takes.set('s1',{url:'/simple-s1.wav',duration:2});
  await resumed.player.startSegment('s1');
  assert.equal(resumed.calls.prepares.length,1);
  resumed.audio.currentTime=1.25;
  await resumed.player.togglePlayback();
  assert.equal(resumed.audio.paused,true);
  await resumed.player.togglePlayback();
  assert.equal(resumed.calls.prepares.at(-1).offset,1.25);
  assert.equal(resumed.audio.currentTime,1.25);
  assert.equal(resumed.audio.playbackRate,2.5,'Pause/resume preserves speed');
  resumed.player.stopAudio({clear:true});
  assert.equal(resumed.audio.playbackRate,2.5,'Clearing the source preserves speed despite load() resetting the rate');
  await resumed.player.togglePlayback();
  assert.equal(resumed.audio.playbackRate,2.5,'Starting again after clearing keeps the selected speed');
  assert.equal(resumed.calls.prepares.at(-1).playbackRate,2.5);

  // Exercise the real speed-change handler as well as initialization, so both
  // paths must set the media default that load() restores.
  resumed.nodes.get('#playback-speed').listeners.change({target:{value:'2'}});
  assert.equal(resumed.audio.defaultPlaybackRate,2);
  resumed.player.stopAudio({clear:true});
  await resumed.player.togglePlayback();
  assert.equal(resumed.audio.playbackRate,2,'A newly selected speed survives clearing and loading');
  assert.equal(resumed.calls.prepares.at(-1).playbackRate,2);

  // Media is preloaded from saved next-passage URLs only, without rendering or
  // crossing into the next chapter. Manual passage navigation stops the old queue.
  const preloaded=environment(async(_book,segment)=>({url:`/simple-${segment.id}.wav`,duration:2}));
  preloaded.takes.set('s2',{url:'/saved-next.wav',duration:2});
  preloaded.takes.set('s3',{url:'/next-chapter.wav',duration:2});
  await preloaded.player.startSegment('s1');
  assert.ok(preloaded.calls.preloads.includes('/saved-next.wav'));
  assert.ok(!preloaded.calls.preloads.includes('/next-chapter.wav'));
  const stopsBeforeSeek=preloaded.calls.stops;
  await preloaded.player.startSegment('s2',{autoplay:false});
  assert.equal(preloaded.calls.stops,stopsBeforeSeek+1);
  assert.deepEqual(preloaded.calls.ensures,['s1']);

  // Selecting an unprepared next passage while paused neither generates nor tells the reader to use Studio.
  const selected=environment(async()=>{throw new Error('Must not render on selection');});
  selected.player.renderReader();
  assert.equal(selected.hooks.segmentId,'s1');
  await selected.player.startSegment('s2',{autoplay:false});
  assert.equal(selected.state.segmentId,'s2');
  assert.equal(selected.hooks.segmentId,'s2','Selecting a different passage in the same chapter refreshes the simple-listen audition source');
  assert.equal(selected.calls.ensures.length,0);
  assert.equal(selected.calls.toasts.length,0);

  // Following a character's reference changes the selected passage after
  // choosing the chapter; refresh the audition source to that exact passage.
  const referenced=environment(async()=>{throw new Error('Source navigation must not synthesize');});
  referenced.player.renderReader();
  referenced.nodes.get('#cast-grid').listeners.click({target:{closest:selector=>selector==='[data-reference-chapter]'?
    {dataset:{referenceChapter:'c1',referenceSegment:'s2'}}:null}});
  assert.equal(referenced.state.chapterId,'c1');
  assert.equal(referenced.state.segmentId,'s2');
  assert.equal(referenced.hooks.segmentId,'s2','The narrator example follows the referenced passage, not the chapter\'s first passage');
  assert.equal(referenced.calls.ensures.length,0);
  assert.equal(referenced.calls.plays,0);

  // Rejected/stale enhanced takes cannot be played even for a studio preview.
  const stale=environment(async()=>null);
  stale.state.book.segments[0].audio.stale=true;
  await stale.player.startSegment('s1',{enhanced:true});
  assert.equal(stale.calls.plays,0);

  // A delayed media-play rejection after Stop does not raise a stale error in the new player state.
  let rejectPlay;
  const media=environment(async()=>({url:'/simple.wav',duration:1}));
  media.audio.play=()=>new Promise((_resolve,reject)=>{rejectPlay=reject;});
  const starting=media.player.startSegment('s1');
  while(!rejectPlay) await tick();
  media.player.stopAudio({clear:true});
  rejectPlay(new Error('Old media request'));await starting;
  assert.equal(media.calls.toasts.length,0);
  // Execute the production media-error handler. load() deliberately clears the
  // fake MediaError, verifying that diagnostics capture the code beforehand.
  const broken=environment(async(_book,segment)=>({url:`/simple-${segment.id}.wav`,duration:2}));
  await broken.player.startSegment('s1');
  broken.audio.currentTime=1.25;
  broken.audio.error={code:3,message:'Private media details must not be recorded'};
  const stopsBeforeError=broken.calls.stops,requestsBeforeError=broken.calls.ensures.length;
  broken.events.error();
  assert.equal(broken.calls.diagnostics.at(-1).event,'playback_media_error');
  assert.equal(broken.calls.diagnostics.at(-1).media_error_code,3);
  assert.equal(broken.calls.diagnostics.at(-1).book_id,'book');
  assert.equal(broken.calls.diagnostics.at(-1).segment_id,'s1');
  assert.equal(broken.calls.diagnostics.at(-1).playback_rate,2.5);
  assert.equal(broken.calls.diagnostics.at(-1).operation,'media');
  assert.ok(!JSON.stringify(broken.calls.diagnostics).includes('Private'));
  assert.equal(broken.audio.error,null,'Clear resets MediaError after its code was captured');
  assert.equal(broken.audio.src,'');
  assert.equal(broken.audio.paused,true);
  assert.equal(broken.calls.stops,stopsBeforeError+1,'Media failure cancels the preparation queue');
  assert.deepEqual(broken.calls.forgotten,['s1']);
  assert.equal(broken.takes.has('s1'),false,'Only the browser-selected take is forgotten');
  assert.equal(broken.state.pendingOffset,1.25);
  assert.deepEqual(broken.calls.positions.at(-1),{segmentId:'s1',currentTime:1.25});
  assert.match(broken.calls.toasts.at(-1),/Press Play to check the local cache/);
  assert.equal(broken.calls.toastErrors.at(-1),true);
  await tick();
  assert.equal(broken.calls.ensures.length,requestsBeforeError,'A media error never starts a paid retry');
  const diagnosticsAfterError=broken.calls.diagnostics.length;
  broken.events.error();
  assert.equal(broken.calls.diagnostics.length,diagnosticsAfterError,'Ignore error events after src is cleared');
  assert.equal(broken.calls.stops,stopsBeforeError+1);

  // An error in enhanced playback keeps simple cache entries intact.
  const enhancedError=environment(async()=>null);
  enhancedError.setSimple(false);
  await enhancedError.player.startSegment('s1');
  enhancedError.audio.error={code:4};
  enhancedError.events.error();
  assert.deepEqual(enhancedError.calls.forgotten,[]);
  assert.match(enhancedError.calls.toasts.at(-1),/regenerating it in the studio/);
  assert.equal(enhancedError.calls.diagnostics.at(-1).media_error_code,4);

  // Real waiting/stalled/playing event handlers describe a resumable stall.
  // Repeated playing events do not falsely report another recovery.
  const stalled=environment(async()=>({url:'/simple.wav',duration:2}));
  await stalled.player.startSegment('s1');
  stalled.events.waiting();
  assert.equal(stalled.player.buffering,true);
  assert.match(stalled.nodes.get('#player-subtitle').textContent,/Buffering/);
  assert.equal(stalled.calls.diagnostics.at(-1).event,'playback_waiting');
  stalled.events.playing();
  assert.equal(stalled.player.buffering,false);
  assert.equal(stalled.calls.diagnostics.at(-1).event,'playback_resumed');
  const resumedEvents=stalled.calls.diagnostics.length;
  stalled.events.playing();
  assert.equal(stalled.calls.diagnostics.length,resumedEvents);
  stalled.events.stalled();
  assert.equal(stalled.player.buffering,true);
  assert.equal(stalled.calls.diagnostics.at(-1).event,'playback_waiting','A stalled event alone is auditable');
  stalled.events.playing();
  assert.equal(stalled.calls.diagnostics.at(-1).event,'playback_resumed');
  stalled.player.stopAudio();
  const afterPause=stalled.calls.diagnostics.length;
  stalled.events.stalled();
  assert.equal(stalled.calls.diagnostics.length,afterPause,'Paused loading does not report a playback stall');
  assert.equal(stalled.player.buffering,false);
  assert.equal(stalled.calls.ensures.length,1,'Waiting/resumed events do not independently request speech');

  // Voice auditions use the production controller and the same media element.
  // Their timestamps and selected passage must never become reading progress.
  const example={id:'preview-1',source:'passage',text:'One.',segment_id:'s1',character_id:'narrator'};
  const exampleAudio={url:'/example.wav',duration:2,available:true};
  const sampleResponse=()=>({data:{preview:example,audio:exampleAudio,cached:true}});
  const audition=environment(async(_book,segment)=>({url:`/simple-${segment.id}.wav`,duration:2}),sampleResponse);
  await audition.player.startSegment('s1');
  audition.audio.currentTime=1.25;
  const narrationRequests=audition.calls.ensures.length;
  await audition.voicePreview.start(audition.state.book,{provider:'gemini',voice:'Leda',segment_id:'s1'},'Leda example');
  assert.equal(audition.audio.src,'/example.wav');
  assert.equal(audition.audio.playbackRate,2.5,'Examples share the bottom player speed');
  assert.equal(audition.state.segmentId,'s1');
  assert.equal(audition.state.audioSegmentId,null,'Example audio never masquerades as a narrated passage');
  assert.equal(audition.state.pendingOffset,1.25);
  assert.equal(audition.nodes.get('#previous-segment').disabled,true);
  assert.equal(audition.nodes.get('#next-segment').disabled,true);
  assert.equal(audition.nodes.get('#voice-preview-panel').hidden,false);
  assert.equal(audition.nodes.get('#voice-preview-text').textContent,'One.');
  audition.events.loadedmetadata();
  assert.equal(audition.audio.currentTime,0,'Loading an example must not seek to the reader offset');
  const updatesBeforeSample=audition.calls.updates.length;
  audition.audio.currentTime=.75;
  audition.events.timeupdate();
  audition.events.playing();
  assert.equal(audition.calls.updates.length,updatesBeforeSample,'Example events cannot drive paid simple-listen lookahead');
  assert.equal(audition.storage.get('bardic:progress:book').currentTime,1.25,'Periodic persistence retains the paused reading position');
  audition.nodes.get('#audio-progress').listeners.input({target:{value:'1.5'}});
  assert.equal(audition.audio.currentTime,1.5,'Scrubbing controls the example itself');
  assert.equal(audition.state.pendingOffset,1.25);
  await audition.player.togglePlayback();
  assert.equal(audition.audio.paused,true);
  await audition.player.togglePlayback();
  assert.equal(audition.audio.paused,false);
  assert.equal(audition.audio.src,'/example.wav');
  assert.equal(audition.calls.ensures.length,narrationRequests,'Play/pause of an example cannot request narration');
  audition.nodes.get('#playback-speed').listeners.change({target:{value:'2'}});
  assert.equal(audition.audio.playbackRate,2);
  assert.equal(audition.hooks.playbackRate,2,'The simple-listen panel receives the same shared speed');
  await audition.events.ended();
  assert.equal(audition.state.voicePreview,null);
  assert.equal(audition.voicePreview.getState().status,'idle');
  assert.equal(audition.audio.src,'');
  assert.equal(audition.audio.paused,true);
  assert.equal(audition.state.segmentId,'s1','Ending an example never advances the book');
  assert.equal(audition.state.pendingOffset,1.25);
  assert.equal(audition.calls.ensures.length,narrationRequests,'Ending an example never requests the next passage');
  assert.equal(audition.nodes.get('#voice-preview-panel').hidden,true);
  await audition.player.togglePlayback();
  audition.events.loadedmetadata();
  assert.equal(audition.audio.src,'/simple-s1.wav');
  assert.equal(audition.audio.currentTime,1.25,'Explicit normal play returns to the saved reading offset');
  assert.equal(audition.audio.playbackRate,2);

  // Cancelling a pending audition from the bottom Play control prevents a late
  // response from starting, and leaves the current reading position intact.
  let sampleReady;
  const cancelledSample=environment(async()=>null,()=>new Promise(resolve=>{sampleReady=resolve;}));
  cancelledSample.state.pendingOffset=.8;
  const preparingSample=cancelledSample.voicePreview.start(cancelledSample.state.book,{provider:'system',voice:'Samantha'});
  await until(()=>sampleReady);
  assert.equal(cancelledSample.nodes.get('#play-button').innerHTML,'pause');
  await cancelledSample.player.togglePlayback();
  sampleReady(sampleResponse());
  await preparingSample;
  assert.equal(cancelledSample.calls.plays,0);
  assert.equal(cancelledSample.state.pendingOffset,.8);
  assert.equal(cancelledSample.state.voicePreview,null);

  // A stopped or deliberately aborted media resume cannot toast over a newer
  // reader state, and must not restart a sample or narration.
  for(const stale of [true,false]){
    const resumedSample=environment(async()=>null,sampleResponse);
    await resumedSample.voicePreview.start(resumedSample.state.book,{provider:'system',voice:'Samantha'});
    await resumedSample.player.togglePlayback();
    let rejectResume;
    resumedSample.audio.play=()=>new Promise((_resolve,reject)=>{rejectResume=reject;});
    const resumingSample=resumedSample.player.togglePlayback();
    await until(()=>rejectResume);
    if(stale)resumedSample.voicePreview.stop();
    const rejection=new Error('Old sample request');
    if(!stale)rejection.name='AbortError';
    rejectResume(rejection);
    await resumingSample;
    assert.equal(resumedSample.calls.toasts.length,0,stale?'Closing an example suppresses a late resume rejection':'An aborted resume does not report a playback error');
    assert.equal(resumedSample.calls.ensures.length,0);
    assert.equal(resumedSample.calls.previews.length,1);
  }

  // Switching modes waits for the old provider job in either direction, so
  // cooperative cancellation does not produce a new busy-book POST race.
  let finishReader, readerWaited=false;
  const readerToSample=environment(async()=>null,sampleResponse);
  readerToSample.listen.waitForStopped=()=>{readerWaited=true;return new Promise(resolve=>{finishReader=resolve;});};
  const afterReader=readerToSample.voicePreview.start(readerToSample.state.book,{provider:'system',voice:'Samantha'});
  await until(()=>readerWaited);
  assert.equal(readerToSample.calls.previews.length,0,'Preview synthesis waits for cancelled reader lookahead');
  finishReader(true);
  await afterReader;
  assert.equal(readerToSample.calls.previews.length,1);

  let acceptedPreview, settledPreview;
  const sampleToReader=environment(async()=>({url:'/reading-again.wav',duration:2}),call=>{
    if(call.url.endsWith('/cancel'))return {data:{status:'running'}};
    if(call.method==='GET')return new Promise(resolve=>{settledPreview=resolve;});
    return new Promise(resolve=>{acceptedPreview=resolve;});
  });
  sampleToReader.state.pendingOffset=.6;
  const oldSample=sampleToReader.voicePreview.start(sampleToReader.state.book,{provider:'system',voice:'Samantha'});
  await until(()=>acceptedPreview);
  const readAgain=sampleToReader.hooks.onToggle();
  acceptedPreview({data:{preview:example,job:{id:'preview-job',status:'queued'}}});
  await until(()=>settledPreview);
  assert.equal(sampleToReader.calls.ensures.length,0,'Reader synthesis waits for cancelled preview generation');
  assert.equal(sampleToReader.calls.plays,0);
  settledPreview({data:[{id:'preview-job',status:'cancelled'}]});
  await oldSample;await readAgain;
  assert.equal(sampleToReader.calls.ensures.length,1);
  assert.equal(sampleToReader.audio.src,'/reading-again.wav');
  assert.equal(sampleToReader.calls.prepares[0].offset,.6);
  assert.equal(sampleToReader.state.voicePreview,null);

  // Device/media failure returns to the book without evicting its simple take
  // or silently generating a replacement preview.
  const failedSample=environment(async()=>null,()=>({data:{preview:{...example,segment_id:'s2'},audio:exampleAudio,cached:true}}));
  failedSample.takes.set('s1',{url:'/saved-reading.wav',duration:2});
  failedSample.state.pendingOffset=.9;
  await failedSample.voicePreview.start(failedSample.state.book,{provider:'system',voice:'Samantha'});
  failedSample.audio.error={code:3};
  failedSample.events.error();
  assert.equal(failedSample.state.voicePreview,null);
  assert.equal(failedSample.state.pendingOffset,.9);
  assert.equal(failedSample.takes.get('s1').url,'/saved-reading.wav');
  assert.deepEqual(failedSample.calls.forgotten,[]);
  assert.equal(failedSample.calls.previews.length,1);
  assert.equal(failedSample.calls.diagnostics.at(-1).segment_id,'s2','Media diagnostics identify the audition passage rather than the paused reader passage');
  assert.match(failedSample.calls.toasts.at(-1),/Your reading position is saved/);

  // Audition helpers read unsaved form choices and use source scope without
  // mutating the saved character or passage assignments.
  const casting=environment(async()=>null,sampleResponse);
  const character={id:'mara',name:'Mara',voice:'Kore',system_voice:'Samantha',direction:'Saved delivery.'};
  casting.state.book.characters.push(character);
  casting.state.book.segments[1].speaker_id='mara';
  casting.state.segmentId='s2';
  const castBefore=JSON.stringify(casting.state.book);
  // A cast card shows one provider's voice select ("voice_choice"): "" is Default,
  // "id:<voice>" a direct voice and "library:<id>" a library voice.
  const characterForm={dataset:{characterForm:'mara',castProvider:'gemini'},elements:{voice_choice:{value:'id:Puck'},direction:{value:'An unsaved gentle delivery.'}}};
  casting.player.auditionCharacter(characterForm,'gemini');
  await until(()=>casting.voicePreview.getState().status==='ready');
  assert.deepEqual(casting.calls.previews[0].body,{provider:'gemini',character_id:'mara',segment_id:'s2',voice:'Puck',model:'tts-test',direction:'An unsaved gentle delivery.'});
  casting.state.segmentId='s1';
  casting.player.auditionCharacter({...characterForm,elements:{...characterForm.elements,voice_choice:{value:'id:Alex'}}},'system');
  await until(()=>casting.voicePreview.getState().status==='ready');
  assert.equal(casting.calls.previews[1].body.segment_id,'s2','Use this chapter\'s character passage when the selected passage has a different speaker');
  assert.equal(casting.calls.previews[1].body.voice,'Alex');
  casting.state.chapterId='c2';
  casting.player.auditionCharacter({...characterForm,elements:{...characterForm.elements,voice_choice:{value:''}}},'gemini');
  await until(()=>casting.voicePreview.getState().status==='ready');
  assert.equal(casting.calls.previews[2].body.segment_id,undefined,'Without a local passage, the server chooses character text or its demo fallback');
  assert.equal(casting.calls.previews[2].body.voice,'Kore','Gemini Default is Kore');
  // The passage helper selects its provider through the existing studio field.
  if(!casting.nodes.has('#render-provider')) casting.nodes.set('#render-provider',{value:'gemini'});
  else casting.nodes.get('#render-provider').value='gemini';
  casting.player.auditionPassage({dataset:{segmentForm:'s1'},elements:{speaker_id:{value:'mara'},direction:{value:'Unsaved passage cue.'}}});
  await until(()=>casting.voicePreview.getState().status==='ready');
  assert.deepEqual(casting.calls.previews[3].body,{provider:'gemini',character_id:'mara',segment_id:'s1',voice:'Kore',model:'tts-test',segment_direction:'Unsaved passage cue.'},
    'Legacy Gemini voice fields are still read');
  // Breeze Default needs a default voice; without one nothing is requested.
  casting.player.auditionCharacter({...characterForm,elements:{...characterForm.elements,voice_choice:{value:''}}},'breeze');
  assert.equal(casting.calls.previews.length,4,'Breeze Default without a default voice never requests a sample');
  assert.match(casting.calls.toasts.at(-1),/no default Breeze voice yet/);
  casting.player.auditionCharacter({...characterForm,elements:{...characterForm.elements,voice_choice:{value:'library:vl_story'}}},'breeze');
  await until(()=>casting.voicePreview.getState().status==='ready');
  assert.deepEqual(casting.calls.previews[4].body,{provider:'breeze',character_id:'mara',voice:'library:vl_story',direction:'An unsaved gentle delivery.'},
    'A library voice is sent by reference and the server resolves its current version; Breeze sends no model');
  casting.nodes.get('#render-provider').value='breeze';
  casting.player.auditionPassage({dataset:{segmentForm:'s1'},elements:{speaker_id:{value:'mara'},direction:{value:'Unsaved passage cue.'}}});
  assert.equal(casting.calls.previews.length,5,'A character on Breeze Default cannot audition without a default voice');
  assert.equal(JSON.stringify(casting.state.book),castBefore,'No audition saves voice, direction or speaker edits');
  casting.state.voiceLibrary={voices:[],defaults:{breeze:'vl_narr'}};
  casting.player.auditionPassage({dataset:{segmentForm:'s1'},elements:{speaker_id:{value:'mara'},direction:{value:'Unsaved passage cue.'}}});
  await until(()=>casting.voicePreview.getState().status==='ready');
  assert.equal(casting.calls.previews[5].body.voice,'','With a default voice, Default is sent as an empty voice for the server to resolve');
  character.voices={breeze:{library:'vl_narr'}};
  const pinnedBefore=JSON.stringify(casting.state.book);
  casting.player.auditionPassage({dataset:{segmentForm:'s1'},elements:{speaker_id:{value:'mara'},direction:{value:'Unsaved passage cue.'}}});
  await until(()=>casting.voicePreview.getState().status==='ready');
  assert.deepEqual(casting.calls.previews[6].body,{provider:'breeze',character_id:'mara',segment_id:'s1',voice:'library:vl_narr',segment_direction:'Unsaved passage cue.'});
  assert.equal(JSON.stringify(casting.state.book),pinnedBefore,'No audition saves voice, direction or speaker edits');
  delete character.voices;
  console.log('Main player simple-listen integration checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
