# -*- coding: utf-8 -*-
"""轻量股票血缘查询脚本（供 Bot subprocess 调用）。
用法：python kg_stock_query.py 605100.SH [days]
stdout = JSON，stdout/stderr 分离。
"""

import sys
import os
import json

sys.path.insert(0, r"C:\aios\quant_backtest")
os.environ.setdefault("KG_DSN", "bolt://127.0.0.1:7687")

import kg_portal

code = sys.argv[1] if len(sys.argv) > 1 else ""
days = int(sys.argv[2]) if len(sys.argv) > 2 else 90

if not code:
    json.dump({"ok": False, "error": "code required"}, sys.stdout, ensure_ascii=False)
    sys.exit(1)

try:
    r = kg_portal.stock(code, days=days)
    # 剥离 cyq/pandas 依赖字段（kg_portal.stock 最后 try-except 吞了）
    # 但 basic/similar/hotmoney/drive/holders/structures/candles/limits/topgaps/vol_events 都有
    json.dump(r, sys.stdout, ensure_ascii=False, default=str)
except Exception as e:
    import traceback

    json.dump({"ok": False, "error": str(e)}, sys.stdout, ensure_ascii=False)
    traceback.print_exc(file=sys.stderr)
    sys.exit(2)
