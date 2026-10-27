# -*- coding: utf-8 -*-
"""HK 点位误收只读扫描（xq-quality-dev 刀二取证，临时脚本不入仓）。

对全量 xq_status（不只已分类行）找出「裸 5 位 + 点位语境」共现样本：
用当前 RE_HK bare 通道同口径判定该 5 位是否会被收码，再查点位语境词。
零 DB 写入（mode=ro）。
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xq_classify as xc

# 点位语境词（刀二观察项：v1.3 硬排除未覆盖「关口」等语境）
POINT_CTX = re.compile(r"关口|点位|大关|恒指|恒生指数|国企指数|阻力|支撑|突破|跌破|站上|整数")

con = sqlite3.connect("file:%s?mode=ro" % xc.DB_PATH.as_posix(), uri=True, timeout=30)
con.execute("PRAGMA busy_timeout=30000")
rows = con.execute("SELECT status_id, title, desc FROM xq_status").fetchall()
con.close()
print("total rows:", len(rows))

hits = []
for sid, title, desc in rows:
    text = (title or "") + " " + (desc or "")
    if not re.search(r"[0-9]{5}", text):
        continue
    hk_ctx = bool(xc._HK_CONTEXT_RE.search(text))
    for m in re.finditer(r"(?<![0-9.\-])([0-9]{5})(?![0-9\-])", text):
        code = m.group(1)
        # 复刻 bare 通道硬排除：后缀 点/元/块/万/手
        tail = text[m.end():m.end() + 2]
        if re.match(r"\s*[元块万亿手点]", tail):
            continue
        # 复刻 bare 收码口径：有语境 → 收；无语境 → 仅中文紧贴名收
        prev = text[m.start() - 1] if m.start() > 0 else ""
        if hk_ctx:
            captured = True
        elif prev not in xc._ADJ_NOISE and "\u4e00" <= prev <= "\u9fff":
            captured = True
        else:
            captured = False
        ctx_span = text[max(0, m.start() - 30):m.end() + 30]
        point_hit = bool(POINT_CTX.search(ctx_span))
        if captured or point_hit:
            hits.append((sid, code, captured, point_hit, hk_ctx,
                         ctx_span.replace("\n", " ")))

print("candidate 5-digit hits:", len(hits))
cap_point = [h for h in hits if h[2] and h[3]]
print("captured AND point-context:", len(cap_point))
print()
for sid, code, cap, pt, hkctx, ctx in cap_point[:40]:
    print("MISCONF? %s code=%s hk_ctx=%s | %s" % (sid, code, hkctx, ctx))
print()
# 全部 captured 且无点位语境的对照计数
cap_only = [h for h in hits if h[2] and not h[3]]
print("captured without point-context:", len(cap_only))
for sid, code, cap, pt, hkctx, ctx in cap_only[:20]:
    print("captured %s code=%s hk_ctx=%s | %s" % (sid, code, hkctx, ctx))
