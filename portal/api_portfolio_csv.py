# -*- coding: utf-8 -*-
"""P4 批7-PF 函数族拆出：portfolio 摘要/CSV/平仓原因分类/配置生效。

2026-10-31 WO-A5FROZEN（K-ORG-20261031-A5FROZEN）从 api_portfolio.py 原样
搬出，仅做位置迁移：函数名/签名/行为不变（api_portfolio 顶部 import 回流）。
"""
import os
import subprocess
import sys

from api_common import QBT_DIR, REPORTS_DIR, _read_json, _screen_cache


def _pf_summary(sid):
    """配置生效后读回该账户摘要（现金/净值/持仓数）供前端反馈。"""
    led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
    ac = (led.get("accounts") or {}).get(sid) or {}
    curve = ac.get("nav_curve") or []
    return {"capital": ac.get("capital"), "cash": ac.get("cash"),
            "n_open": len(ac.get("open") or []),
            "nav": curve[-1]["nav"] if curve else ac.get("capital")}

def _pf_csv(sid=""):
    """K51-B 交易明细 CSV：平仓+加减仓流水（按日期混排）+当前持仓快照。
    utf-8-sig 带 BOM（Excel 直开不乱码）。"""
    import csv
    import io
    led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
    rows = []
    for _sid, ac in (led.get("accounts") or {}).items():
        if sid and _sid != sid:
            continue
        for c in ac.get("closed") or []:
            rows.append([c.get("exit_date", ""), _sid, c.get("name", ""),
                         c.get("market", ""), c.get("reason", ""),
                         c.get("exit_px", ""), c.get("qty", ""),
                         round((c.get("qty") or 0) * (c.get("exit_px") or 0), 2),
                         c.get("exit_pnl_pct", ""), c.get("hold_days", "")])
        for t in ac.get("trades") or []:
            rows.append([t.get("d", ""), _sid, t.get("name", ""), "",
                         t.get("act", ""), t.get("px", ""), "",
                         t.get("amount", ""), t.get("pct", ""), ""])
    rows.sort(key=lambda r: r[0])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["日期", "账户", "标的", "市场", "动作", "价格",
                "数量", "金额/市值", "盈亏%/比例", "持有日"])
    w.writerows(rows)
    w.writerow([])
    w.writerow(["当前持仓", "", "", "", "", "", "", "", "", ""])
    w.writerow(["建仓日", "账户", "标的", "市场", "备注", "现价",
                "数量", "成本", "浮盈%", "持有日"])
    for _sid, ac in (led.get("accounts") or {}).items():
        if sid and _sid != sid:
            continue
        for p in ac.get("open") or []:
            w.writerow([p.get("entry_date", ""), _sid, p.get("name", ""),
                        p.get("market", ""), p.get("note", ""),
                        p.get("last_px", ""), p.get("qty", ""),
                        p.get("cost", ""), p.get("pnl_pct", ""),
                        p.get("hold_days", "")])
    return buf.getvalue().encode("utf-8-sig")

def _pf_reason_cat(r):
    """K51-B 平仓原因归类（复盘统计口径）：止损/止盈/闸门/翻空/离池/
    到期/手动/跟随源/其他。"""
    r = r or ""
    for pre, cat in (("止损", "止损"), ("止盈", "止盈"), ("环境闸门", "闸门"),
                     ("信号翻空", "翻空"), ("离池", "离池"), ("持有", "到期"),
                     ("手动", "手动"), ("源账本", "跟随源"), ("撤资", "其他")):
        if r.startswith(pre):
            return cat
    return "其他"

def _pf_apply(sid):
    """K45/K46 配置生效公共段：立即跑一次跑批（同日幂等：只应用资金
    注资/撤资，不推进交易日）→ 清大屏缓存。返回 (run_tail, err)。"""
    try:
        r = subprocess.run(
            [sys.executable, "-X", "utf8", "portfolio.py"],
            cwd=QBT_DIR, capture_output=True, text=True,
            timeout=180, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return None, "跑批失败 rc=%d: %s" % (
                r.returncode, (r.stdout or r.stderr or "")[-300:])
    except subprocess.TimeoutExpired:
        return None, "跑批超时（180s）"
    _screen_cache["t"] = 0.0              # 配置已变，清大屏缓存
    return (r.stdout or "").strip().splitlines()[-2:], None
