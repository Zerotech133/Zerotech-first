import hashlib,json
from pathlib import Path
import pandas as pd
root=Path('quant_backtest/data/tushare_staging/history_3y')
receipts=list(root.rglob('*.receipt.json'))
checks={'receipts':len(receipts),'raw_sha_mismatch':0,'parquet_sha_mismatch':0,'invalid':0,'duplicate_keys':0,'rows':0}
for rp in receipts:
    obj=json.loads(rp.read_text(encoding='utf-8'))
    raw=Path(obj['raw_path']); pq=Path(obj['staged_path'])
    if hashlib.sha256(raw.read_bytes()).hexdigest()!=obj.get('raw_sha256') or hashlib.sha256(raw.read_bytes()).hexdigest()!=obj.get('body_sha256'): checks['raw_sha_mismatch']+=1
    if hashlib.sha256(pq.read_bytes()).hexdigest()!=obj.get('staged_sha256'): checks['parquet_sha_mismatch']+=1
    frame=pd.read_parquet(pq)
    checks['rows']+=len(frame)
    keys=['ts_code','trade_date']
    if frame.duplicated(keys).any(): checks['duplicate_keys']+=int(frame.duplicated(keys).sum())
    if obj.get('complete') is not True or obj.get('status')!='candidate_ready': checks['invalid']+=1
Path('work/three_year_history_integrity.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(checks,ensure_ascii=False))
