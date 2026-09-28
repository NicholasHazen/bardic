const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i=0;i<60;i++) await tick(); }
class Container {
  constructor() { this.innerHTML=''; this.listeners={}; }
  addEventListener(name,fn) { this.listeners[name]=fn; }
}
function story(count=20) {
  return {id:'buffer-book',revision:1,segments:Array.from({length:count+2},(_,i)=>({
    id:`p${i}`,chapter_id:i<count ? 'chapter-a' : 'chapter-b',start:i*20,end:i*20+10,text:`Original passage ${i}.`,
  }))};
}
function environment({book=story(),duration=12,handler,records,continuous}={}) {
  const calls=[], container=new Container(), storage=new Map();
  if (continuous !== undefined) storage.set(`bardic:listen:${book.id}`,JSON.stringify({continuous}));
  let plays=0;
  const audio=id=>({url:`/saved/${id}.wav`,duration,asset_id:id,available:true});
  const normal=call=>call.url.endsWith('/cancel') ? {status:'cancelled'} : {
    session:{id:'session-buffer'},audio:audio(call.body.segment_id),cached:false,
  };
  const scope={window:records ? {BardicDiagnostics:{record:(event,fields)=>records.push({event,...fields})}} : {},setTimeout:fn=>setImmediate(fn),localStorage:{
    getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),
  },fetch:async(url,options={})=>{
    const call={url,method:options.method||'GET',body:options.body ? JSON.parse(options.body) : null};
    calls.push(call);
    const result=handler ? await handler(call,normal,audio) : normal(call);
    if (result instanceof Error) throw result;
    return {ok:result?.ok!==false,status:result?.statusCode||200,json:async()=>result};
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/listen.js'),'utf8'),scope);
  const api=scope.window.BardicListen;
  const options={chapterId:'chapter-a',segmentId:'p0',playbackRate:2.5,
    status:{has_api_key:true,providers:[{id:'system',available:true}]},onPlay:()=>plays++};
  const click=action=>container.listeners.click({target:{closest:()=>({dataset:{listenAction:action}})}});
  const change=(field,value)=>container.listeners.change({target:{dataset:{listenField:field},value}});
  async function init() { await api.render(container,book,options); change('mode','simple'); }
  const posts=()=>calls.filter(call=>call.method==='POST'&&!call.url.endsWith('/cancel'));
  return {api,book,container,options,calls,posts,click,change,init,plays:()=>plays};
}

test('warmup uses measured durations, playback rate and resume offset; rendering is passive',async()=>{
  for (const [rate,offset,expected] of [[1,0,1],[2.5,0,3],[1,11,2]]) {
    const env=environment(); await env.init();
    env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:rate});
    assert.equal(env.posts().length,0,'Neither render nor a time update creates playback intent');
    const selected=await env.api.prepare(env.book,env.book.segments[0],{playbackRate:rate,offset});
    assert.equal(selected.asset_id,'p0');
    assert.equal(env.posts().length,expected);
    const buffer=env.api.getBuffer(env.book);
    assert.ok(Math.abs(buffer.seconds-(expected*12-offset)/rate)<1e-9);
    assert.ok(env.container.innerHTML.includes(`at ${rate}×`));
    assert.ok(env.container.innerHTML.includes('<progress'));
    await settle();
    assert.equal(env.posts().length,expected,'Warmup alone does not begin the rolling generation loop');
    env.api.stop(env.book);
  }
});

test('playing fills a rate-aware rolling buffer and replenishes it as time advances',async()=>{
  const env=environment({duration:10}); await env.init();
  await env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5});
  assert.equal(env.posts().length,3);
  env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:2.5,currentTime:0});
  await settle();
  assert.equal(env.posts().length,12,'120 seconds of audio covers 48 seconds at 2.5×');
  env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:2.5,currentTime:9});
  await settle();
  assert.equal(env.posts().length,13);
  const continuation=await env.api.prepare(env.book,env.book.segments[1],{playbackRate:2.5});
  assert.equal(continuation.asset_id,'p1');
  assert.equal(env.posts().length,13,'Already-buffered automatic continuation has no extra warmup');
  env.api.stop(env.book);
  env.api.updatePlayback(env.book,env.book.segments[10],{playbackRate:2.5});
  await settle();
  assert.equal(env.posts().length,13,'A paused time event never restarts work');
});

test('a shared speed change during warmup adjusts its remaining target without starting lookahead',async()=>{
  for (const [initialRate,nextRate,expected] of [[1,2.5,3],[2.5,1,1]]) {
    let release;
    const env=environment({duration:12,handler:(call,normal)=>{
      if (call.body?.segment_id==='p0') return new Promise(resolve=>{release=()=>resolve(normal(call));});
      return normal(call);
    }}); await env.init();
    env.options.playbackRate=initialRate;
    await env.api.render(env.container,env.book,env.options);
    const preparing=env.api.prepare(env.book,env.book.segments[0],{playbackRate:initialRate});
    while (!release) await tick();
    await env.api.render(env.container,env.book,{...env.options,playbackRate:nextRate,preparing:true});
    assert.equal(env.api.getBuffer(env.book).rate,nextRate);
    assert.equal(env.api.getBuffer(env.book).targetSeconds,10);
    assert.equal(env.posts().length,1,'Rendering the new rate never submits another in-flight request');
    release();
    assert.equal((await preparing).asset_id,'p0');
    await settle();
    assert.equal(env.posts().length,expected,'Warmup uses the new listening speed while respecting its passage cap');
    assert.ok(Math.abs(env.api.getBuffer(env.book).seconds-expected*12/nextRate)<1e-9);
    assert.ok(!env.calls.some(call=>call.url.endsWith('/cancel')));
    env.api.stop(env.book);
  }
});

test('the shared speed callback keeps playback intent and saved audio without cancelling queued work',async()=>{
  const env=environment({duration:12}); await env.init();
  await env.api.prepare(env.book,env.book.segments[0],{playbackRate:1});
  env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:1}); await settle();
  const initial=env.posts().length;
  assert.equal(initial,4);
  env.options.playing=true;
  env.options.onRateChange=rate=>{
    env.options.playbackRate=rate;
    env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:rate});
    void env.api.render(env.container,env.book,env.options);
  };
  await env.api.render(env.container,env.book,env.options);
  env.container.listeners.change({target:{dataset:{listenSpeed:''},value:'2.5'}});
  await settle();
  assert.equal(env.api.getBuffer(env.book).rate,2.5);
  assert.equal(env.posts().length,10,'The existing rolling buffer grows for the faster shared playback rate');
  assert.equal(env.posts().filter(call=>call.body.segment_id==='p0').length,1,'The current passage is never generated again');
  assert.ok(!env.calls.some(call=>call.url.endsWith('/cancel')),'A speed choice does not cancel the listening job');
  assert.match(env.container.innerHTML,/aria-label="Pause listening">Pause/);
  assert.match(env.container.innerHTML,/value="2.5" selected>2.5×/);
  env.api.stop(env.book);
});

test('short passages obey the warmup cap and 12-future-passage bound, without crossing chapters when continuous is off',async()=>{
  for (const count of [2,30]) {
    const env=environment({book:story(count),duration:.1,continuous:false}); await env.init();
    await env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5});
    assert.equal(env.posts().length,Math.min(3,count));
    env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:2.5});
    await settle();
    assert.equal(env.posts().length,Math.min(13,count));
    assert.ok(env.posts().every(call=>Number(call.body.segment_id.slice(1))<count));
    env.api.stop(env.book);
  }
});

test('continuous listening carries warmup and the rolling queue into the next chapter, still bounded',async()=>{
  for (const count of [2,30]) {
    const env=environment({book:story(count),duration:.1}); await env.init();
    await env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5});
    assert.equal(env.posts().length,3,'Warmup may include the next chapter\'s opening passages');
    env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:2.5});
    await settle();
    assert.equal(env.posts().length,Math.min(13,count+2),'At most 12 future passages, across the chapter boundary');
    assert.equal(env.posts().some(call=>Number(call.body.segment_id.slice(1))>=count),count<12);
    // Playback moving into the next chapter keeps the same intent and queue.
    const next=env.book.segments[count];
    env.api.updatePlayback(env.book,next,{playbackRate:2.5});
    assert.equal(env.api.getBuffer(env.book).preparing || env.api.getBuffer(env.book).seconds>0,true);
    const continued=await env.api.prepare(env.book,next,{playbackRate:2.5,continuation:true});
    assert.equal(continued?.asset_id,next.id);
    env.api.stop(env.book);
  }
});

test('explicit rest-of-chapter preparation is serial, chapter-scoped and never autoplays',async()=>{
  let inFlight=0,maximum=0;
  const env=environment({book:story(8),handler:async(call,normal)=>{
    inFlight++; maximum=Math.max(maximum,inFlight); await tick(); inFlight--; return normal(call);
  }}); await env.init();
  env.options.segmentId='p2';
  await env.api.render(env.container,env.book,env.options);
  env.click('prepare-chapter'); await settle();
  assert.equal(maximum,1);
  assert.deepEqual(env.posts().map(call=>call.body.segment_id),['p2','p3','p4','p5','p6','p7']);
  assert.equal(env.plays(),0);
  assert.ok(env.container.innerHTML.includes('rest of this chapter is saved and ready'));
  assert.equal(env.api.getBuffer(env.book).chapterPreparation,false);
});

test('chapter preparation waits for the audition barrier before submitting any passage',async()=>{
  let release;
  const env=environment({book:story(3)}); await env.init();
  env.options.beforeChapterPrepare=()=>new Promise(resolve=>{release=resolve;});
  await env.api.render(env.container,env.book,env.options);
  env.click('prepare-chapter'); await settle();
  assert.equal(env.posts().length,0);
  assert.equal(env.api.getBuffer(env.book).chapterPreparation,true);
  release(true); await settle();
  assert.deepEqual(env.posts().map(call=>call.body.segment_id),['p0','p1','p2']);
  assert.equal(env.plays(),0);
});

test('Stop, book change or a newer chapter intent cancels a chapter waiting on an audition',async()=>{
  for (const action of ['stop','book','newer']) {
    const releases=[];
    const env=environment({book:story(3)}); await env.init();
    env.options.beforeChapterPrepare=()=>new Promise(resolve=>releases.push(resolve));
    await env.api.render(env.container,env.book,env.options);
    env.click('prepare-chapter'); await tick();
    if (action==='stop') env.click('stop');
    else if (action==='book') await env.api.render(env.container,{...env.book,id:'other-book'},env.options);
    else {
      env.options.segmentId='p2';
      await env.api.render(env.container,env.book,env.options);
      // A newer explicit request uses its own intent and barrier, even while
      // the earlier request has yet to receive its cancellation completion.
      void env.api.prepareChapter(env.book,env.book.segments[2]);
    }
    releases[0](true); await settle();
    assert.equal(env.posts().length,0,'The stale chapter cannot submit after its wait finishes');
    if (action==='newer') {
      assert.equal(releases.length,2);
      releases[1](true); await settle();
      assert.deepEqual(env.posts().map(call=>call.body.segment_id),['p2']);
    }
  }
});

test('a failed audition barrier preserves a retryable chapter without generating or replaying automatically',async()=>{
  const env=environment({book:story(2)}); await env.init();
  let attempts=0;
  env.options.beforeChapterPrepare=async()=>{
    if (++attempts===1) throw new Error('The previous example is still running.');
    return true;
  };
  await env.api.render(env.container,env.book,env.options);
  env.click('prepare-chapter'); await settle();
  assert.equal(env.posts().length,0);
  assert.match(env.container.innerHTML,/previous example is still running/);
  assert.match(env.container.innerHTML,/data-listen-action="retry">Try again/);
  env.click('retry'); await settle();
  assert.equal(attempts,2,'Retry must also cross the audition barrier');
  assert.deepEqual(env.posts().map(call=>call.body.segment_id),['p0','p1']);

  const stopped=environment({book:story(2)}); await stopped.init();
  stopped.options.beforeChapterPrepare=async()=>false;
  await stopped.api.render(stopped.container,stopped.book,stopped.options);
  stopped.click('prepare-chapter'); await settle();
  assert.equal(stopped.posts().length,0);
  assert.equal(stopped.api.getBuffer(stopped.book).chapterPreparation,false);
  assert.match(stopped.container.innerHTML,/Chapter preparation stopped before requesting audio/);
});

test('a future failure preserves ready playback and requires an explicit retry',async()=>{
  let failure=true;
  const env=environment({handler:(call,normal)=>{
    if (call.body?.segment_id==='p1'&&failure) { failure=false; return {ok:false,statusCode:429,detail:'Provider quota unavailable.'}; }
    return normal(call);
  }}); await env.init();
  await env.api.prepare(env.book,env.book.segments[0]);
  env.api.updatePlayback(env.book,env.book.segments[0]); await settle();
  assert.equal(env.posts().length,2);
  assert.equal(env.api.resolve(env.book,env.book.segments[0]).asset_id,'p0');
  assert.match(env.container.innerHTML,/Provider quota unavailable/);
  assert.match(env.container.innerHTML,/data-listen-action="retry">Try again/);
  env.api.updatePlayback(env.book,env.book.segments[0]); await settle();
  assert.equal(env.posts().length,2);
  assert.equal((await env.api.prepare(env.book,env.book.segments[0])).asset_id,'p0');
  await assert.rejects(env.api.prepare(env.book,env.book.segments[1]),/quota unavailable/);
  assert.equal(env.posts().length,2,'Automatic continuation must not retry a failed paid request');
  env.click('retry'); await settle();
  assert.ok(env.posts().length>2);
  env.api.stop(env.book);
});

test('warmup failure beyond the first passage does not prevent playback of saved current audio',async()=>{
  const env=environment({duration:8,handler:(call,normal)=>call.body?.segment_id==='p1'
    ? {ok:false,statusCode:401,detail:'Add a provider key.'} : normal(call)}); await env.init();
  assert.equal((await env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5})).asset_id,'p0');
  assert.equal(env.posts().length,2);
  env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:2.5}); await settle();
  assert.equal(env.posts().length,2);
  assert.match(env.container.innerHTML,/Add a provider key/);
  env.api.stop(env.book);
});

test('stop invalidates queued passages and cancels a late job without returning audio',async()=>{
  let release;
  const env=environment({handler:(call,normal)=>call.url.endsWith('/cancel') ? normal(call)
    : new Promise(resolve=>{release=resolve;})}); await env.init();
  const selected=env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5});
  const queued=env.api.ensure(env.book,env.book.segments[4]);
  await tick();
  env.api.stop(env.book);
  release({session:{id:'late'},job:{id:'late-job',status:'queued'}});
  assert.equal(await selected,null);
  assert.equal(await queued,null);
  await settle();
  assert.equal(env.posts().length,1);
  assert.ok(env.calls.some(call=>call.url==='/api/jobs/late-job/cancel'));
  assert.equal(env.api.resolve(env.book,env.book.segments[0]),null);
});

test('the stopped-listen barrier waits for a late POST job using only cancellation and status reads',async()=>{
  let releasePost,releaseStatus;
  const env=environment({handler:(call,normal)=>{
    if (call.url.endsWith('/cancel')) return normal(call);
    if (call.method==='POST') return new Promise(resolve=>{releasePost=resolve;});
    return new Promise(resolve=>{releaseStatus=resolve;});
  }}); await env.init();
  const pending=env.api.prepare(env.book,env.book.segments[0]);
  while (!releasePost) await tick();
  env.api.stop(env.book);
  let settled=false;
  const barrier=env.api.waitForStopped(env.book).then(value=>{settled=true;return value;});
  await tick();
  assert.equal(settled,false,'An uncertain POST must reveal its job before the barrier opens');
  assert.equal(env.posts().length,1);
  releasePost({job:{id:'old-listen',status:'running'}});
  assert.equal(await pending,null);
  while (!releaseStatus) await tick();
  assert.equal(settled,false);
  assert.ok(env.calls.some(call=>call.url==='/api/jobs/old-listen/cancel'));
  assert.match(env.container.innerHTML,/Stopped\. Finished audio is saved/,'Read-only settlement leaves the stopped panel unchanged');
  releaseStatus([{id:'old-listen',status:'cancelled'}]);
  assert.equal(await barrier,true);
  assert.equal(env.posts().length,1,'The barrier never generates another passage');
  assert.equal(env.api.getBuffer(env.book).preparing,false);
  assert.equal(await env.api.waitForStopped(env.book),true);
});

test('the stopped-listen barrier is invalidated by another book or fresh playback intent',async()=>{
  for (const action of ['book','play']) {
    let releasePost,releaseStatus;
    const env=environment({handler:(call,normal)=>{
      if (call.url.endsWith('/cancel')) return normal(call);
      if (call.method==='POST') return new Promise(resolve=>{releasePost=resolve;});
      return new Promise(resolve=>{releaseStatus=resolve;});
    }}); await env.init();
    const pending=env.api.prepare(env.book,env.book.segments[0]);
    while (!releasePost) await tick();
    env.api.stop(env.book);
    const barrier=env.api.waitForStopped(env.book);
    if (action==='book') await env.api.render(env.container,{...env.book,id:'other-book'},env.options);
    else {
      // A new explicit intent must not be mistaken for the request we stopped.
      const newer=env.api.prepare(env.book,env.book.segments[1]);
      env.api.stop(env.book);
      await newer;
    }
    releasePost({job:{id:'old-listen',status:'running'}});
    assert.equal(await pending,null);
    assert.equal(await barrier,false);
    assert.equal(releaseStatus,undefined,'A stale barrier must not even begin status polling');
    assert.equal(env.posts().length,1);
  }
});

test('book, chapter, source or voice changes invalidate pending warmup and future scheduling',async()=>{
  for (const action of ['book','chapter','source','voice']) {
    let release;
    const env=environment({handler:()=>new Promise(resolve=>{release=resolve;})}); await env.init();
    const preparing=env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5});
    await tick();
    if (action==='voice') env.change('voice','Samantha');
    else {
      const next=action==='book' ? {...env.book,id:'another'} : action==='source'
        ? {...env.book,segments:env.book.segments.map((s,i)=>i===0?{...s,text:'New canonical source.'}:s)} : env.book;
      await env.api.render(env.container,next,{...env.options,chapterId:action==='chapter'?'chapter-b':'chapter-a'});
    }
    release({audio:{url:'/late.wav',duration:20,asset_id:'late'}});
    assert.equal(await preparing,null,action);
    await settle();
    assert.equal(env.posts().length,1,action);
  }
});

test('polling retries transient reads twice; an uncertain POST is never automatically retried',async()=>{
  let polls=0;
  const env=environment({handler:(call,_normal,audio)=>call.method==='POST'
    ? {job:{id:'poll-job',status:'queued'}} : ++polls<3 ? new Error('Temporary connection failure')
      : [{id:'poll-job',status:'completed',audio:audio('p0')}]}); await env.init();
  assert.equal((await env.api.ensure(env.book,env.book.segments[0])).asset_id,'p0');
  assert.equal(polls,3); assert.equal(env.posts().length,1);
  const uncertain=environment({handler:()=>new Error('POST response was lost')}); await uncertain.init();
  await assert.rejects(uncertain.api.prepare(uncertain.book,uncertain.book.segments[0]),/response was lost/);
  uncertain.api.updatePlayback(uncertain.book,uncertain.book.segments[0]); await settle();
  assert.equal(uncertain.posts().length,1);
});

test('evicting an unplayable browser asset only rechecks it on explicit preparation',async()=>{
  const env=environment(); await env.init();
  await env.api.ensure(env.book,env.book.segments[0]);
  env.api.forgetAudio(env.book,env.book.segments[0]);
  assert.equal(env.api.resolve(env.book,env.book.segments[0]),null);
  await settle(); assert.equal(env.posts().length,1);
  await env.api.ensure(env.book,env.book.segments[0]);
  assert.equal(env.posts().length,2,'Backend rechecks matching local cache without a force flag');
  assert.ok(env.posts().every(call=>!call.body.force));
});


test('a new selection waits for cooperative cancellation before posting another passage',async()=>{
  let releasePost,previousFinished=false,polls=0;
  const env=environment({handler:(call,normal)=>{
    if (call.url.endsWith('/cancel')) return {status:'running',cancel_requested:true};
    if (call.url.startsWith('/api/jobs?')) {
      polls++;
      return [{id:'slow-job',status:previousFinished?'cancelled':'running'}];
    }
    if (call.body.segment_id==='p0') return new Promise(resolve=>{releasePost=resolve;});
    assert.equal(previousFinished,true,'No new POST until the known old provider request has finished');
    return normal(call);
  }}); await env.init();
  const old=env.api.prepare(env.book,env.book.segments[0]); await tick();
  env.api.stop(env.book);
  const selected=env.api.prepare(env.book,env.book.segments[5]);
  releasePost({job:{id:'slow-job',status:'running'}});
  assert.equal(await old,null);
  while (!polls) await tick();
  assert.equal(env.posts().length,1);
  previousFinished=true;
  assert.equal((await selected).asset_id,'p5');
  assert.equal(env.posts().length,2);
  env.api.stop(env.book);
});

test('polling exhaustion retains the known job for Stop and the next explicit retry',async()=>{
  let failReads=true,finished=false;
  const env=environment({handler:(call,normal,audio)=>{
    if (call.url.endsWith('/cancel')) return {status:'running'};
    if (call.url.startsWith('/api/jobs?')) {
      if (failReads) throw new Error('Read connection unavailable');
      finished=true;
      return [{id:'known-job',status:'completed',audio:audio('p0')}];
    }
    if (!finished) return {job:{id:'known-job',status:'running'}};
    return normal(call);
  }}); await env.init();
  await assert.rejects(env.api.prepare(env.book,env.book.segments[0]),/Read connection unavailable/);
  assert.equal(env.posts().length,1);
  env.api.stop(env.book);
  assert.ok(env.calls.some(call=>call.url==='/api/jobs/known-job/cancel'));
  failReads=false;
  assert.equal((await env.api.prepare(env.book,env.book.segments[1])).asset_id,'p1');
  assert.equal(env.posts().length,2);
  assert.equal(finished,true);
  env.api.stop(env.book);
});


test('buffer diagnostics contain safe IDs and stage/status without source or error text',async()=>{
  const records=[];
  const env=environment({records,handler:()=>({ok:false,statusCode:429,detail:'Sensitive source and provider response go here.'})});
  await env.init();
  await assert.rejects(env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5}));
  assert.equal(records.length,1);
  assert.equal(records[0].event,'buffer_failed');
  assert.equal(records[0].book_id,env.book.id);
  assert.equal(records[0].segment_id,'p0');
  assert.equal(records[0].playback_rate,2.5);
  assert.equal(records[0].http_status,429);
  assert.equal(records[0].operation,'prepare');
  assert.ok(!JSON.stringify(records).includes('Sensitive'));
  assert.ok(!JSON.stringify(records).includes('Original passage'));
  assert.ok(Object.keys(records[0]).every(key=>['event','book_id','segment_id','session_id','job_id','playback_rate','http_status','operation'].includes(key)));
});


test('Stop keeps its status when a late POST or completed-job response arrives',async()=>{
  for (const responseType of ['post','poll']) {
    let release;
    const env=environment({handler:(call,normal,audio)=>{
      if (call.url.endsWith('/cancel')) return normal(call);
      if (responseType==='post') return new Promise(resolve=>{release=()=>resolve({audio:audio('p0')});});
      if (call.method==='POST') return {job:{id:'finishing-job',status:'running'}};
      return new Promise(resolve=>{release=()=>resolve([{id:'finishing-job',status:'completed',audio:audio('p0')}]);});
    }}); await env.init();
    const preparing=env.api.prepare(env.book,env.book.segments[0],{playbackRate:2.5});
    while (!release) await tick();
    env.click('stop');
    assert.match(env.container.innerHTML,/Stopped\. Finished audio is saved/);
    release();
    assert.equal(await preparing,null);
    await settle();
    assert.equal(env.posts().length,1);
    assert.match(env.container.innerHTML,/Stopped\. Finished audio is saved/);
    assert.doesNotMatch(env.container.innerHTML,/Passage saved\./);
    assert.equal(env.api.getBuffer(env.book).preparing,false);
  }
});

test('Stop invoked during the completion notification invalidates the result and preserves its label',async()=>{
  const env=environment(); await env.init();
  env.options.onChange=()=>env.api.stop(env.book);
  await env.api.render(env.container,env.book,env.options);
  assert.equal(await env.api.ensure(env.book,env.book.segments[0]),null);
  await settle();
  assert.equal(env.posts().length,1);
  assert.match(env.container.innerHTML,/Stopped\. Finished audio is saved/);
  assert.equal(env.api.getBuffer(env.book).preparing,false);
});

test('a late saved-take read cannot overwrite Stop; stopped rate follows current render options',async()=>{
  let readStarted,releaseRead;
  const env=environment({handler:(call,normal)=>{
    if (call.url.includes('/takes?')) {
      readStarted=true;
      return new Promise(resolve=>{releaseRead=()=>resolve({takes:[]});});
    }
    return normal(call);
  }}); await env.init();
  await env.api.ensure(env.book,env.book.segments[0]);
  const reading=env.api.render(env.container,env.book,env.options);
  while (!readStarted) await tick();
  env.api.stop(env.book);
  releaseRead(); await reading; await settle();
  assert.match(env.container.innerHTML,/Stopped\. Finished audio is saved/);
  await env.api.render(env.container,env.book,{...env.options,playbackRate:2});
  assert.equal(env.api.getBuffer(env.book).rate,2);
  assert.match(env.container.innerHTML,/at 2×/);
  assert.equal(env.posts().length,1);
});
