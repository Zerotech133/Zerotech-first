// 执行完整产品脚本；隔离DOM与延迟API回执，不请求网络。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const text=fs.readFileSync(process.argv[2],'utf8');
const scripts=[...text.matchAll(/_(?:DAILY_)?JS = r"""([\s\S]*?)"""/g)].map(m=>m[1].replace('<script>','').replace('</script>',''));
const settle=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
class Element {
  constructor(tag='div',dataset={}){this.tagName=tag.toUpperCase();this.dataset=dataset;this.children=[];this.handlers={};this.attrs={};this.value='';this.disabled=false;this.hidden=false;this.open=false;this._text='';}
  set textContent(value){this._text=String(value??'');this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  appendChild(node){this.children.push(node);return node;}
  append(...nodes){nodes.forEach(n=>this.appendChild(n));}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  setAttribute(key,value){this.attrs[key]=String(value);if(key==='disabled')this.disabled=true;}
  getAttribute(key){return Object.hasOwn(this.attrs,key)?this.attrs[key]:null;}
  removeAttribute(key){delete this.attrs[key];}
  addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);}
  dispatch(type,event={}){return Promise.all((this.handlers[type]??=[]).map(fn=>fn({target:this,key:event.key,preventDefault(){},...event})));}
  click(){if(!this.disabled)return this.dispatch('click');}
  showModal(){this.open=true;}
  close(){this.open=false;return this.dispatch('close');}
  focus(){this.focused=true;}
  select(){}
}
function boot(extra={}){
  const nodes=new Map(),queries=[],timers=new Map(),windowHandlers={};let tid=0;
  const el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const tabs=['overview','coverage','gaps','assets','daily'].map(view=>{const n=el('wh-tab-'+view);n.dataset.view=view;return n;});
  const sync=['tdx','tushare','kg','catalog'].map(name=>new Element('button',{sync:name,label:name}));
  const jobs=['tdx','tushare','kg','catalog'].map(job=>new Element('button',{job}));
  el('warehouse-data').textContent=JSON.stringify({generated_at:'fixture',integrity_at:'fixture',metrics:[],assets:[],...extra});
  el('wh-gap-status').value='all';el('wh-asset-group').value='all';
  el('wh-daily-source').value='tdx';el('wh-daily-market').value='all';el('wh-daily-date').value='2026-10-09';
  const descendants=n=>[n,...n.children.flatMap(descendants)];
  const all=()=>[...nodes.values()].flatMap(descendants);
  const document={getElementById:el,createElement:tag=>new Element(tag),
    querySelectorAll(selector){if(selector==='.wh-tabs [data-view]'||selector==='.wh [data-view]')return tabs;
      if(selector==='[data-sync]')return sync;if(selector==='[data-job]')return jobs;
      const key=selector.match(/^\[data-([\w-]+)\]$/)?.[1]?.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
      return key?all().filter(n=>Object.hasOwn(n.dataset,key)):[];},
    querySelector(selector){return sync.find(b=>selector.includes('"'+b.dataset.sync+'"'));}};
  const context={document,console,Blob,AbortController,Map,Set,JSON,Date,Intl,Math,crypto:require('node:crypto').webcrypto,
    history:{replaceState(){}},location:{hash:'',reload(){}},window:{addEventListener(type,fn){(windowHandlers[type]??=[]).push(fn);}},
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
    setTimeout(fn,delay){const id=++tid;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
    fetch(url,options={}){return new Promise((resolve,reject)=>queries.push({url,options,resolve,reject}));}};
  scripts.forEach(code=>vm.runInNewContext(code,context));
  const reply=(query,body,status=200)=>{assert(query,'未发出预期请求');query.resolve({ok:status>=200&&status<300,status,json:async()=>body});};
  const latest=part=>queries.filter(q=>q.url.includes(part)).at(-1);
  const buttons=()=>descendants(el('wh-daily-rows')).filter(n=>n.tagName==='BUTTON');
  return {el,tabs,sync,queries,timers,reply,latest,buttons,enter:()=>tabs[4].click(),pagehide:()=>windowHandlers.pagehide.forEach(fn=>fn()),
    posts:path=>queries.filter(q=>q.url===path&&q.options.method==='POST')};
}
const idA='a'.repeat(32),idB='b'.repeat(32);
const asset=(name,status='not_updated',enabled=true)=>({asset_id:name,physical_tables:[name],channel:'xtcloud',provider:null,market:'cn',frequency:'D',data_status:status,update_enabled:enabled,targets:[{table:name,target:'2026-10-08',status:status==='waiting_release'?'waiting_release':'due'}],reason:status==='unknown'?'qualification_unknown':null});
const daily=(date='2026-10-09',assets=[asset('daily')],runs=[])=>({date,assets,runs});
const plan=(unsupported=[])=>({plan_id:'p1',expires_at:Date.now()/1000+300,plan:{source:'tdx',market:'all',date:'2026-10-09',plan_digest:'c'.repeat(64),selected_tables:['daily'],targets:[{table:'daily',target:'2026-10-08'}],units:[{unit_id:'u',table:'daily',kind:'date',scope:{date:'2026-10-08'},selection_reason:'user'}],operations:[{table:'daily',operation:'paged_incremental',executable:true,scope:{codes:['A','B']},dependencies:[{table:'trade_cal'}]}],unsupported}});
const run=(id,status='running',changes={})=>({run_id:id,date:'2026-10-09',status,pid:1,process_status:'alive',process_created_at:'identity',heartbeat_at:Date.now()/1000,plan:{source:'tdx',units:[]},progress:{completed_units:1,total_units:2,failed_units:0,unit:'planned_unit'},metrics:{new_rows:null,revised_rows:null,duplicate_rows:null,bytes_written:null},events:[],receipts:[],next_seq:0,has_more:false,...changes});
async function loaded(extra,assets){const h=boot(extra);h.enter();await settle();if(!extra?.daily_read_only)h.reply(h.latest('/api/data/daily?'),daily(undefined,assets));await settle();return h;}
async function previewed(h,body=plan()){h.el('wh-daily-preview').click();await settle();h.reply(h.latest('/api/data/update-plan'),body);await settle();}
const cases=[];
cases.push(['首次只查询状态，切每日后才读目标，键盘保留五视图',async()=>{
  const h=boot();assert.equal(h.queries.length,4);assert(h.queries.every(q=>q.url.startsWith('/api/data/job?')));
  h.tabs[0].dispatch('keydown',{key:'End'});await settle();assert(h.el('wh-tab-daily').focused);assert(h.latest('/api/data/daily?'));
  assert.equal(h.posts('/api/data/update').length,0);assert.equal(h.posts('/api/data/update-plan').length,0);
}]);
cases.push(['日期A迟于B与刷新失败不污染新日期',async()=>{
  const h=boot();h.enter();await settle();const old=h.latest('/api/data/daily?');
  h.el('wh-daily-date').value='2026-10-08';h.el('wh-daily-date').dispatch('change');await settle();
  h.reply(h.latest('/api/data/daily?'),daily('2026-10-08',[asset('B')]));await settle();h.reply(old,daily('2026-10-09',[asset('A')]));await settle();
  assert(h.el('wh-daily-rows').textContent.includes('B'));assert(!h.el('wh-daily-rows').textContent.includes('A'));
  h.el('wh-daily-refresh').click();await settle();h.latest('/api/data/daily?').reject(new Error('不可用'));await settle();
  assert(h.el('wh-daily-rows').textContent.includes('B'));assert(h.el('wh-daily-message').textContent.includes('上次'));
}]);
cases.push(['未知体量、未知资格与来源时点分开，导出完整JSON',async()=>{
  const h=await loaded({},[asset('<img onerror=evil>','unknown',false)]);
  assert(h.el('wh-daily-rows').textContent.includes('未计量'));assert(h.el('wh-daily-rows').textContent.includes('待核验'));
  assert(h.buttons().find(b=>b.textContent==='立即更新').disabled);
  h.el('wh-daily-export').click();await settle();const exported=JSON.parse(h.el('wh-export-text').value);
  assert.equal(exported.date,'2026-10-09');assert.equal(exported.assets[0].asset_id,'<img onerror=evil>');
  assert.equal(h.posts('/api/data/update').length,0);
}]);
cases.push(['冻结依赖与unsupported完整显示，取消不启动',async()=>{
  const h=await loaded();await previewed(h,plan([{table:'minutes',reason:'unsafe_window_replace'}]));
  assert(h.el('wh-daily-plan-body').textContent.includes('trade_cal'));assert(h.el('wh-daily-plan-body').textContent.includes('minutes'));
  assert(h.el('wh-daily-confirm').disabled);h.el('wh-daily-cancel').click();await settle();assert.equal(h.posts('/api/data/update').length,0);
  await previewed(h);h.el('wh-daily-plan-dialog').dispatch('cancel');await settle();assert.equal(h.posts('/api/data/update').length,0);
}]);
cases.push(['批量只冻结到期可更新项，等待/历史/未知不扩大',async()=>{
  const h=await loaded({},[asset('due'),asset('waiting','waiting_release'),asset('unknown','unknown',false),{...asset('history'),update_enabled:false,reason:'historical_unsupported'}]);
  h.el('wh-daily-batch').click();await settle();const body=JSON.parse(h.latest('/api/data/update-plan').options.body);
  assert.deepEqual(body.selected_tables,['due']);assert.equal(body.source,'tdx');assert.equal(body.market,'all');
}]);
cases.push(['POST仅使用已确认计划，重复点击同幂等请求，结束重新读数据状态',async()=>{
  const h=await loaded();await previewed(h);const p=h.posts('/api/data/update-plan')[0];assert.equal(JSON.parse(p.options.body).retry_of,null);
  h.el('wh-daily-confirm').click();h.el('wh-daily-confirm').click();await settle();assert.equal(h.posts('/api/data/update').length,1);
  const request=JSON.parse(h.latest('/api/data/update').options.body);assert.deepEqual(Object.keys(request).sort(),['plan_digest','plan_id','request_id']);assert(request.request_id);
  h.reply(h.latest('/api/data/update'),{run_id:idA,started:true,status:'running'});await settle();
  h.reply(h.latest('/api/data/runs/'),run(idA,'succeeded',{progress:{completed_units:2,total_units:2,failed_units:0,unit:'planned_unit'}}));await settle();
  assert(h.latest('/api/data/daily?'));h.reply(h.latest('/api/data/daily?'),daily(undefined,[asset('daily','unknown',false)]));await settle();
  assert(h.el('wh-daily-rows').textContent.includes('待核验'));assert(!h.el('wh-daily-rows').textContent.includes('已更新'));
}]);
cases.push(['旧run与启动迟于轮询均不能覆盖新run，未知分母不定进度',async()=>{
  const h=await loaded({},[asset('daily')]);await previewed(h);h.el('wh-daily-confirm').click();await settle();const start=h.latest('/api/data/update');
  // 新任务通过显式任务选择打开；启动迟到必须丢弃。
  h.el('wh-daily-run-id').value=idB;h.el('wh-daily-open-run').click();await settle();const b=h.latest('/api/data/runs/');
  h.reply(b,run(idB,'running',{progress:{completed_units:1,total_units:null,failed_units:1,unit:'planned_unit'}}));await settle();
  h.reply(start,{run_id:idA,started:true,status:'running'});await settle();
  assert(h.el('wh-daily-run-title').textContent.includes(idB));assert(!h.el('wh-daily-run-title').textContent.includes(idA));
  assert.equal(h.el('wh-daily-progress').getAttribute('value'),null);assert(h.el('wh-daily-progress-label').textContent.includes('分母'));
  h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();const a=h.latest('/api/data/runs/');
  h.el('wh-daily-run-id').value=idB;h.el('wh-daily-open-run').click();await settle();const b2=h.latest('/api/data/runs/');
  h.reply(b2,run(idB));await settle();h.reply(a,run(idA,'failed'));await settle();assert(h.el('wh-daily-run-title').textContent.includes(idB));
}]);
cases.push(['来源空/网络失败有逐请求时间和范围，事件分页与单元结果导出',async()=>{
  const h=await loaded();h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();
  const receipts=['authorized_empty','connection_error'].map((result_kind,i)=>({receipt_id:'r'+i,run_id:idA,unit_id:'u'+i,table:'daily',provider:'provider',channel:'gateway',source_id:'api',api_name:'daily',request_scope:{code:'A',page:i},requested_at:'2026-10-09T10:00:00+08:00',received_at:'2026-10-09T10:00:01+08:00',source_time:null,result_rows:result_kind==='authorized_empty'?0:null,result_kind,error_kind:result_kind==='connection_error'?'network':null}));
  h.reply(h.latest('/api/data/runs/'),run(idA,'running',{events:[{seq:1,kind:'source_receipt',payload:receipts[0]}],receipts:[],next_seq:1,has_more:true}));await settle();
  assert(h.latest('/api/data/runs/').url.includes('after_seq=1'));
  h.reply(h.latest('/api/data/runs/'),run(idA,'running',{events:[{seq:2,kind:'source_receipt',payload:receipts[1]}],next_seq:2,has_more:false,units:[{plan:{unit_id:'u0',table:'daily'},result:{execution_status:'source_empty',receipt_ids:['r0'],new_rows:null}}]}));await settle();
  assert(h.el('wh-daily-receipts').textContent.includes('来源返回空'));assert(h.el('wh-daily-receipts').textContent.includes('连接失败'));
  assert(h.el('wh-daily-receipts').textContent.includes('10:00:01'));assert(h.el('wh-daily-receipts').textContent.includes('待核验'));
  h.el('wh-daily-export').click();await settle();const exported=JSON.parse(h.el('wh-export-text').value);
  assert.equal(exported.run.events.length,2);assert.equal(exported.run.receipts.length,2);assert.equal(exported.run.units[0].result.receipt_ids[0],'r0');
}]);
cases.push(['源和日期变化取消旧计划，旧资产按钮只进入每日',async()=>{
  const h=await loaded();await previewed(h);h.el('wh-daily-source').value='tushare';h.el('wh-daily-source').dispatch('change');await settle();
  h.el('wh-daily-confirm').click();await settle();assert.equal(h.posts('/api/data/update').length,0);
  h.sync[1].click();await settle();assert.equal(h.el('wh-daily-source').value,'tushare');assert(!h.queries.some(q=>q.url.startsWith('/api/data/sync?')));
}]);
cases.push(['非实时fixture禁启动且不混入真实daily请求',async()=>{
  const h=await loaded({daily_read_only:true,daily_snapshot:daily(undefined,[asset('fixture')])});
  assert(h.el('wh-daily-message').textContent.includes('非实时'));assert(h.el('wh-daily-preview').disabled);assert(h.el('wh-daily-batch').disabled);
  h.el('wh-daily-confirm').click();h.sync[2].click();await settle();assert.equal(h.posts('/api/data/update').length,0);assert(!h.queries.some(q=>q.url.startsWith('/api/data/sync?')));
  assert(!h.queries.some(q=>q.url.startsWith('/api/data/daily?')));
  assert.equal(h.queries.length,0,'只读快照不混入真实旧任务状态');
}]);
cases.push(['不以行数分母伪装计划单元进度',async()=>{
  const h=await loaded();h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();
  h.reply(h.latest('/api/data/runs/'),run(idA,'running',{progress:{completed_units:5,total_units:10,failed_units:0,unit:'rows'}}));await settle();
  assert.equal(h.el('wh-daily-progress').getAttribute('value'),null);
  assert(!h.el('wh-daily-progress-label').textContent.includes('已完成 5 个计划单元'));assert(h.el('wh-daily-progress-label').textContent.includes('单位待核验'));
}]);
cases.push(['回执失败重试保留同一request_id，同日刷新不吞启动回执',async()=>{
  const h=await loaded();await previewed(h);h.el('wh-daily-confirm').click();await settle();
  const first=h.latest('/api/data/update');first.reject(new Error('响应中断'));await settle();
  h.el('wh-daily-confirm').click();await settle();const second=h.latest('/api/data/update');
  assert.notEqual(first,second);assert.equal(JSON.parse(first.options.body).request_id,JSON.parse(second.options.body).request_id);
  h.el('wh-daily-refresh').click();await settle();h.reply(h.latest('/api/data/daily?'),daily());await settle();
  h.reply(second,{run_id:idA,status:'running',started:true});await settle();
  assert(h.el('wh-daily-run-title').textContent.includes(idA));
}]);
cases.push(['精确重试只选原失败或中断unit，不扩成功单元',async()=>{
  const h=await loaded();h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();
  const units=[{plan:{unit_id:'good',table:'daily'},result:{execution_status:'succeeded'}},{plan:{unit_id:'bad',table:'daily'},result:{execution_status:'failed'}},{plan:{unit_id:'stopped',table:'daily'},result:{execution_status:'interrupted'}}];
  h.reply(h.latest('/api/data/runs/'),run(idA,'partial',{retry_supported:true,units}));await settle();
  h.reply(h.latest('/api/data/daily?'),daily());await settle();h.el('wh-daily-retry').click();await settle();
  const body=JSON.parse(h.latest('/api/data/update-plan').options.body);assert.equal(body.retry_of,idA);assert.deepEqual(body.retry_units,['bad','stopped']);
}]);
cases.push(['切换任务等待回执时立即清旧进度计量与回执',async()=>{
  const h=await loaded();h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();
  h.reply(h.latest('/api/data/runs/'),run(idA,'succeeded',{progress:{completed_units:2,total_units:2,failed_units:0,unit:'planned_unit'},metrics:{new_rows:123},receipts:[{receipt_id:'old',table:'旧来源',unit_id:'u',result_kind:'authorized_data'}]}));await settle();
  assert.equal(h.el('wh-daily-progress').getAttribute('value'),'2');
  h.el('wh-daily-run-id').value=idB;h.el('wh-daily-open-run').click();await settle();
  assert.equal(h.el('wh-daily-progress').getAttribute('value'),null);assert(h.el('wh-daily-run-metrics').textContent.includes('未计量'));
  assert(!h.el('wh-daily-receipts').textContent.includes('旧来源'));assert(!h.el('wh-daily-run-metrics').textContent.includes('123'));
}]);
cases.push(['只读快照本地查看任务回执与部分计量且不查询API',async()=>{
  const saved=run(idA,'partial',{metrics:{new_rows:0,revised_rows:null},receipts:[{receipt_id:'snapshot-r',table:'snapshot-table',unit_id:'u',result_kind:'authorized_empty'}],events:[{seq:1,kind:'saved'}],units:[{plan:{unit_id:'u'},result:{execution_status:'source_empty'}}]});
  const h=await loaded({daily_read_only:true,daily_snapshot:daily(undefined,[asset('fixture')],[saved])});
  h.el('wh-daily-runs').children[0].click();await settle();
  assert(h.el('wh-daily-run-title').textContent.includes(idA));assert(h.el('wh-daily-run-state').textContent.includes('非实时'));
  assert(h.el('wh-daily-receipts').textContent.includes('snapshot-table'));assert(h.el('wh-daily-run-metrics').textContent.includes('新增 0'));
  assert(h.el('wh-daily-run-metrics').textContent.includes('修订 未计量'));assert(h.el('wh-daily-events').textContent.includes('source_empty'));
  h.el('wh-daily-poll').click();await settle();assert.equal(h.queries.length,0);assert(h.el('wh-daily-retry').disabled);
}]);
cases.push(['迟到预览计划不能跨日期打开对话框',async()=>{
  const h=await loaded();h.el('wh-daily-preview').click();await settle();const old=h.latest('/api/data/update-plan');
  h.el('wh-daily-date').value='2026-10-08';h.el('wh-daily-date').dispatch('change');await settle();
  h.reply(old,plan());await settle();assert(!h.el('wh-daily-plan-dialog').open);assert(h.el('wh-daily-confirm').disabled);
}]);
cases.push(['同任务轮询乱序保留新进度',async()=>{
  const h=await loaded();h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();const old=h.latest('/api/data/runs/');
  h.el('wh-daily-poll').click();await settle();const fresh=h.latest('/api/data/runs/');
  h.reply(fresh,run(idA,'running',{progress:{completed_units:4,total_units:5,failed_units:0,unit:'planned_unit'}}));await settle();
  h.reply(old,run(idA));await settle();assert.equal(h.el('wh-daily-progress').getAttribute('value'),'4');
}]);
cases.push(['缺少重试能力或切换来源即禁用精确重试',async()=>{
  const h=await loaded();h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();
  const changes={units:[{plan:{unit_id:'bad',table:'daily'},result:{execution_status:'failed'}}]};
  h.reply(h.latest('/api/data/runs/'),run(idA,'partial',changes));await settle();assert(h.el('wh-daily-retry').disabled);
  h.el('wh-daily-poll').click();await settle();h.reply(h.latest('/api/data/runs/'),run(idA,'partial',{...changes,retry_supported:true}));await settle();assert(!h.el('wh-daily-retry').disabled);
  h.el('wh-daily-source').value='tushare';h.el('wh-daily-source').dispatch('change');await settle();assert(h.el('wh-daily-retry').disabled);
}]);
cases.push(['关闭页面后迟到轮询不更新状态或安排后续请求',async()=>{
  const h=await loaded();h.el('wh-daily-run-id').value=idA;h.el('wh-daily-open-run').click();await settle();const pending=h.latest('/api/data/runs/');
  h.pagehide();const title=h.el('wh-daily-run-title').textContent;h.reply(pending,run(idA));await settle();
  assert.equal(h.el('wh-daily-run-title').textContent,title);assert.equal([...h.timers.values()].filter(t=>t.delay!==12000).length,0);
}]);
(async()=>{let failed=0;for(const [name,test]of cases){try{await test();console.log('PASS: '+name);}catch(error){failed++;console.error('FAIL: '+name+'\n'+error.stack);}}if(failed)process.exitCode=1;else console.log('PASS: 完整每日产品脚本 '+cases.length+' 项');})().catch(error=>{console.error(error);process.exitCode=1;});
