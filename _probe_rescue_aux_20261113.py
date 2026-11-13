# -*- coding: utf-8 -*-
"""只读探针：用今天台账里 proj_data（aux 链逐表巡检）这封真信喂云端模型，
看它对"新证据类型"能否给出可用方案。**不投信、不写台账、不改任何产物。**

目的：上一轮验收只覆盖 data/invest 两类证据（源新鲜度 + 共识池时效）。
aux_chain 的 incomplete 是另一种病因（逐表巡检 1/7 未达标），
若模型在这里胡编，说明 SYSTEM_PROMPT 的证据覆盖面不够，07:00 链路仍不可用。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools" / "preopen"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agent_platform"))
sys.stdout.reconfigure(encoding="utf-8")

import daily_check_rescue as R  # noqa: E402

TARGET = "20261112"
rec_path = ROOT / "var" / "preopen" / "daily_check" / ("check_%s.json" % TARGET)
rec = json.loads(rec_path.read_text(encoding="utf-8"))

runs = [x for x in rec.get("runs") or [] if not x.get("dry")]
if not runs:
    print("FAIL 台账里没有真实运行记录")
    raise SystemExit(2)
run = runs[-1]

# 注意：check_receipts 读的是 rec["letters"]，必须传 run（不是整份台账 doc）。
# rescue.run() 里是 rec = same_day[-1]，同层级。
items = R.check_receipts(run)
print("待升级 %d 项：%s" % (len(items), [i["dept"] for i in items]))
print()

target_item = None
for it in items:
    if it["dept"] == "proj_data":
        target_item = it
        break
if target_item is None:
    print("FAIL 未找到 proj_data 项")
    raise SystemExit(2)

meta = {
    "target": TARGET,
    "exec_day": run.get("exec_day"),
    "sent_at": run.get("checked_at"),
    "left_min": 0,
}
# rescue_one 从 rec["letters"] 按 dept 取正文（item 本身不带 letter）
src = None
for L in run.get("letters") or []:
    if L.get("dept") == "proj_data":
        src = L
        break
print("=" * 74)
print("喂给模型的证据（proj_data 的信正文）：")
print(((src or {}).get("body") or (src or {}).get("body_preview") or "<无正文>")[:1500])
print("=" * 74)
print()

rs = R.rescue_one(target_item, run, model="deepseek-flash", timeout=180)
print("ok        :", rs.get("ok"))
print("error     :", rs.get("error"))
print("finish    :", rs.get("finish_reason"))
print("usage     :", json.dumps(rs.get("usage") or {}, ensure_ascii=False))
print("flags     :", rs.get("flags"))
print()

p = rs.get("parsed") or {}
if p:
    print("likely_cause :", p.get("likely_cause"))
    print("confidence   :", p.get("confidence"))
    print("diagnosis    :", p.get("diagnosis"))
    print("why          :", p.get("why"))
    print("deadline     :", p.get("deadline_advice"))
    print()
    print("actions:")
    for a in rs.get("actions") or []:
        print("  %s. %s" % (a.get("step"), a.get("cmd")))
        print("     expect=%s" % a.get("expect"))
        print("     if_abnormal=%s" % a.get("if_abnormal"))
        bad = (a.get("path_exists") or {})
        miss = [k for k, v in bad.items() if not v]
        if a.get("banned_hits") or a.get("unknown_flags") or miss:
            print("     ⚠ banned=%s unknown_flags=%s missing=%s"
                  % (a.get("banned_hits"), a.get("unknown_flags"), miss))
    print()
    print("needs_human :", json.dumps(p.get("needs_human") or [], ensure_ascii=False))
    print("unknowns    :", json.dumps(p.get("unknowns") or [], ensure_ascii=False))

print()
print("=== 渲染出的信（不发送）===")
title, body = R.render_letter(target_item, rs, meta)
print(title)
print(body)
