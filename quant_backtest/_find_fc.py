# -*- coding: utf-8 -*-
"""找近 30 天 follow_confirm / lure_warn 样本供 --ach 验收。"""

import pandas as pd

df = pd.read_csv(r"data\semantica\qhm_calibration.csv")
m = df.d0 >= 20260829
fc = df[m & (df.post10 > 0) & (df.post10 <= 1e8) & (df.pre10 <= 0)]
lw = df[m & (df.post10 > 0) & (df.pre10 > 0)]
print("近30天 follow_confirm %d 条：" % len(fc))
print(fc[["tc", "d0", "post10", "ret5"]].head(5).to_string(index=False))
print("近30天 lure_warn %d 条：" % len(lw))
print(lw[["tc", "d0", "post10", "pre10", "ret5"]].head(5).to_string(index=False))
