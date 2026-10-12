import json
from pathlib import Path
import pandas as pd
out={}
for name in ('tdx','tushare'):
 root=Path('quant_backtest/data')/name
 result={'tables':0,'read_ok':0,'read_error':[],'empty_files':0,'rows_sampled':0}
 for d in sorted(root.iterdir()):
  if not d.is_dir() or d.name.startswith('_'): continue
  files=sorted(d.glob('*.parquet'))
  if not files: continue
  result['tables']+=1
  p=files[0]
  try:
   df=pd.read_parquet(p)
   result['read_ok']+=1; result['rows_sampled']+=len(df)
   if df.empty: result['empty_files']+=1
  except Exception as exc: result['read_error'].append({'table':d.name,'file':p.name,'error':str(exc)[:200]})
 out[name]=result
Path('work/all_sources_readability_20261012.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False))
