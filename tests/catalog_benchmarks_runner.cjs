const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const root=path.join(__dirname,'..','app','module_builtins');
const dict=JSON.parse(fs.readFileSync(path.join(root,'catalog/i18n.json'),'utf8')).en;
const nodes={},make=()=>({innerHTML:'',textContent:'',hidden:true,dataset:{},classList:{toggle(){}},setAttribute(){},addEventListener(){},querySelector(){return {focus(){},open:false}},focus(){},scrollTop:0});
const ctx={console,setTimeout,clearTimeout,Date,Math,JSON,Promise,Set,Map,Number,String,Array,Object,Infinity,
  window:{addEventListener(){}},document:{querySelector:s=>nodes[s]||(nodes[s]=make()),addEventListener(){}},
  Memu:{saved:{},lang:'en',t:(key,vars)=>{let value=dict[key]??'??'+key;if(typeof value==='object')value=value.other;for(const[k,v]of Object.entries(vars||{}))value=value.replaceAll('{'+k+'}',v);return value},
    i18n:{has:key=>key in dict,number:(number,options)=>number.toFixed(options?.maximumFractionDigits||0)},
    save:async()=>{},fetch:async()=>{throw new Error('offline fixture')},models:async()=>[],onLanguage(){},ready(){},open:async()=>{},copy:async()=>{}}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(root,'benchmarks/data.js'),'utf8')+'\n'+fs.readFileSync(path.join(root,'catalog/main.js'),'utf8'),ctx);
ctx.assert=assert;
vm.runInContext(`
const model={id:'google/gemini-3.8-flash:free',name:'Gemini 3.8 Flash',org:'Google',slug:'google'};
benchmarkState.cache.arena.text={at:Date.now(),url:'https://arena.ai/leaderboard/text',rows:[
 {model:'Gemini 3.8 Flash (high)',org:'Google DeepMind',score:1500},
 {model:'Gemini 3.8 Flash (low)',org:'Google',score:1200},
 {model:'Gemini 3.8 Flash',org:'Other vendor',score:9999},
 {model:'Gemini 3.8 Flash Lite',org:'Google',score:1800},
 {model:'Gemini 3.8 Pro',org:'Google',score:1600},
 {model:'Qwen 3',org:'Qwen',score:1100},
]};
const result=benchmarkResults(model,'arena:text');
assert.equal(result.rows.length,2);assert.equal(result.selected.score,1500);assert.equal(result.place,4);
assert.equal(result.defaultBest,true);assert(benchmarkValue(model,'arena:text').includes('Gemini 3.8 Flash (high)'));
benchmarkVariants.set(model.id+'|arena:text','Gemini 3.8 Flash (low)');assert.equal(benchmarkResults(model,'arena:text').selected.score,1200);
assert.equal(benchmarkResults({...model,id:'google/gemini-3.8-flash-high',name:'Gemini 3.8 Flash (high)'},'arena:text').selected.score,1500);
assert.equal(benchmarkResults({...model,id:'google/gemini-3.8-flash-lite',name:'Gemini 3.8 Flash Lite'},'arena:text').selected.score,1800);
assert.equal(benchmarkResults({...model,id:'google/gemini-3.8-flash-mini',name:'Gemini 3.8 Flash Mini'},'arena:text').selected,null);
benchmarkVariants.set(model.id+'|arena:text','Removed test variant');
assert(benchmarkResults(model,'arena:text').variantMissing);assert(benchmarkValue(model,'arena:text').includes('previously selected variant is unavailable'));
benchmarkState.cache.aa={at:Date.now()-7*3600000,rows:[
 {model:'Gemini 3.8 Flash (high)',org:'Google',m:{intelligence:80,ttft:1.2}},
 {model:'Gemini 3.8 Flash (low)',org:'Google',m:{intelligence:60,ttft:.3}},
 {model:'Gemini 3.8 Pro',org:'Google',m:{intelligence:90,ttft:.5}},
 {model:'Alpha 1',m:{intelligence:40,ttft:2}}, {model:'Beta 1',m:{intelligence:30,ttft:3}}
]};
const latency=benchmarkResults(model,'aa:ttft');assert.equal(latency.selected.score,.3);assert.equal(latency.data.score.higher,false);assert.equal(latency.place,1);
assert(benchmarkMeta('aa:ttft',latency.data).includes('older than 6 hours'));
benchmarkState.errors.aa='source failed';assert(benchmarkMeta('aa:intelligence',benchmarks.boardData('aa:intelligence')).includes('source failed'));
assert.equal(benchmarkResults(model,'simple:simplebench').selected,null);assert(benchmarkValue(model,'simple:simplebench').includes('Source not loaded yet'));
assert.equal(benchmarkResults(model,'memu:index').selected,null); // ambiguous cross-vendor source is not merged into an index
benchmarkState.cache.arena.text.rows=benchmarkState.cache.arena.text.rows.filter(row=>row.org!=='Other vendor');
assert(benchmarkResults(model,'memu:index').selected);assert(benchmarkValue(model,'memu:index').includes('Best variants on each board'));
assert.equal(benchmarks.canon('meta-llama/llama-3.1-70b:free'),'llama-3.1-70b');
assert.equal(benchmarks.canon('anthropic/claude-opus-5-20260101-thinking'),'claude-opus-5');
assert.equal(benchmarks.boardData('aa:gpqa').rows.length,0); // incomplete legacy metrics stay absent; never zero-filled
const base=slim({id:'a/one',name:'One',context_length:1050000,top_provider:{max_completion_tokens:128000},pricing:{prompt:'0.000002',completion:'0.00001'}});
const other={...base,id:'b/two',name:'Two',context:1000000,price:{...base.price}};
function bestCells(key){renderCompare(nodesForTest);const label=t(key);const tr=nodesForTest.innerHTML.split('<tr>').find(row=>row.startsWith('<th>'+label+'</th>'));return [...tr.matchAll(/<td class="([^"]*)">/g)].map(match=>match[1]==='best');}
const nodesForTest={innerHTML:''};state.cache={rows:[base,other]};state.compare=[base.id,other.id];
assert.deepEqual(bestCells('fact.maxout'),[false,false]);assert.deepEqual(bestCells('fact.input'),[false,false]);assert.deepEqual(bestCells('fact.output'),[false,false]);
assert.deepEqual(bestCells('fact.context'),[true,false]);state.compare.reverse();assert.deepEqual(bestCells('fact.context'),[false,true]);
other.price.input=0;assert.deepEqual(bestCells('fact.input'),[true,false]);other.price.input=undefined;assert.deepEqual(bestCells('fact.input'),[false,false]);
other.context=1050001;assert.deepEqual(bestCells('fact.context'),[false,false]);
const low={...base,id:'c/three',context:32000};state.cache.rows.push(low);state.compare=[base.id,other.id,low.id];assert.deepEqual(bestCells('fact.context'),[true,true,false]);
const fixture={router:{name:'Router',models:{a:{id:'vendor/model',canonical_model_id:'vendor/core',cost:{input:12},limit:{context:9000}}}},vendor:{name:'Vendor',models:{a:{id:'core',canonical_model_id:'vendor/core',cost:{input:2,output:0},limit:{context:8000}},b:{id:'core-v2',canonical_model_id:'vendor/core-v2',modalities:{input:['audio'],output:['text']}}}}};
const expanded=modelsDevRows(fixture);assert.equal(expanded.length,2);const direct=expanded.find(row=>row.id==='vendor/core');assert.equal(direct.price.input,2);assert.equal(direct.price.output,0);assert.equal(direct.provider,'Vendor');assert.equal(direct.context,8000);assert.equal(expanded.find(row=>row.id==='vendor/core-v2').price.input,undefined);
assert.equal(modelsDevRows({r:{name:'R',models:{x:{id:'reseller-only-alias'}}}}).length,0); // reseller aliases without a canonical lab model are not separate models
assert.equal(modelsDevRows({a:{models:{x:{id:'x',canonical_model_id:'v/x',status:'deprecated'}}}}).length,0);
assert.equal(modelKey('anthropic/claude-sonnet-4.5'),modelKey('claude-sonnet-4-5-20250929'));assert.notEqual(modelKey('openai/gpt-5'),modelKey('openai/gpt-5-mini'));
assert.equal(mergeCatalogs([{id:'anthropic/claude-sonnet-4.5',cutoff:''}],[{id:'anthropic/claude-sonnet-4-5',cutoff:'2025-03'}]).length,1);
console.log(JSON.stringify({passed:true,comparison_ties:true,expanded_catalog_deduplicated:true,variant_selection:true,exact_model_family:true,org_collision_excluded:true,lower_is_better:true,missing_not_zero:true,stale_and_partial_errors:true,index_same_core:true}));
`,ctx);
vm.runInContext(`(async()=>{
  await new Promise(resolve=>setTimeout(resolve,0));
  let calls=0, failModelsDev=false;
  Memu.fetch=async({url})=>{calls++;if(url===MODELS_DEV&&failModelsDev)throw new Error('fixture unavailable');return {text:JSON.stringify(url===MODELS_DEV?fixture:{data:[{id:'vendor/core',name:'Vendor: Core',pricing:{prompt:'0.000007',completion:'0.000008'}}]})};};
  state.cache={at:Date.now(),rows:[]};await load();
  assert.equal(calls,2);assert.equal(state.cache.schema,CACHE_SCHEMA);assert.equal(state.cache.rows.length,2);
  assert.equal(byId('vendor/core').price.input,7);assert.equal(byId('vendor/core').source,'openrouter');
  await load();assert.equal(calls,2); // combined cache is reused; legacy cache was refreshed above
  failModelsDev=true;await load(true);assert.equal(state.cache.rows.length,2);assert(state.error.includes('Models.dev'));assert.equal(state.cache.partial,true);
  assert.equal(byId('vendor/core-v2').source,'models.dev'); // partial failure preserves that source's previous rows
  console.log('PASS: catalog merging, cache migration and partial-source recovery');
})()`,ctx).catch(error=>{console.error(error);process.exitCode=1});
