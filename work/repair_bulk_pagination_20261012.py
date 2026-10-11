import hashlib,json,sys,time
from pathlib import Path
import requests
sys.path.insert(0,'quant_backtest'); from rps_resonance import _token
s=Path('quant_backtest/data/tushare_staging'); path=s/'nine_dim_source_payload_full_20261012.json'; p=json.loads(path.read_text(encoding='utf8')); codes=sorted(p['source_receipts']['per_code']); token=_token(); start='20240909'; end='20261008'
def fetch(api,limit):
 rows=[]; fields=[]; reqs=[]; off=0
 while True:
  params={'start_date':start,'end_date':end,'offset':off,'limit':limit}; t=time.time();
  r=requests.post('http://api.tushare.pro',json={'api_name':api,'token':token,'params':params,'fields':''},timeout=25); raw=r.content; j=r.json(); d=j.get('data') or {}; part=d.get('items') or []; reqs.append({'api_name':api,'request_params':params,'source_code':j.get('code'),'source_message':j.get('msg') or j.get('message') or '','body_sha256':hashlib.sha256(raw).hexdigest(),'row_count':len(part),'elapsed_s':round(time.time()-t,3),'fields':d.get('fields') or []})
  if j.get('code')!=0: raise RuntimeError(f'{api} source error {j.get("code")} {j.get("msg")}')
  if not fields: fields=d.get('fields') or []
  rows.extend(part); print(api,'offset',off,'rows',len(part),'total',len(rows),flush=True)
  if len(part)<limit: break
  off+=len(part)
 return fields,rows,reqs
for api,limit in [('report_rc',5000),('block_trade',1000)]:
 fields,rows,reqs=fetch(api,limit); idx={f:i for i,f in enumerate(fields)}; grouped={c:[] for c in codes}
 for row in rows:
  c=str(row[idx['ts_code']]) if 'ts_code' in idx else ''
  if c in grouped: grouped[c].append(row)
 for c in codes:
  rec=p['source_receipts']['per_code'][c][api]; rec['fields']=fields; rec['rows']=grouped[c]; rec['row_count']=len(grouped[c]); rec['request_params']={'start_date':start,'end_date':end,'mode':'bulk_date_range'}; rec['raw_response_count']=len(reqs); rec['source_code']=0; rec['err']=0; rec['response_status']='source_response_verified'; rec['coverage_status']='sample_has_data' if grouped[c] else 'valid_empty_response'; rec['body_sha256']=hashlib.sha256(json.dumps({'fields':fields,'rows':grouped[c]},ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
 p['source_receipts'].setdefault('bulk_requests',{})[api]=reqs
 p['summary'][api]={'requests':len(reqs),'source_code_0':len(reqs),'errors':0,'rows_total':len(rows),'nonempty_codes':sum(bool(grouped[c]) for c in codes),'empty_codes':sum(not grouped[c] for c in codes)}
p['limitations']=(p.get('limitations') or [])+['report_rc/block_trade 已按接口分页上限补齐，仍为 staging 来源快照。']
path.write_text(json.dumps(p,ensure_ascii=False),encoding='utf8'); print(json.dumps(p['summary'],ensure_ascii=False))
