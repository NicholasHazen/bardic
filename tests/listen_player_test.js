/* Execute the production player's real functions with fake media and UI surfaces. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../spintails/static/app.js'),'utf8');
function between(start,end){ const a=source.indexOf(start),b=source.indexOf(end,a+start.length); assert.ok(a>=0&&b>a,`${start} boundaries`); return source.slice(a,b); }
const functions=[
  between('function stopAudio(', 'function renderLibrary('),
  between('function renderReader(', 'function systemVoiceOptions('),
  between('async function startSegment(', 'function updateProviderHint('),
  between("audio.addEventListener('ended'", "audio.addEventListener('error'")
].join('\n');
const helpers=source.split('\n').filter(line=>/^const (currentChapter|chapterSegments|segmentById|characterById|playable|simpleActive|listeningAudio|listeningReady|busyJob) =/.test(line)).join('\n');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const initialBook=()=>({id:'book',title:'The lamp',chapters:[{id:'c1',title:'First',kind:'chapter',narrative_order:1,text:'One. Two.'},{id:'c2',title:'Second',kind:'chapter',narrative_order:2,text:'Three.'}],characters:[{id:'narrator',name:'Narrator'}],segments:[
  {id:'s1',chapter_id:'c1',speaker_id:'narrator',text:'One.',start:0,end:4,audio:{url:'/enhanced-one.wav',duration:1,provider:'gemini'}},
  {id:'s2',chapter_id:'c1',speaker_id:'narrator',text:'Two.',start:5,end:9,audio:{url:'/enhanced-two.wav',duration:1,provider:'gemini'}},
  {id:'s3',chapter_id:'c2',speaker_id:'narrator',text:'Three.',start:0,end:6,audio:{url:'/enhanced-three.wav',duration:1,provider:'gemini'}}]});
function environment(ensure){
  const book=initialBook(),nodes=new Map(),calls={plays:0,stops:0,ensures:[],toasts:[],saves:0},events={};
  let simple=true, readerHooks;
  const takes=new Map();
  const state={book,chapterId:'c1',segmentId:'s1',audioSegmentId:null,pendingOffset:0,selectionVersion:1,jobs:[],tab:'read',status:{}};
  const audio={src:'',paused:true,currentTime:0,duration:2,
    pause(){this.paused=true;},play(){calls.plays++;this.paused=false;return Promise.resolve();},load(){this.currentTime=0;},
    removeAttribute(name){if(name==='src')this.src='';},getAttribute(name){return name==='src'?this.src:null;},addEventListener(name,fn){events[name]=fn;}};
  const listen={isSimple:()=>simple,resolve:(_book,segment)=>simple?takes.get(segment?.id):segment?.audio,
    ensure:async(book,segment)=>{calls.ensures.push(segment.id);const value=await ensure(book,segment);if(value)takes.set(segment.id,value);return value;},
    stop:()=>{calls.stops++;},render:(_node,_book,hooks)=>{readerHooks=hooks;},allowsAdvance:(_book,a,b)=>a.chapter_id===b.chapter_id};
  const $=selector=>{if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',hidden:false,disabled:false,classList:{toggle(){}},setAttribute(){}});return nodes.get(selector);};
  const context={state,audio,window:{SpinTailsListen:listen},$, $$:()=>[],icon:name=>name,formatTime:value=>String(value),
    escapeHTML:value=>String(value??''),toast:message=>calls.toasts.push(message),saveProgress:()=>calls.saves++,
    updateHighlight:()=>{},renderStudio:()=>{},renderJob:()=>{},pollJobs:()=>{}};
  vm.runInNewContext(`let playGeneration=0,preparingListen=false,previewEnhanced=false;\n${helpers}\n${functions}\n`+
    'globalThis.player={startSegment,togglePlayback,stopAudio,renderReader,updatePlayer,moveSegment,simpleActive,get preparing(){return preparingListen;},get preview(){return previewEnhanced;}};',context);
  return {state,audio,calls,nodes,events,player:context.player,takes,setSimple:value=>{simple=value;},get hooks(){return readerHooks;}};
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
  await flowing.events.ended();
  assert.equal(flowing.state.segmentId,'s2');
  assert.deepEqual(flowing.calls.ensures,['s1','s2']);
  await flowing.events.ended();
  assert.equal(flowing.state.segmentId,'s2');
  assert.deepEqual(flowing.calls.ensures,['s1','s2']);
  assert.ok(flowing.calls.toasts.at(-1).startsWith('Chapter complete'));

  // Selecting an unprepared next passage while paused neither generates nor tells the reader to use Studio.
  const selected=environment(async()=>{throw new Error('Must not render on selection');});
  await selected.player.startSegment('s2',{autoplay:false});
  assert.equal(selected.state.segmentId,'s2');
  assert.equal(selected.calls.ensures.length,0);
  assert.equal(selected.calls.toasts.length,0);

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
  console.log('Main player simple-listen integration checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
