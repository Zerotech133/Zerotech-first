import json
from pathlib import Path
from collections import Counter
root=Path('quant_backtest/data/tushare_staging')
tables=['ts_daily_basic','ts_margin_detail']
by={}
for p in root.rglob('*.receipt.json'):
    if not any(p.name.startswith(t+'_') for t in tables): continue
    try: obj=json.loads(p.read_text(encoding='utf-8'))
    except Exception: continue
    d=str(obj.get('request_params',{}).get('trade_date',''))
    t=obj.get('table') or p.name.split('_202',1)[0]
    if len(d)==8: by[(t,d)]=(p,obj)
dates=[]
import sys
sys.path.insert(0,str(Path('quant_backtest').resolve()))
from three_year_history import load_trade_dates
for d in load_trade_dates(Path('quant_backtest/data/tushare/trade_cal'),'20230101','20261009'): dates.append(d)
missing=[]; ready=0; raw_missing=0; statuses=Counter()
for t in tables:
  for d in dates:
    pair=by.get((t,d))
    if not pair: missing.append(f'{t}:{d}'); continue
    p,obj=pair; statuses[obj.get('status','missing')]+=1
    pq=Path(obj.get('staged_path','')) if obj.get('staged_path') else p.with_name(p.name.replace('.receipt.json','.parquet'))
    raw=Path(obj.get('raw_path','')) if obj.get('raw_path') else p.with_name(p.name.replace('.receipt.json','.json'))
    if obj.get('complete') is True and obj.get('status')=='candidate_ready' and pq.exists():
      ready+=1
      if not raw.exists(): raw_missing+=1
    else: missing.append(f'{t}:{d}')
report={'date_count':len(dates),'expected_units':len(dates)*len(tables),'parquet_ready':ready,'raw_missing_among_ready':raw_missing,'missing_or_invalid_count':len(missing),'missing_or_invalid_sample':missing[:100],'status_counts':dict(statuses),'production_write':False,'global_activation':False}
Path('work/k162_existing_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
