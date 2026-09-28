// Exercise the actual settings helpers without a browser or provider requests.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {id:id.slice(1),value:'',innerHTML:'',textContent:'',hidden:false,required:false});
    return nodes.get(id);
  };
  const calls = [];
  const sandbox = {
    document:{querySelector:node}, Audio:class {constructor() {this.preload='';}},
    localStorage:{getItem:()=>null}, setTimeout,clearTimeout,URL,FormData,
    renderAccountCheck:()=>{}, updateSettingsControls:()=>{}, updateStatusUI:()=>{},
    showInlineError:(_id,message)=>{throw new Error(message);},
    fetch:async (path, options) => {
      calls.push({path,body:JSON.parse(options.body)});
      const body = path === '/api/settings' ? structuredClone(sandbox.api.state.status) : {
        provider:'openai',state:'ready',message:'Listed models',models:[
          {id:'gpt-6-luna',label:'GPT-6 Luna',roles:['analysis','preprocess'],structured_output:true,availability:'listed'},
          {id:'gpt-6-sol',label:'GPT-6 Sol',roles:['analysis','preprocess'],structured_output:true,availability:'listed'},
        ],
      };
      return {ok:true,headers:{get:()=> 'application/json'},json:async()=>body};
    },
  };
  vm.createContext(sandbox);
  const source = fs.readFileSync(new URL('../bardic/static/app.js', `file://${__filename}`), 'utf8');
  vm.runInContext(source.split('function clearKeyInputs()')[0] + '\nglobalThis.api={state,fillSettings,fillProviderModels,modelValue,modelPickerChanged,refreshModels};', sandbox);
  sandbox.api.state.status = {
    analysis_provider:'openai',tts_model:'gemini-tts',tts_models:['gemini-tts','gemini-lite-tts'],
    analysis_models_by_provider:{gemini:'gemini-3.8-flash',openai:'gpt-6-sol',anthropic:'claude-sonnet-5'},
    preprocess_models_by_provider:{gemini:'gemini-3.5-flash-lite',openai:'gpt-6-luna',anthropic:'claude-haiku'},
    analysis_providers:[], model_catalogs:{},
  };
  for (const provider of ['gemini','openai','anthropic']) {
    const base = sandbox.api.state.status;
    base.model_catalogs[provider] = {models:[
      {id:base.analysis_models_by_provider[provider],label:'Balanced',structured_output:true,roles:['analysis','preprocess'],tier:'balanced'},
      {id:base.preprocess_models_by_provider[provider],label:'Economy',structured_output:true,roles:['analysis','preprocess'],tier:'economy',input_usd_per_million:.1,output_usd_per_million:.5,price_date:'2026-09-27'},
      {id:'other-model',label:'Another choice',structured_output:null,roles:['analysis','preprocess']},
    ]};
  }
  return {api:sandbox.api,node,calls};
}

test('selected values do not filter out the rest of the model list', () => {
  const {api,node} = setup();
  api.fillSettings();
  assert.equal(node('#analysis-model-openai').value,'gpt-6-sol');
  assert.match(node('#analysis-model-openai').innerHTML,/gpt-6-luna/);
  assert.match(node('#analysis-model-openai').innerHTML,/other-model/);
  assert.match(node('#analysis-model-openai').innerHTML,/Custom model ID/);
  assert.equal(node('#preprocess-model-openai').value,'gpt-6-luna');
  assert.equal(node('#preprocess-model-openai-custom').hidden,true);
  assert.match(node('#preprocess-model-openai-details').textContent,/\$0.1 input/);
  assert.match(node('#tts-model').innerHTML,/gemini-lite-tts/);
});

test('existing custom IDs are retained separately for both roles', () => {
  const {api,node} = setup();
  api.state.status.analysis_models_by_provider.openai='custom-analyzer:latest';
  api.state.status.preprocess_models_by_provider.openai='custom-preprocessor';
  api.fillSettings();
  assert.equal(node('#analysis-model-openai').value,'__custom__');
  assert.equal(api.modelValue('analysis','openai'),'custom-analyzer:latest');
  assert.equal(api.modelValue('preprocess','openai'),'custom-preprocessor');
  assert.equal(node('#analysis-model-openai-custom').hidden,false);
  assert.equal(node('#analysis-model-openai-custom').required,true);
  node('#analysis-model-openai').value='gpt-6-sol';
  api.modelPickerChanged('analysis','openai');
  assert.equal(node('#analysis-model-openai-custom').hidden,true);
  assert.equal(node('#analysis-model-openai-custom').required,false);
});

test('refresh saves only its entered key and preserves unsaved model choices', async () => {
  const {api,node,calls} = setup();
  api.fillSettings();
  node('#api-key-openai').value='new-key';
  node('#analysis-model-openai').value='__custom__';
  node('#analysis-model-openai-custom').value='private-model';
  node('#preprocess-model-openai').value='gpt-6-sol';
  node('#api-key-anthropic').value='unsaved-other-key';
  await api.refreshModels('openai');
  assert.deepEqual(calls,[{path:'/api/settings',body:{api_keys:{openai:'new-key'}}},{path:'/api/models/openai/refresh',body:{}}]);
  assert.equal(api.modelValue('analysis','openai'),'private-model');
  assert.equal(api.modelValue('preprocess','openai'),'gpt-6-sol');
  assert.equal(node('#api-key-anthropic').value,'unsaved-other-key');
  assert.equal(node('#api-key-openai').value,'');
  assert.equal(api.state.settingsBusy,false);
});

test('opening settings never refreshes inventory or checks accounts', () => {
  const {api,calls} = setup();
  api.fillSettings();
  assert.deepEqual(calls,[]);
});

test('model labels from responses are escaped', () => {
  const {api,node} = setup();
  api.state.status.model_catalogs.openai.models[0].label='<img onerror="alert(1)">';
  api.fillSettings();
  assert.ok(!node('#analysis-model-openai').innerHTML.includes('<img'));
  assert.match(node('#analysis-model-openai').innerHTML,/&lt;img/);
});
