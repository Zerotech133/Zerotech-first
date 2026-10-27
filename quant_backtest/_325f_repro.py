# -*- coding: utf-8 -*-
"""复现回补失败调用，打印完整错误。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tushare_sync as T  # noqa: E402

print("TOKEN tail:", T.TOKEN[-4:])
p = {
    "ts_code": "000795.SZ",
    "freq": "1min",
    "start_date": "20260701 09:00:00",
    "end_date": "20260731 15:00:00",
    "offset": 0,
}
try:
    d = T.ts_call("stk_mins", p)
    items = d.get("items") or []
    print("OK rows:", len(items))
except Exception as e:
    print("FULL ERROR:")
    print(str(e))
