import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path('quant_backtest').resolve()))
from history_backfill import pull_one
stage=Path('quant_backtest/data/tushare_staging/explicit_gaps')
items=[pull_one('ts_daily_basic','daily_basic','20260928',stage),pull_one('ts_margin_detail','margin_detail','20261008',stage)]
print(json.dumps([{k:x.get(k) for k in ('table','api_name','status','complete','row_count','unique_keys','duplicate_keys','body_sha256','staged_sha256','reused')} for x in items],ensure_ascii=False))
