# -*- coding: utf-8 -*-
"""T+2 开盘决策卡生成器（2026-11-11 想法 3，任务 #104）。

用当晚 pool_state 的结构价位（close/zg/zd/real_hi/real_lo）+ 老田复盘的
T+2 三条策略，逐票产出量化价位卡：清仓/减仓/保留条件全部落到价位上，
明早 9:15 集合竞价直接对着核。

规则（全部来自既有纪律与复盘三条，非新发明）：
  · 电力（复盘①）：「明天开盘不反弹就清掉」→ 开盘价 < 昨收 ×0.995 即清；
    反弹但 < zg 仍属中枢上方悬空，减半。
  · 小盘（复盘②）：只留「午后为正 + 收盘位置>60%」四只；其余冲高回落票
    开盘低于昨收 ×0.99 即减，跌破 real_lo 前低即清。
  · 红利银行（复盘①延伸）：不再留防守，开盘 < 昨收 ×0.998 减半，
    收盘跌破 zd（中枢下沿）次日清。
  · 结构位规则（贴 zg 纪律）：所有持仓距 zg >+5% 的，开盘冲 real_hi 减半
    （中枢上方高位，纪律不追不恋）。
输出：h5_daily/portfolio_card.html（暗色单页，红涨绿跌）。
"""
import glob
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")


def latest_pool():
    fs = sorted(glob.glob(os.path.join(REPORTS, "pool_state_2*.json")))
    return json.load(open(fs[-1], encoding="utf-8"))


# 复盘点名的三组操作对象（T+2 三条，2026-11-11 盘后复盘原文映射）
PLAN = {
    # 电力：开盘不反弹就清（复盘①）
    "电力清仓线": {"600795.SH": 0.995, "601985.SH": 0.995,
                   "600011.SH": 0.995, "600674.SH": 0.995},
    # 小盘保留名单（复盘②：午后为正+位置>60%）—— 海天/华依/德马/海油
    # 这四只在系统池里没有（海天瑞声688642 在池外；海油/德马按复盘名单）
    "小盘保留": ["688642.SH", "300853.SH", "688360.SH", "600938.SH"],
    # 银行：不留防守（复盘①延伸）
    "银行减半线": {c: 0.998 for c in
                  ("601398.SH", "601939.SH", "601988.SH", "601328.SH",
                   "601818.SH", "601009.SH", "601077.SH")},
}
PORT = os.path.join(HERE, "data", "external_portfolio.json")

UP = "#f04a5a"    # 红涨
DN = "#2fbf71"    # 绿跌
BG = "#0d1117"
FG = "#c9d5e3"


def fmt(v, pct=False):
    if v is None:
        return "—"
    return ("%.2f%%" % v) if pct else ("%.2f" % v)


def build_rows():
    pool = latest_pool()
    idx = {}
    for tier, rows in pool["buckets"].items():
        for r in rows:
            idx[r["code"]] = r
    pf = json.load(open(PORT, encoding="utf-8"))
    out = []
    qing = PLAN["电力清仓线"]
    bank = PLAN["银行减半线"]
    keep = set(PLAN["小盘保留"])
    for h in pf["holdings"]:
        c, nm, grp = h["code"], h["name"], h["group"]
        r = idx.get(c) or {}
        close = r.get("close")
        zg, zd = r.get("zg"), r.get("zd")
        hi, lo = r.get("real_hi"), r.get("real_lo")
        act, line1, line2 = "持有/观察", "—", "—"
        if c in qing and close:
            p = round(close * qing[c], 2)
            act = "开盘<%s 即清" % p
            line1 = "反弹>zg %s 仍悬空 → 减半" % fmt(zg)
        elif c in bank and close:
            p = round(close * bank[c], 2)
            act = "开盘<%s 减半" % p
            line1 = "收盘破 zd %s → 次日清" % fmt(zd)
        elif grp == "小盘":
            if c in keep:
                act = "保留（复盘白名单）"
                line1 = "破昨低 %s 减半" % fmt(lo)
            elif close:
                act = "开盘<%s 减" % round(close * 0.99, 2)
                line1 = "破 real_lo %s 清" % fmt(lo)
        # 结构位规则兜底：距 zg >+5% 高位票
        dz = r.get("dist_zg")
        if dz is not None and dz > 0.05 and hi:
            line2 = "冲 real_hi %s 减半（高位纪律）" % fmt(hi)
        out.append({"code": c, "name": nm, "group": grp,
                    "tier": r.get("bucket") or "not_in_pool",
                    "close": close, "zg": zg, "zd": zd,
                    "hi": hi, "lo": lo, "dist_zg": dz,
                    "act": act, "l1": line1, "l2": line2,
                    "pct": r.get("pct")})
    return out


def render(rows):
    trs = []
    for r in rows:
        pct = r["pct"]
        color = UP if (pct or 0) > 0 else (DN if (pct or 0) < 0 else FG)
        dz = r["dist_zg"]
        dzs = ("+" if (dz or 0) >= 0 else "") + ("%.1f%%" % (dz * 100)) if dz is not None else "—"
        dzc = DN if (dz or 0) > 0.05 else FG  # 距zg>+5% 标绿(远离买点)
        trs.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td style='color:%s'>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td style='color:%s'>%s</td>"
            "<td class='act'>%s</td><td class='sub'>%s %s</td></tr>" % (
                r["code"], r["name"], r["group"], color,
                fmt(pct * 100 if pct is not None else None, True),
                fmt(r["close"]), fmt(r["zg"]), fmt(r["zd"]), dzc, dzs,
                r["act"], r["l1"], r["l2"]))
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>T+2 开盘决策卡 · 2026-11-12</title><style>
body{background:%s;color:%s;font:13px/1.6 -apple-system,'Segoe UI',sans-serif;
 margin:24px;max-width:1200px}
h1{font-size:17px} .m{color:#7b8698;font-size:12px}
table{border-collapse:collapse;width:100%%;margin-top:14px}
th{background:#161d28;color:#8fa1b5;font-weight:600;text-align:left;
 padding:7px 10px;border-bottom:1px solid #233042;font-size:12px}
td{padding:7px 10px;border-bottom:1px solid #1a2331}
.act{color:#e8b93f;font-weight:600} .sub{color:#7b8698;font-size:11.5px}
.note{background:#131a24;border-radius:10px;padding:12px 14px;margin-top:16px;
 font-size:12px;color:#8fa1b5}
</style></head><body>
<h1>T+2 开盘决策卡（2026-11-12 周四）</h1>
<div class="m">数据：pool_state_20261111（11-11 收盘）· 生成于 %s ·
红涨绿跌 · 纪律来源：11-11 盘后复盘三条 + 贴 zg 结构纪律</div>
<table><tr><th>代码</th><th>名称</th><th>组</th><th>当日</th><th>收盘</th>
<th>zg</th><th>zd</th><th>距zg</th><th>开盘动作</th><th>二级条件</th></tr>
%s</table>
<div class="note">规则注记：① 电力组开盘 &lt;昨收×0.995 即清（复盘①）；
② 银行组不留防守，开盘 &lt;昨收×0.998 减半、收盘破 zd 次日清（复盘①延伸）；
③ 小盘只留复盘白名单（海天/华依/德马/海油，池外票按同规则人工核）；
④ 所有距 zg &gt;+5%% 的高位票，冲 real_hi 减半（贴 zg 纪律，不追不恋）。
本卡是观察辅助，不构成投资建议；执行以老田盘前判断为准。</div>
</body></html>""" % (BG, FG, time.strftime("%Y-%m-%d %H:%M"), "".join(trs))
    dst = os.path.join(HERE, "h5_daily", "portfolio_card.html")
    open(dst, "w", encoding="utf-8").write(html)
    print("卡片 →", dst, "（%d 只）" % len(rows))


if __name__ == "__main__":
    render(build_rows())
