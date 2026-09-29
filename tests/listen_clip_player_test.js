/* The production player follows clips inside one chunk WAV without reloading. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../bardic/static/app.js'),'utf8');
function between(start,end){ const a=source.indexOf(start),b=source.indexOf(end,a+start.length); assert.ok(a>=0&&b>a,`${start} boundaries`); return source.slice(a,b); }
const functions=[
  between('function saveProgress(', 'function stopAudio('),
  between('function stopAudio(', 'function renderLibrary('),
  between('async function startSegment(', 'function updateProviderHint('),
  between("audio.addEventListener('loadedmetadata'", "window.addEventListener('pagehide'"),
  between("$('#audio-progress').addEventListener('input'", "audio.addEventListener('loadedmetadata'"),
].join('\n');
const helpers=source.split('\n').filter(line=>/^const (currentChapter|chapterSegments|segmentById|characterById|playable|simpleActive|listeningAudio|listeningReady|busyJob|progressKey) =/.test(line)).join('\n');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const chunk=(url,start,end)=>({url,clip_start:start,clip_end:end,duration:end-start,chunk_id:url,timing:'estimated'});

function environment(){
  const book={id:'book',title:'Harbor',chapters:[{id:'c1',title:'One',kind:'chapter',text:'A. B. C. D.'}],characters:[{id:'narrator',name:'Narrator'}],passages:[
    {id:'s1',chapter_id:'c1',speaker_id:'narrator',text:'A.',start:0,end:2},{id:'s2',chapter_id:'c1',speaker_id:'narrator',text:'B.',start:3,end:5},
    {id:'s3',chapter_id:'c1',speaker_id:'narrator',text:'C.',start:6,end:8},{id:'s4',chapter_id:'c1',speaker_id:'narrator',text:'D.',start:9,end:11}]};
  const takes=new Map([['s1',chunk('/a.wav',0,2)],['s2',chunk('/a.wav',2,5)],['s3',chunk('/b.wav',0,3)],['s4',chunk('/b.wav',3,4)]]);
  const calls={loads:0,plays:0,highlights:0,positions:[],updates:[],prepares:[]},events={},nodes=new Map();
  const state={book,chapterId:'c1',segmentId:'s1',audioSegmentId:null,pendingOffset:0,selectionVersion:1,jobs:[],tab:'read',status:{},lastSave:0};
  const audio={src:'',paused:true,currentTime:0,duration:5,playbackRate:1,defaultPlaybackRate:1,
    pause(){this.paused=true;},play(){calls.plays++;this.paused=false;return Promise.resolve();},
    load(){calls.loads++;this.currentTime=0;this.duration=this.src==='/b.wav'?4:5;},
    removeAttribute(name){if(name==='src')this.src='';},getAttribute(name){return name==='src'?this.src:null;},addEventListener(name,fn){events[name]=fn;}};
  const listen={isSimple:()=>true,resolve:(_book,segment)=>takes.get(segment?.id),prepare:async(_book,segment)=>{calls.prepares.push(segment.id);return takes.get(segment.id);},
    updatePlayback:(_book,segment,options)=>calls.updates.push({id:segment.id,...options}),stop(){},allowsAdvance:(_book,a,b)=>a.chapter_id===b.chapter_id};
  const $=selector=>{if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',hidden:false,disabled:false,value:0,max:0,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},classList:{toggle(){}},setAttribute(){}});return nodes.get(selector);};
  const context={state,audio,window:{BardicListen:listen},$, $$:()=>[],icon:name=>name,formatTime:value=>String(value),setTimeout:fn=>setImmediate(fn),
    Audio:class {constructor(){this.src='';}load(){}removeAttribute(){this.src='';}},
    safeRead:(_key,fallback)=>fallback,safeWrite:(key,value)=>{if(key.startsWith('bardic:progress:'))calls.positions.push(value);},
    escapeHTML:value=>String(value??''),toast:()=>{},updateHighlight:()=>{calls.highlights++;},renderReader:()=>{},renderStudio:()=>{},renderJob:()=>{},renderPassageDetail:()=>{}};
  vm.runInNewContext(`let playGeneration=0,preparingListen=false,previewEnhanced=false,mediaBuffering=false; const listeningPreloads=new Map();\n${helpers}\n${functions}\nglobalThis.player={startSegment,updatePlayer,saveProgress,passageTime,beginVoicePreview};`,context);
  return {state,audio,calls,events,nodes,takes,player:context.player};
}

test('contiguous clips in one file advance the highlight without reloading or pausing', async()=>{
  const env=environment();
  await env.player.startSegment('s1');
  assert.equal(env.audio.src,'/a.wav');
  assert.equal(env.calls.loads,1);
  env.audio.currentTime=1.2;
  env.events.timeupdate();
  assert.equal(env.state.segmentId,'s1');
  assert.equal(env.nodes.get('#duration').textContent,'9','the player shows the whole chapter, not one clip or chunk');
  env.audio.currentTime=2.01;
  env.events.timeupdate();
  assert.equal(env.state.segmentId,'s2');
  assert.equal(env.state.audioSegmentId,'s2');
  assert.equal(env.audio.paused,false);
  assert.equal(env.calls.loads,1,'the chunk file is not reloaded');
  assert.ok(env.calls.highlights>=1);
  env.audio.currentTime=3.5;
  env.events.timeupdate();
  assert.equal(env.nodes.get('#elapsed').textContent,'3.5','elapsed time counts earlier passages in the chapter');
  // Chapter and book progress both count source text, so a one-chapter book shows the same figure.
  assert.equal(env.nodes.get('#player-progress').textContent,'38% of chapter · 38% of book');
  assert.equal(env.calls.positions.at(-1).segmentId,'s2','advancing saves the new passage');
  env.player.saveProgress();
  assert.equal(env.calls.positions.at(-1).currentTime,1.5,'saved progress is passage-relative');
  assert.equal(env.calls.updates.at(-1).currentTime,1.5,'buffer updates receive passage-relative time');
});

test('a clip end followed by another file moves to that file; seeking within a file only seeks', async()=>{
  const env=environment();
  await env.player.startSegment('s2',{offset:0.5});
  assert.equal(env.audio.src,'/a.wav');
  env.events.loadedmetadata();
  assert.equal(env.audio.currentTime,2.5,'resume seeks to clip start plus offset');
  env.audio.currentTime=5;
  await env.events.ended();
  for (let i=0;i<5;i++) await new Promise(resolve=>setImmediate(resolve));
  assert.equal(env.state.segmentId,'s3');
  assert.equal(env.audio.src,'/b.wav');
  assert.equal(env.calls.loads,2);
  env.audio.duration=4;
  await env.player.startSegment('s4',{offset:0.25});
  assert.equal(env.calls.loads,2,'another clip in the loaded file seeks instead of reloading');
  assert.equal(env.audio.currentTime,3.25);
});

test('a clip that ends before its file when the next passage lives elsewhere stops and moves on', async()=>{
  const env=environment();
  // s2 now comes from a newer, separate chunk file.
  env.takes.set('s2',chunk('/c.wav',0,3));
  await env.player.startSegment('s1');
  assert.equal(env.audio.src,'/a.wav');
  env.audio.currentTime=2.05;
  env.events.timeupdate();
  for (let i=0;i<5;i++) await tick();
  assert.equal(env.state.segmentId,'s2');
  assert.equal(env.audio.src,'/c.wav','the next passage loads its own file');
  assert.equal(env.calls.loads,2);
  assert.equal(env.audio.paused,false,'playback continues after the switch');
});

test('a voice example during chunk playback keeps the passage-relative position', async()=>{
  const env=environment();
  await env.player.startSegment('s2');
  env.audio.currentTime=3.5;  // 1.5 s into s2's clip (2-5 s)
  env.player.beginVoicePreview();
  assert.equal(env.state.voicePreview.offset,1.5);
  assert.equal(env.state.pendingOffset,1.5);
  assert.equal(env.state.segmentId,'s2');
});

test('the scrubber previews while dragging and seeks across the chapter on release', async()=>{
  const env=environment();
  await env.player.startSegment('s1');
  const scrubber=env.nodes.get('#audio-progress');
  assert.equal(scrubber.max,9);
  scrubber.listeners.input({target:{value:'6.5'}});
  assert.equal(env.audio.src,'/a.wav','dragging does not load or start passages');
  assert.equal(env.state.segmentId,'s1');
  assert.equal(env.nodes.get('#elapsed').textContent,'6.5','the drag target is previewed');
  scrubber.listeners.change({target:{value:'6.5'}});
  for (let i=0;i<5;i++) await tick();
  assert.equal(env.state.segmentId,'s3','release selects the passage at that chapter time');
  assert.equal(env.audio.src,'/b.wav');
  assert.equal(env.audio.paused,false,'playback continues after seeking');
  env.events.loadedmetadata();
  assert.equal(env.audio.currentTime,1.5,'offset is measured inside the target clip');
  const loads=env.calls.loads;
  scrubber.listeners.change({target:{value:'5.75'}});
  assert.equal(env.audio.currentTime,.75,'a seek inside the loaded passage only moves the playhead');
  assert.equal(env.calls.loads,loads);
  scrubber.listeners.change({target:{value:'3'}});
  for (let i=0;i<5;i++) await tick();
  assert.equal(env.state.segmentId,'s2');
  env.events.loadedmetadata();
  assert.equal(env.audio.currentTime,3,'s2 starts at 2 s in its chunk, plus the 1 s offset');
  assert.equal(env.nodes.get('#elapsed').textContent,'3');
});

test('unnarrated passages are estimated from text and a paused seek does not request narration', async()=>{
  const env=environment();
  env.takes.delete('s4');
  await env.player.startSegment('s1',{autoplay:false});
  assert.equal(env.nodes.get('#duration').textContent,'~8.5','an estimated total is marked approximate');
  const before=env.calls.prepares.length;
  env.nodes.get('#audio-progress').listeners.change({target:{value:'8.7'}});
  for (let i=0;i<5;i++) await tick();
  assert.equal(env.state.segmentId,'s4');
  assert.equal(env.state.pendingOffset,0,'an estimated span starts at the passage beginning');
  assert.equal(env.calls.prepares.length,before,'seeking while paused only moves the position');
  assert.equal(env.nodes.get('#elapsed').textContent,'8');
  assert.equal(env.nodes.get('#player-progress').textContent,'75% of chapter · 75% of book','the scrubber is in seconds; the percentages both count text');
});
