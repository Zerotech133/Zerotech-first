// 在隔离浏览器边界中执行产品脚本，验证异步回执乱序与导出内容。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
const script = source.split('_JS = r"""')[1].split('"""')[0].replace('<script>', '').replace('</script>', '');
class Element {
  constructor(dataset={}) { this.dataset=dataset; this.value=''; this.textContent=''; this.disabled=false; this.handlers={}; }
  addEventListener(name, action) { this.handlers[name]=action; }
  click() { return this.handlers.click?.(); }
  setAttribute() {}
  showModal() { this.open=true; }
  close() { this.open=false; }
  focus() {}
  select() {}
}
const nodes=new Map(), queries=[], timers=new Map();
let timerId=0;
const element=id => { if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id); };
const syncButtons=['tdx','tushare','kg','catalog'].map(sync => new Element({sync,label:sync}));
const statusButtons=['tdx','tushare','kg','catalog'].map(job => new Element({job}));
element('warehouse-data').textContent=JSON.stringify({generated_at:'fixture',integrity_at:'checked',metrics:[
  {name:'=formula',label:'测试',status:'FAIL',status_label:'异常',value:3,unit:'键',policy:'',details:{missing:['000001.SZ','000002.SZ','000003.SZ']}}
],assets:[]});
element('wh-gap-status').value='all'; element('wh-asset-group').value='all';
const document={getElementById:element,createElement:()=>new Element(),
  querySelectorAll(selector) { if(selector==='[data-sync]')return syncButtons; if(selector==='[data-job]')return statusButtons; return []; },
  querySelector(selector) { return syncButtons.find(b=>selector.includes('"'+b.dataset.sync+'"')); }};
const context={document,console,Blob,AbortController,Map,Set,JSON,history:{replaceState(){}},location:{hash:'',reload(){}},
  window:{addEventListener(){}},confirm:()=>true,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},
  setTimeout(fn, delay) { const id=++timerId;timers.set(id,{fn,delay});return id; },clearTimeout(id) {timers.delete(id);},
  fetch(url) { return new Promise(resolve=>queries.push({url,resolve})); }};
vm.runInNewContext(script, context);
const settle=async()=> { for(let n=0;n<12;n++) await Promise.resolve(); };
const reply=(query,body,status=200)=>query.resolve({ok:status===200,status,json:async()=>body});
(async()=>{
  assert.equal(queries.length,4);
  assert(queries.every(q=>q.url.startsWith('/api/data/job?')), '加载看板不得启动同步');
  const oldStatus=queries.find(q=>q.url.endsWith('kg'));
  const click=syncButtons[2].click();
  assert.equal(queries.length,4,'页面内确认前不得触发同步');
  await element('wh-sync-confirm').click(); await settle();
  const start=queries.find(q=>q.url.startsWith('/api/data/sync?'));
  assert(start);
  reply(start,{started:true}); await settle();
  reply(oldStatus,{running:false,exit:null}); await settle();
  assert.equal(syncButtons[2].disabled,true,'旧查询不得把刚启动的任务按钮恢复');
  const fresh=queries.filter(q=>q.url==='/api/data/job?src=kg').at(-1);
  assert.notEqual(fresh,oldStatus,'启动成功后必须重新查询最新任务状态');
  reply(fresh,{running:true,exit:null,tail:'真实脚本隔离运行'}); await click; await settle();
  assert.equal(element('wh-job-kg').textContent,'运行中');
  assert.equal(syncButtons[2].disabled,true);
  await element('wh-export-gaps').click();
  const exported=JSON.parse(element('wh-export-text').value);
  assert.equal(exported.data[0].details.missing.length,3);
  assert.equal(exported.integrity_at,'checked');
  await element('wh-export-csv').click();
  assert(element('wh-export-text').value.includes('"\'=formula"'), 'CSV文字单元格必须防止公式执行');
  console.log('PASS: 加载只查状态、旧回执乱序、启动后重新查询、完整JSON与CSV公式保护');
})().catch(error=>{console.error(error);process.exitCode=1;});
