# -*- coding: utf-8 -*-
"""K-STOCK-122 M7 手机端止盈止损指令层（飞书指令的消费端）。

设计（方案 §M7，老田 2026-10-11 追加）：
- 止损（sl）：直接委托 PaperEngine.set_stop（复用既有 check_stops 触发链，
  SELL pending 次日开盘撮合，与 auto_stops 机制完全同轨）。
- 止盈（tp）：引擎无止盈位字段，落本文件 exit_rules.json（持仓级主动线），
  check_tps() 每日 routine 中调用——湖内最新收盘 >= 触发价 → 生成 SELL
  pending（order_kind='tp'，strategy='take_profit'），镜像 check_stops 的
  幂等 ref 与 T+1 可卖检查，走既有风控与对账。
- 与 tier_gate 关系：本层是持仓级主动线，tier_gate 是账本级纪律熔断，
  两层独立，本层触发不豁免 tier_gate。

CLI（feishu_bot_ws 经子进程调用，--json 单行输出）：
  python exit_rules.py list  [--account main] [--json]
  python exit_rules.py set   --code 605100 --kind tp --pct 8 [--account main] [--json]
  python exit_rules.py set   --code 605100 --kind sl --pct 5 [--account main] [--json]
  python exit_rules.py clear --code 605100 --kind tp [--account main] [--json]
  python exit_rules.py check [--account main] [--dry-run] [--json]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import PaperEngine          # noqa: E402
from market import ParquetMarket                # noqa: E402

try:  # 与 trainee 同款相对导入兜底
    from trainee import DEFAULT_DB              # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    DEFAULT_DB = HERE.parent / "data" / "paper.db"

RULES_PATH = HERE / "exit_rules.json"
DEFAULT_ACCOUNT = "main"
_EPS = 1e-9


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_code(raw: str) -> str:
    """手机指令码补全交易所后缀：605100→605100.SH（engine._norm_stock 不补后缀）。
    规则：6xx→SH；0/3xx→SZ；8/4xx→BJ；gb_ 前缀（US）原样。"""
    s = str(raw).strip().upper()
    if s.startswith("GB_"):
        return s
    digits = "".join(ch for ch in s if ch.isdigit())[:6]
    if len(digits) == 6:
        suf = ("SH" if digits.startswith("6")
               else "BJ" if digits.startswith(("8", "4"))
               else "SZ")
        return "%s.%s" % (digits, suf)
    from engine import _norm_stock
    return _norm_stock(raw)


def _load() -> dict:
    if RULES_PATH.exists():
        with open(RULES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"rules": []}


def _save(d: dict) -> None:
    # K-STOCK-414：原子写（tmp+replace）——半截 JSON 会让 _load 抛异常，
    # set/clear/check 全线不可用（routine 吞成 tp_error=止盈链静默瘫痪）
    tmp = str(RULES_PATH) + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=1))
    os.replace(tmp, str(RULES_PATH))


def _engine(account: str) -> PaperEngine:
    return PaperEngine(DEFAULT_DB, ParquetMarket(), account=account)


# ---------------- 指令动作 ----------------

def set_line(account: str, code: str, kind: str, pct: float) -> dict:
    """设止盈/止损线。pct 一律为正数（触发幅度）。止损→引擎 stop_price；
    止盈→exit_rules.json（触发价=持仓成本×(1+pct/100)）。"""
    kind = (kind or "").strip().lower()
    if kind not in ("tp", "sl"):
        return {"ok": False, "error": "kind 须为 tp（止盈）或 sl（止损）"}
    try:
        pct = float(str(pct).rstrip("%"))
    except ValueError:
        return {"ok": False, "error": "pct 须为数字（如 8 或 8%%）"}
    if pct <= 0:
        return {"ok": False, "error": "pct 须为正数（止损写 5 表示跌 5%% 触发）"}
    if pct >= 90:
        return {"ok": False, "error": "pct 超出合理区间 (0,90)"}
    stock = _norm_code(code)
    eng = _engine(account)
    try:
        pos = next((p for p in eng.positions(account)
                    if p["stock"] == stock), None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "账本不可用: %r" % e}
    if not pos:
        return {"ok": False, "error": "%s 在账本 %s 无持仓" % (stock, account)}
    cost = pos["avg_cost"]
    if kind == "sl":
        r = eng.set_stop(stock, round(cost * (1 - pct / 100), 2),
                         account_id=account)
        out = {"ok": True, "kind": "sl", "account": account,
               "stock": stock, "avg_cost": cost,
               "line": r["stop_price"], "pct": -pct,
               "note": "已设止损（复用既有 check_stops 链，触线次日开盘清仓）",
               "at": _now()}
    else:
        line = round(cost * (1 + pct / 100), 2)
        d = _load()
        d["rules"] = [x for x in d.get("rules", [])
                      if not (x["account"] == account and x["stock"] == stock)]
        d["rules"].append({"account": account, "stock": stock, "kind": "tp",
                           "avg_cost": cost, "line": line, "pct": pct,
                           "set_at": _now(), "source": "feishu"})
        _save(d)
        out = {"ok": True, "kind": "tp", "account": account,
               "stock": stock, "avg_cost": cost, "line": line, "pct": pct,
               "note": "已设止盈（每日 routine 检查，触线次日开盘清仓）",
               "at": _now()}
    out["qty"] = pos["qty"]
    return out


def clear_line(account: str, code: str, kind: str) -> dict:
    kind = (kind or "").strip().lower()
    stock = _norm_code(code)
    if kind == "sl":
        _engine(account).clear_stop(stock, account_id=account)
        return {"ok": True, "kind": "sl", "stock": stock,
                "note": "已撤销止损", "at": _now()}
    if kind == "tp":
        d = _load()
        n0 = len(d.get("rules", []))
        d["rules"] = [x for x in d.get("rules", [])
                      if not (x["account"] == account and x["stock"] == stock
                              and x["kind"] == "tp")]
        _save(d)
        if len(d["rules"]) == n0:
            return {"ok": False, "error": "%s 无止盈线可撤" % stock}
        return {"ok": True, "kind": "tp", "stock": stock,
                "note": "已撤销止盈", "at": _now()}
    return {"ok": False, "error": "kind 须为 tp 或 sl"}


def list_lines(account: str = DEFAULT_ACCOUNT) -> dict:
    """当前持仓线位快照（止损来自引擎，止盈来自 exit_rules.json）。"""
    eng = _engine(account)
    rows = []
    try:
        stops = {x["stock"]: x for x in eng.stops(account)}
    except Exception:  # noqa: BLE001
        stops = {}
    for pos in eng.positions(account):
        s = stops.get(pos["stock"])
        rows.append({
            "stock": pos["stock"], "qty": pos["qty"],
            "avg_cost": pos["avg_cost"], "last": (s or {}).get("last"),
            "stop_price": (s or {}).get("stop_price"),
            "stop_distance_pct": (s or {}).get("distance_pct"),
        })
    tps = [x for x in _load().get("rules", [])
           if x["account"] == account and x["kind"] == "tp"]
    return {"ok": True, "account": account, "positions": rows,
            "tp_rules": tps}


def check_tps(account: str, eng: PaperEngine | None = None,
              dry_run: bool = False) -> dict:
    """止盈检查（每日 routine 在 check_stops 后调用）。
    镜像 engine.check_stops：收盘 >= 触发价 → SELL pending（order_kind='tp'）。"""
    eng = eng or _engine(account)
    out = {"triggered": [], "watch": [], "account": account,
           "dry_run": dry_run}
    for rule in [x for x in _load().get("rules", [])
                 if x["account"] == account and x["kind"] == "tp"]:
        stock = rule["stock"]
        try:
            pos = next((p for p in eng.positions(account)
                        if p["stock"] == stock), None)
        except Exception:  # noqa: BLE001
            continue
        if not pos:   # 已清仓 → 规则失效，就地清除
            if not dry_run:
                clear_line(account, stock, "tp")
            out["watch"].append({"stock": stock, "why": "无持仓，规则已清除"})
            continue
        try:
            bar = eng.market.last_bar(stock)
        except Exception:  # noqa: BLE001
            bar = None
        if bar is None:
            continue
        if bar["close"] < rule["line"] - _EPS:
            out["watch"].append({"stock": stock, "close": bar["close"],
                                 "line": rule["line"]})
            continue
        ref = "tp#%s@%s#%s" % (account, stock, bar["trade_date"])
        dup = eng.con.execute("SELECT id FROM orders WHERE ref=?",
                              (ref,)).fetchone()
        if dup:
            out["triggered"].append({"stock": stock,
                                     "why": "幂等跳过（%s 已存在）" % ref})
            continue
        avail = eng.available_qty(stock, account)
        if avail <= 0:
            out["triggered"].append({"stock": stock,
                                     "why": "触发但可卖 0（T+1 冻结），明日再查"})
            continue
        plan_date = eng.market.next_trading_day(
            datetime.now().isoformat(timespec="seconds"))
        if plan_date is None:
            out["triggered"].append({"stock": stock,
                                     "why": "湖无后续交易日，明日再查"})
            continue
        reason = ("止盈触发：收盘 %s >= 触发价 %s（+%s%%，%s），"
                  "明日开盘清仓 %s 股" % (bar["close"], rule["line"],
                                          rule["pct"], bar["trade_date"],
                                          avail))
        if not dry_run:
            eng.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,"
                "price_base,status,reason,conf,strategy,order_kind,"
                "account_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), "ai", ref, stock, "SELL", avail, plan_date, "open",
                 "pending", reason, None, "take_profit", "tp", account,
                 _now()))
            # 触发后规则完成使命，就地清除
            clear_line(account, stock, "tp")
        out["triggered"].append({"stock": stock, "qty": avail,
                                 "plan_date": plan_date, "ref": ref,
                                 "reason": reason, "status": "pending"})
    if not dry_run:
        eng.con.commit()
    return out


# ---------------- CLI ----------------

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="exit_rules", description=__doc__)
    # 全局参数同时挂主 parser 与各子命令（调用方习惯 `set ... --account x`）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--account", default=DEFAULT_ACCOUNT)
    common.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--account", default=DEFAULT_ACCOUNT)
    ap.add_argument("--dry-run", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", parents=[common])
    p = sub.add_parser("set", parents=[common])
    p.add_argument("--code", required=True)
    p.add_argument("--kind", required=True, choices=["tp", "sl"])
    p.add_argument("--pct", required=True)
    p = sub.add_parser("clear", parents=[common])
    p.add_argument("--code", required=True)
    p.add_argument("--kind", required=True, choices=["tp", "sl"])
    sub.add_parser("check", parents=[common])
    a, _unk = ap.parse_known_args(argv)
    # K-STOCK-414：删除复制粘贴的重复分发块（set/clear/check 全部双跑：
    # clear sl 持仓已清时第二次抛 Reject → 成功操作也报错退出码 1）
    if a.cmd == "list":
        r = list_lines(a.account)
    elif a.cmd == "set":
        r = set_line(a.account, a.code, a.kind, a.pct)
    elif a.cmd == "clear":
        r = clear_line(a.account, a.code, a.kind)
    else:
        r = check_tps(a.account, dry_run=a.dry_run)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
