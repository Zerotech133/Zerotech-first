# -*- coding: utf-8 -*-
"""K-STOCK-167 驾驶舱数据面（自 home_server.py/8094 移植，方案 A 拍板）。

2026-10-31 WO-A5FROZEN（K-ORG-20261031-A5FROZEN）从 api_misc.py 原样搬出，
仅做位置迁移：函数名/签名/行为不变（api_misc 顶部 import 回流保持命名空间）。
"""
import json
import os
import time

from api_common import ROOT


# ⚠ api_common.ROOT = portal/ 目录；数据资产在仓库根（home_server 旧 ROOT 语义）
_HOME_ROOT = os.path.dirname(ROOT)
_HOME_PAPER_DB = os.path.join(_HOME_ROOT, "k_stock", "data", "paper.db")
_HOME_ORG_JSON = os.path.join(_HOME_ROOT, "agent_platform", "registry",
                              "org.json")
_HOME_SHADOW = os.path.join(_HOME_ROOT, "agent_platform", "data", "shadow",
                            "shadow_ledger.jsonl")


def _home_mt(p):
    try:
        return time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(os.path.getmtime(p)))
    except OSError:
        return None


def _home_now_context():
    """「此刻」模式（老板动线视角）：按星期+时钟给出现在该看什么。"""
    now = time.localtime()
    wd, t = now.tm_wday, now.tm_hour * 100 + now.tm_min
    if wd >= 5:
        return {"mode": "weekend", "label": "周末 · 裁决窗准备模式",
                "focus": ["复核 5 日窗数据完整性", "预读裁决材料",
                          "菜鸟陪练：对账本随便提问"]}
    if t < 930:
        return {"mode": "pre", "label": "盘前 · 看昨日复盘与今日计划",
                "focus": ["挂单计划（pending 的 plan_date）",
                          "昨日跑批产物核对", "今日信号产出预告"]}
    if t <= 1500:
        return {"mode": "cn_open", "label": "A 股盘中 · 盯账本与信号",
                "focus": ["模拟盘 pending 撮合情况", "策略源最新信号",
                          "账本回撤与止损距离"]}
    if t <= 2130:
        return {"mode": "post", "label": "盘后 · 复盘与明日预告",
                "focus": ["今日跑批产物齐全性", "裁决窗材料更新",
                          "菜鸟日报核对"]}
    return {"mode": "us_open", "label": "美股盘中 · 盯 US 独立账本",
            "focus": ["us_qingping 成交与止损", "US 行情缓存新鲜度",
                      "美股信号次日撮合预告"]}


def _home_money():
    """我的钱：CNY/USD 分组 + 日变动（paper.db 只读）。"""
    try:
        if not os.path.exists(_HOME_PAPER_DB):
            return {"ok": False}
        import sqlite3
        con = sqlite3.connect(
            "file:%s?mode=ro" % _HOME_PAPER_DB.replace("\\", "/"),
            uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        try:
            groups = {"CNY": [], "USD": []}
            for r in con.execute(
                    "SELECT DISTINCT account_id FROM equity_marks "
                    "ORDER BY account_id"):
                aid = r["account_id"]
                marks = con.execute(
                    "SELECT date,total FROM equity_marks WHERE account_id=? "
                    "ORDER BY date", (aid,)).fetchall()
                if not marks:
                    continue
                last = marks[-1]
                prev = marks[-2] if len(marks) > 1 else None
                groups["USD" if str(aid).startswith("us_") else "CNY"].append({
                    "account": aid, "total": last["total"],
                    "delta": (round(last["total"] - prev["total"], 2)
                              if prev else None),
                    "days": len(marks)})
            return {"ok": True, **groups}
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": str(e)[:80]}


def _home_timeline():
    """今日跑批时间线：关键产物 mtime 推 automation 执行态。"""
    checks = [
        ("07:05 主闭环", _HOME_PAPER_DB + ".bak", "settle/exec/mark"),
        ("14:40 双刀扫描",
         os.path.join(_HOME_ROOT, "quant_backtest", "reports"),
         "rps/决策产物"),
        ("17:10 影子运行", _HOME_SHADOW, "投研部影子决策"),
        ("17:35 A0 日报",
         os.path.join(_HOME_ROOT, "agent_platform", "data", "shadow"),
         "日报+点评"),
        ("16:40 X5 公网快照",
         os.path.join(_HOME_ROOT, "deliverables"), "对外快照"),
    ]
    today_s = time.strftime("%Y-%m-%d")
    out = []
    for name, p, what in checks:
        mt = _home_mt(p)
        out.append({"name": name, "what": what, "last": mt,
                    "ran_today": bool(mt and mt.startswith(today_s))})
    return out


def _home_cal():
    """裁决窗倒计时。⚠ 日期随 ROADMAP 里程碑人工更新（当前窗 10-13~15）。"""
    today = time.localtime()
    y = today.tm_year
    win = {"10-13": "撮合验收五判据 + 冻结期生效",
           "10-14": "S3 裁决（RiskGuard 误杀复核）",
           "10-15": "解禁三校准（B9→B10→B8）"}
    items = []
    for md, what in win.items():
        target = time.strptime("%d-%s" % (y, md), "%Y-%m-%d")
        items.append({"day": md, "what": what,
                      "in_days": (time.mktime(target) - time.mktime(today))
                      // 86400})
    return {"ok": True, "items": items}


def _home_org():
    try:
        d = json.loads(open(_HOME_ORG_JSON, encoding="utf-8").read())
        return {"ok": True,
                "chief": d.get("chief", {}).get("name")
                         or d.get("chief", {}).get("agent_id"),
                "depts": [{"name": x.get("name", x.get("dept")),
                           "status": x.get("status", "active"),
                           "charter": x.get("charter", "")}
                          for x in d.get("departments", [])]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": str(e)[:80]}


def build_home_cockpit() -> dict:
    """零界门面「此刻区」数据源（原 home_server.cockpit 瘦身版）。"""
    return {"ok": True, "now": time.strftime("%Y-%m-%d %H:%M"),
            "now_context": _home_now_context(),
            "money": _home_money(),
            "timeline": _home_timeline(),
            "calendar": _home_cal(),
            "org": _home_org()}
