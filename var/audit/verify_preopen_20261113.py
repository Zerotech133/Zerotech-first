# -*- coding: utf-8 -*-
"""11-13 盘前反哺验收表：把清单第 5 节的 10 个检查点变成可复跑脚本。

只读，不写任何生产文件。日期字面量一律码点构造。
"""
import io
import json
import os
import re

DELIV = r"C:\项目\deliverables"
D = lambda *c: "".join(chr(c) for c in c)
DAY = D(50, 48, 50, 54, 49, 49, 49, 50)      # 20261112
EXEC = D(50, 48, 50, 54, 49, 49, 49, 51)     # 20261113

run = json.load(io.open(os.path.join(DELIV, "preopen_run_20261113.json"),
                        encoding="utf-8"))
pick = json.load(io.open(
    os.path.join(DELIV, "preopen_pick15_v2_adj_check_20261112.json"),
    encoding="utf-8"))
html = io.open(os.path.join(DELIV, "preopen_report_20261113.html"),
               encoding="utf-8").read()
plain = re.sub(r"<[^>]+>", "", html)

au = run.get("audit") or {}
pv = run.get("pick_provenance") or {}
tl = pick.get("toplist_filter") or {}

rows = [
    ("1 校对不阻断", au.get("verdict") in ("ok", "warn") and not au.get("problems"),
     "verdict=%s problems=%d" % (au.get("verdict"), len(au.get("problems") or []))),
    ("2 龙虎榜以选票为准", any("V1b 生效" in w for w in au.get("warns") or []),
     "status=%s source=%s" % (tl.get("status"), tl.get("source"))),
    ("3 涨停历史日不误报", "历史日" in html and not any(
        "自算比官方多" in w for w in au.get("warns") or []),
     "交叉验证已标历史日"),
    ("4 复用校验可用", pv.get("reused") is False and pv.get("data_day") == DAY,
     "data_day=%s chain_policy_v=%s" % (pv.get("data_day"), pv.get("chain_policy_v"))),
    ("5 精排日推进", pick.get("refine_date") == DAY,
     "refine_date=%s" % pick.get("refine_date")),
    ("6 口径版本一致", pick.get("chain_policy_v") == 3 and pick.get("score_mode") == "v5",
     "v%s / %s" % (pick.get("chain_policy_v"), pick.get("score_mode"))),
    ("7 池子滞后可见", pick.get("package_lag_days") is not None,
     "package_asof=%s lag=%s" % (pick.get("package_asof"),
                                 pick.get("package_lag_days"))),
    ("8 H5 结论层干净", not re.search(r"<h1[^>]*>[^<]*[A-Za-z]{3,}", html),
     "首屏未见英文代号"),
    ("9 抽屉口径正确", all(k in html for k in (
        "评分权重", "本票取值", "动量名次", "量能倍数", "权重置 0"))
     and not any(k in html for k in ("四项加权", "无区分度已剔除", "<span>量比 <b>")),
     "权重/取值/量能倍数分列"),
    ("10 名单可比", pick.get("score_weights") == {
        "mom_rank": 0.1, "trend": 0.1, "vol_h": 0.45,
        "liq": 0.0, "confirm": 0.35} and len(pick.get("top15_adj") or []) == 15,
     "权重未改 / 15 只"),
    ("附 行业断供降级", "本节略过" in html, "非必需块不再干掉整份报告"),
    ("附 免责可转发", "可全文转发" in plain, "全文转发、请勿断章"),
    ("附 封板口径可见", "封板且涨幅超 9%" in plain, "与收盘触板口径分开说明"),
]

print("11-13 盘前反哺验收（数据日 %s / 执行日 %s）" % (DAY, EXEC))
print("=" .ljust(64, "="))
for name, ok, note in rows:
    print("  %s  %-18s %s" % ("PASS" if ok else "FAIL", name, note))
bad = [n for n, ok, _ in rows if not ok]
print("=" .ljust(64, "="))
print("结果：%d/%d 通过%s" % (len(rows) - len(bad), len(rows),
                            "" if not bad else "，未过：" + ",".join(bad)))
raise SystemExit(0 if not bad else 2)
