const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../bardic/static/diagnostics.js'), 'utf8');
function reporter(fetch) {
  const scope = {window:{},fetch};
  vm.runInNewContext(source,scope);
  return scope.window.BardicDiagnostics;
}
test('diagnostics sends operational fields locally and drops text, credentials, URLs and stacks', async () => {
  const sent = [];
  const log = reporter(async(url,options) => {sent.push({url,...options});return {ok:true};});
  log.record('buffer_failed',{book_id:'book-1',segment_id:'seg-1',job_id:'job-1',session_id:'session-1',
    playback_rate:2.5,http_status:503,operation:'poll',message:'Private book text and secret-key',
    text:'Private chapter',api_key:'secret-key',url:'https://secret.example',stack:'Private stack'});
  assert.equal(sent.length,1);
  assert.equal(sent[0].url,'/api/diagnostics');
  assert.equal(sent[0].keepalive,true);
  assert.deepEqual(JSON.parse(sent[0].body),{event:'buffer_failed',book_id:'book-1',segment_id:'seg-1',
    session_id:'session-1',job_id:'job-1',operation:'poll',playback_rate:2.5,http_status:503});
  assert.ok(!JSON.stringify(sent).includes('secret-key'));
});
test('repeated diagnostics are coalesced and malformed fields never reach the endpoint', () => {
  const sent=[];
  const log=reporter(async(_url,options)=>{sent.push(JSON.parse(options.body));});
  const issue={segment_id:'s1',playback_rate:2,media_error_code:2,operation:'media'};
  log.record('playback_media_error',issue);
  log.record('playback_media_error',issue);
  log.record('unknown_event',issue);
  assert.equal(sent.length,1);
  log.record('playback_media_error',{book_id:'book text with spaces',playback_rate:Infinity,
    http_status:999,media_error_code:10,operation:'secret-string'});
  assert.deepEqual(sent[1],{event:'playback_media_error'});
});
test('network or synchronous logger failure never interrupts playback or retries', async () => {
  let attempts=0;
  const log=reporter(()=>{attempts++;return Promise.reject(new Error('offline'));});
  assert.doesNotThrow(()=>log.record('playback_waiting',{segment_id:'s1'}));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(attempts,1);
  const unavailable=reporter(()=>{throw new Error('disabled');});
  assert.doesNotThrow(()=>unavailable.record('playback_waiting'));
});
