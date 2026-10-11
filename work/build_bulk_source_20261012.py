import copy, hashlib, json, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
sys.path.insert(0,'quant_backtest')
from rps_resonance import _token
stage=Path('quant_backtest/data/tushare_staging'); end='20261008'; start='20240909'
gap=json.loads((stage/'nine_dim_historical_source_gap_20261012_batch003.json').read_text(encoding='utf8'))
codes=sorted(set(gap['covered_codes'])|set(gap['missing_codes']))
TOKEN=_token()
if not TOKEN: raise RuntimeError('token_missing')

bulk={api:{'fields':[],'rows':[],'requests':[],'errors':[]} for api in ('forecast','report_rc','block_trade')}

def request(api, params):
    body={'api_name':api,'token':TOKEN,'params':params,'fields':''}; started=time.time(); last=''
    for attempt in range(2):
        try:
            resp=requests.post('http://api.tushare.pro',json=body,timeout=25)
            raw=resp.content; digest=hashlib.sha256(raw).hexdigest(); j=resp.json(); data=j.get('data') or {}
            rec={'api_name':api,'request_params':params,'source_code':j.get('code'),'source_message':j.get('msg') or j.get('message') or '', 'body_sha256':digest,'row_count':len(data.get('items') or []),'elapsed_s':round(time.time()-started,3)}
            if j.get('code')==0:
                rec['fields']=data.get('fields') or []; rec['rows']=data.get('items') or []
                return rec
            last=rec['source_message'][:200]
        except Exception as exc: last=str(exc)[:200]
        if attempt==0: time.sleep(1.5)
    return {'api_name':api,'request_params':params,'source_code':None,'source_message':last or 'request_failed','body_sha256':None,'fields':[],'rows':[],'row_count':0,'elapsed_s':round(time.time()-started,3)}

def fetch_range(api, params):
    offset=0; total=0
    while True:
        p=dict(params,offset=offset,limit=6000); rec=request(api,p); bulk[api]['requests'].append({k:v for k,v in rec.items() if k!='rows'}); bulk[api]['requests'][-1]['rows']=rec['rows']
        if rec['source_code']!=0:
            bulk[api]['errors'].append(rec); break
        if not bulk[api]['fields']: bulk[api]['fields']=rec['fields']
        rows=rec['rows']; bulk[api]['rows'].extend(rows); total+=len(rows)
        if len(rows)<6000: break
        offset+=len(rows)
    print(api,'rows',total,'requests',len(bulk[api]['requests']),flush=True)

fetch_range('report_rc',{'start_date':start,'end_date':end})
fetch_range('block_trade',{'start_date':start,'end_date':end})

cur=datetime.strptime(start,'%Y%m%d'); stop=datetime.strptime(end,'%Y%m%d'); n=0
while cur<=stop:
    ds=cur.strftime('%Y%m%d'); rec=request('forecast',{'ann_date':ds,'limit':6000}); bulk['forecast']['requests'].append({k:v for k,v in rec.items() if k!='rows'}); bulk['forecast']['requests'][-1]['rows']=rec['rows']
    if rec['source_code']!=0: bulk['forecast']['errors'].append(rec)
    elif not bulk['forecast']['fields']: bulk['forecast']['fields']=rec['fields']
    bulk['forecast']['rows'].extend(rec['rows']); n+=1
    if n%25==0: print('forecast_dates',n, 'rows',len(bulk['forecast']['rows']),flush=True)
    cur+=timedelta(days=1)
print('forecast rows',len(bulk['forecast']['rows']),'requests',len(bulk['forecast']['requests']),'errors',len(bulk['forecast']['errors']),flush=True)

api_fields={'forecast':bulk['forecast']['fields'],'report_rc':bulk['report_rc']['fields'],'block_trade':bulk['block_trade']['fields']}
by_code={c:{} for c in codes}
for api in ('forecast','report_rc','block_trade'):
    idx={f:i for i,f in enumerate(api_fields[api])}; grouped={c:[] for c in codes}
    code_i=idx.get('ts_code')
    if code_i is not None:
        for row in bulk[api]['rows']:
            c=str(row[code_i]);
            if c in grouped: grouped[c].append(row)
    params={'start_date':start,'end_date':end,'mode':'bulk_date_range'}
    if api=='forecast': params={'start_date':start,'end_date':end,'mode':'bulk_ann_date_daily'}
    ok=(not bulk[api]['errors'])
    for c in codes:
        rows=grouped[c]; by_code[c][api]={'api_name':api,'ts_code':c,'request_params':params,'source_code':0 if ok else None,'source_message':'' if ok else 'bulk_request_error','body_sha256':hashlib.sha256(json.dumps({'fields':api_fields[api],'rows':rows},ensure_ascii=False,separators=(',',':')).encode()).hexdigest() if ok else None,'fields':api_fields[api],'rows':rows,'row_count':len(rows),'response_status':'source_response_verified' if ok else 'source_error','coverage_status':'sample_has_data' if rows else ('valid_empty_response' if ok else 'unknown'),'raw_response_count':len(bulk[api]['requests'])}
summary={}
for api in ('forecast','report_rc','block_trade'):
 rows=bulk[api]['rows']; summary[api]={'requests':len(bulk[api]['requests']),'source_code_0':sum(r.get('source_code')==0 for r in bulk[api]['requests']),'errors':len(bulk[api]['errors']),'rows_total':len(rows),'nonempty_codes':sum(bool(by_code[c][api]['rows']) for c in codes),'empty_codes':sum(not by_code[c][api]['rows'] for c in codes)}
out={'schema_version':1,'snapshot_type':'nine_dim_source_payload','captured_at_utc':datetime.now(timezone.utc).isoformat(),'asof_trade_date':end,'candidate_count':len(codes),'global_activation':False,'production_write':False,'score_rerun':False,'source_endpoint':'http://api.tushare.pro','summary':summary,'source_receipts':{'per_code':by_code,'bulk_requests':{api:bulk[api]['requests'] for api in ('forecast','report_rc','block_trade')}},'limitations':['bulk snapshot is staging only; it proves source receipts and preserves rows for as-of filtering, but does not authorize production write or complete fina/margin dependencies.']}
out_path=stage/'nine_dim_source_payload_full_20261012.json'; out_path.write_text(json.dumps(out,ensure_ascii=False),encoding='utf8')
print('WROTE',out_path,'codes',len(codes),'bytes',out_path.stat().st_size,flush=True); print(json.dumps(summary,ensure_ascii=False))
