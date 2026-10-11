import copy,hashlib,json,sys,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests
sys.path.insert(0,'quant_backtest'); from rps_resonance import _token
s=Path('quant_backtest/data/tushare_staging'); gap=json.loads((s/'nine_dim_historical_source_gap_20261012_batch003.json').read_text(encoding='utf8')); codes=sorted(set(gap['covered_codes'])|set(gap['missing_codes'])); start='20240909'; end='20261009'; token=_token()
def call(api,params):
 body={'api_name':api,'token':token,'params':params,'fields':''}; t=time.time();
 for a in range(2):
  try:
   r=requests.post('http://api.tushare.pro',json=body,timeout=25); raw=r.content; j=r.json(); d=j.get('data') or {}
   rec={'api_name':api,'request_params':params,'source_code':j.get('code'),'source_message':j.get('msg') or j.get('message') or '','body_sha256':hashlib.sha256(raw).hexdigest(),'fields':d.get('fields') or [],'rows':d.get('items') or [],'row_count':len(d.get('items') or []),'elapsed_s':round(time.time()-t,3)}
   if j.get('code')==0:return rec
   err=rec['source_message'][:200]
  except Exception as e: err=str(e)[:200]
  if a==0: time.sleep(1.5)
 raise RuntimeError(f'{api} failed: {err}')
def chunks(start,end,days=60):
 a=datetime.strptime(start,'%Y%m%d'); z=datetime.strptime(end,'%Y%m%d')
 while a<=z:
  b=min(a+timedelta(days=days-1),z); yield a.strftime('%Y%m%d'),b.strftime('%Y%m%d'); a=b+timedelta(days=1)
def fetch_range(api,params,limit=5000):
 rows=[]; fields=[]; reqs=[]
 for st,en in chunks(params['start_date'],params['end_date']):
  off=0
  while True:
   rec=call(api,dict(params,start_date=st,end_date=en,offset=off,limit=limit)); reqs.append({k:v for k,v in rec.items() if k!='rows'}); reqs[-1]['rows']=rec['rows']; fields=fields or rec['fields']; rows.extend(rec['rows']); print(api,st,en,'offset',off,'rows',rec['row_count'],flush=True)
   if rec['row_count']<limit:break
   off+=rec['row_count']
 return fields,rows,reqs
# report_rc chunked date ranges
rf,rr,rq=fetch_range('report_rc',{'start_date':start,'end_date':end},5000)
# block_trade only the actual replay anchors, avoiding irrelevant dates
bf=[]; br=[]; bq=[]
for i,td in enumerate([a for a in gap['anchors'] if a<=end],1):
 rec=call('block_trade',{'trade_date':td,'limit':1000}); bf=bf or rec['fields']; br.extend(rec['rows']); bq.append({k:v for k,v in rec.items() if k!='rows'}); bq[-1]['rows']=rec['rows']; print('block_trade',td,'rows',rec['row_count'],flush=True)
# forecast by announcement date
ff=[]; fr=[]; fq=[]; n=0
for st,en in chunks(start,end,1):
 rec=call('forecast',{'ann_date':st,'limit':6000}); ff=ff or rec['fields']; fr.extend(rec['rows']); fq.append({k:v for k,v in rec.items() if k!='rows'}); fq[-1]['rows']=rec['rows']; n+=1
 if n%50==0: print('forecast_dates',n,'rows',len(fr),flush=True)
print('forecast total',len(fr),'requests',len(fq),flush=True)
# group per candidate code
by={c:{} for c in codes}
def group(api,fields,rows,params,requests):
 idx={f:i for i,f in enumerate(fields)}; g={c:[] for c in codes}
 for row in rows:
  if 'ts_code' in idx and str(row[idx['ts_code']]) in g:g[str(row[idx['ts_code']])].append(row)
 ok=all(r.get('source_code')==0 for r in requests)
 for c in codes:
  rs=g[c]; by[c][api]={'api_name':api,'ts_code':c,'request_params':params,'source_code':0 if ok else None,'source_message':'' if ok else 'bulk_request_error','body_sha256':hashlib.sha256(json.dumps({'fields':fields,'rows':rs},ensure_ascii=False,separators=(',',':')).encode()).hexdigest() if ok else None,'fields':fields,'rows':rs,'row_count':len(rs),'response_status':'source_response_verified' if ok else 'source_error','coverage_status':'sample_has_data' if rs else ('valid_empty_response' if ok else 'unknown'),'raw_response_count':len(requests)}
group('forecast',ff,fr,{'start_date':start,'end_date':end,'mode':'bulk_ann_date_daily'},fq)
group('report_rc',rf,rr,{'start_date':start,'end_date':end,'mode':'bulk_date_range_chunked'},rq)
group('block_trade',bf,br,{'start_date':start,'end_date':end,'mode':'bulk_anchor_dates','anchor_dates':[a for a in gap['anchors'] if a<=end]},bq)
summary={}
for api,reqs,rows in [('forecast',fq,fr),('report_rc',rq,rr),('block_trade',bq,br)]:summary[api]={'requests':len(reqs),'source_code_0':sum(r.get('source_code')==0 for r in reqs),'errors':sum(r.get('source_code')!=0 for r in reqs),'rows_total':len(rows),'nonempty_codes':sum(bool(by[c][api]['rows']) for c in codes),'empty_codes':sum(not by[c][api]['rows'] for c in codes)}
out={'schema_version':1,'snapshot_type':'nine_dim_source_payload','captured_at_utc':datetime.now(timezone.utc).isoformat(),'asof_trade_date':end,'candidate_count':len(codes),'global_activation':False,'production_write':False,'score_rerun':False,'source_endpoint':'http://api.tushare.pro','summary':summary,'source_receipts':{'per_code':by,'bulk_requests':{'forecast':fq,'report_rc':rq,'block_trade':bq}},'limitations':['bulk snapshot is staging only; source receipts do not authorize production write or complete fina/margin dependencies.']}
outp=s/'nine_dim_source_payload_full_20261012.json'; outp.write_text(json.dumps(out,ensure_ascii=False),encoding='utf8'); print('WROTE',outp,'bytes',outp.stat().st_size); print(json.dumps(summary,ensure_ascii=False))
