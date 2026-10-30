# -*- coding: utf-8 -*-
"""K-STOCK-122 M6 周报/月报（四段：成绩/问题/下步方向/待裁决）+ 飞书推送。

数据面（零 LLM 也可产出）：equity_marks 净值、成交账本、judge_reviews、
review_minutes（分歧/决议）、tier_gate 触发（orders reason 含"纪律门槛"）。
「下步工作方向」llm=True 时由投研经理 Ollama 生成，否则规则兜底。

口径变更记录（2026-10-12 多组合对齐，K-STOCK-154）：
  此前净值只取 main（组合 chaser/taishan 权益不入周报），orders 统计无
  account 过滤（混入 us_qingping USD 独立账本）。现统一为人民币侧账户
  集合 cn_accounts() = {main} ∪ {每个已订阅组合的 trader}（chaser/taishan…），
  us_qingping（USD 独立账本口径）不入人民币组合成绩。
  performance 新增：total_equity（逐账户周期内最新 mark 求和）、
  equity_by_account（dict account→rows）、accounts（per-account 成交拆分）。
  兼容性：equity 字段仍为 main 曲线不变；fills/buys/discipline_blocks
  由「全账户」改为过滤后口径（原实现含 USD 账本，属口径修正）。
  --pf 可切换单账户视角。

用法：
  python period_summary.py --weekly  [--push] [--llm] [--pf all|account_id]
  python period_summary.py --monthly [--push] [--llm] [--pf all|account_id]
落盘：k_stock/data/summary/weekly-YYYY-Www.json | monthly-YYYY-MM.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
sys.path.insert(0, str(HERE))

from judge import _connect, LLM_MODEL, OLLAMA  # noqa: E402
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为 k_stock 包导入时
    from k_stock.paper.safe_urlopen import safe_urlopen

# 组合→trader 映射数据文件（portal/subscriptions.py 的存储层）。
SUB_PATH = HERE.parent.parent / "portal" / "data" / "strategy_subscriptions.json"
# us_qingping 为 USD 独立账本口径，不入人民币组合成绩
USD_LEDGER = "us_qingping"
FALLBACK_ACCOUNTS = frozenset({"main", "chaser"})


def cn_accounts() -> set:
    """人民币侧账户集合：{main} ∪ {每个已订阅组合的 trader}。

    组合→trader 映射来自 portal/data/strategy_subscriptions.json
    （portal/subscriptions.py:portfolios() 为官方枚举接口，此处直接读
    同一数据文件以省去 strategies.json 依赖、降低导入成本）。
    文件缺失/损坏时兜底 {main, chaser} 并 stderr 警告；
    us_qingping 即使被误绑为 trader 也强制排除（USD 独立账本口径）。
    """
    accounts = {"main"}
    try:
        d = json.loads(SUB_PATH.read_text(encoding="utf-8"))
        for pf in d.get("portfolios") or []:
            t = pf.get("trader")
            if t:
                accounts.add(t)
    except Exception as exc:  # noqa: BLE001
        print(
            "WARN: strategy_subscriptions 读取失败，账户集兜底 "
            "{main, chaser}：%s" % exc,
            file=sys.stderr,
        )
        accounts |= set(FALLBACK_ACCOUNTS)
    accounts.discard(USD_LEDGER)
    return accounts


def _period(kind: str):
    today = datetime.now().date()
    if kind == "weekly":
        start = today - timedelta(days=today.weekday() + 7)  # 上周一
        end = start + timedelta(days=6)
        tag = "weekly-%s-W%02d" % (start.isocalendar()[0], start.isocalendar()[1])
    else:
        # 月报由每周日 20:30 计划任务触发（Windows 月末触发器注册繁琐），
        # 此处守卫：仅当今天是本月最后一日才真正产出+推送，否则静默退出。
        tomorrow = today + timedelta(days=1)
        if tomorrow.month != today.month:
            pass  # 今天是月末 → 继续
        else:
            return None, None, None
        end = today
        start = end.replace(day=1)
        tag = "monthly-%04d-%02d" % (end.year, end.month)
    return start.isoformat(), end.isoformat(), tag


def build(kind: str, llm: bool = False, account: str = "all") -> dict:
    start, end, tag = _period(kind)
    out: dict = {"ok": True, "kind": kind, "tag": tag, "period": [start, end]}
    # 口径（2026-10-12 多组合对齐）：--pf all 聚合 main+全部组合 trader 账本；
    # 指定 account_id 则只看该账户。us_qingping 不在推导集内（USD 独立账本）。
    accounts = {account} if account != "all" else cn_accounts()
    ph = ",".join("?" * len(accounts))
    accts = tuple(sorted(accounts))
    con = _connect()
    try:
        # ① 成绩：净值序列（多账户） + 成交统计（同口径过滤）
        eq_all = [
            dict(r)
            for r in con.execute(  # nosec B608 - 值参数化/内部标识符（已抽查）
                "SELECT account_id,date,total FROM equity_marks "  # nosec B608 - 值参数化/内部标识符（已抽查）
                "WHERE account_id IN (%s) AND date BETWEEN ? AND ? ORDER BY date" % ph,
                accts + (start, end),
            )
        ]
        # 兼容：equity 字段仍为 main 曲线（--pf 单账户时为该账户曲线）
        base = account if account != "all" else "main"
        eq = [r for r in eq_all if r["account_id"] == base]
        if len(eq) >= 2:
            chg = (eq[-1]["total"] / eq[0]["total"] - 1) * 100
        else:
            chg = None
        # 口径修正：原实现无 account 过滤，混入 us_qingping 等 USD 账本订单
        fills = con.execute(  # nosec B608 - 值参数化/内部标识符（已抽查）
            "SELECT COUNT(*) n, SUM(CASE WHEN side='BUY' THEN 1 ELSE 0 END) b "  # nosec B608 - 值参数化/内部标识符（已抽查）
            "FROM orders WHERE status='filled' AND fill_date BETWEEN ? AND ? "
            "AND account_id IN (%s)" % ph,
            (start, end) + accts,
        ).fetchone()
        # per-account 成交拆分（口径同上：人民币侧账户集合）
        by_acct = {
            r["acct"]: {"fills": r["n"], "buys": r["b"] or 0}
            for r in con.execute(  # nosec B608 - 值参数化/内部标识符（已抽查）
                "SELECT account_id acct, COUNT(*) n, SUM(CASE WHEN "  # nosec B608 - 值参数化/内部标识符（已抽查）
                "side='BUY' THEN 1 ELSE 0 END) b FROM orders "
                "WHERE status='filled' AND fill_date BETWEEN ? AND ? "
                "AND account_id IN (%s) GROUP BY account_id" % ph,
                (start, end) + accts,
            )
        }
        # 合并权益：逐账户周期内最新 mark 求和
        latest = {}
        for r in eq_all:
            latest[r["account_id"]] = r["total"]
        out["performance"] = {
            "equity": eq,
            "change_pct": round(chg, 2) if chg is not None else None,
            "fills": fills["n"],
            "buys": fills["b"] or 0,
            "total_equity": round(sum(latest.values()), 2) if latest else None,
            "equity_by_account": {
                a: [r for r in eq_all if r["account_id"] == a] for a in sorted(latest)
            },
            "accounts": by_acct,
        }
        # ② 问题：judge 评审低分日（JSON 目录）+ 纪律拦截（DB）
        low = []
        jdir = DATA / "judge_reviews"
        if jdir.exists():
            for fp in jdir.glob("*.json"):
                try:
                    d = json.loads(fp.read_text(encoding="utf-8"))
                    if (
                        start <= (d.get("date") or "") <= end
                        and (d.get("score") or 100) < 70
                    ):
                        low.append(
                            {
                                "date": d.get("date"),
                                "score": d.get("score"),
                                "grade": d.get("grade"),
                            }
                        )
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/period_summary.py", exc)
        # 口径修正：纪律拦截同 account 过滤（原混入 USD 独立账本拒单）
        blocks = con.execute(  # nosec B608 - 值参数化/内部标识符（已抽查）
            "SELECT COUNT(*) FROM orders WHERE status='rejected' "  # nosec B608 - 值参数化/内部标识符（已抽查）
            "AND (reason LIKE '%纪律%' OR reason LIKE '%门槛%') "
            "AND substr(ts,1,10) BETWEEN ? AND ? "
            "AND account_id IN (" + ph + ")",
            (start, end) + accts,
        ).fetchone()[0]
        out["problems"] = {"low_judge_days": low, "discipline_blocks": blocks}
    finally:
        con.close()
    # 分歧/决议统计（review_minutes）
    mdir = DATA / "review_minutes"
    dis_total, res_total = 0, 0
    if mdir.exists():
        for fp in mdir.glob("*.json"):
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                if start <= (d.get("date") or "") <= end:
                    dis_total += len(d.get("disagreements") or [])
                    res_total += len(d.get("resolutions") or [])
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/period_summary.py", exc)
    out["problems"]["meeting_disagreements"] = dis_total
    out["problems"]["meeting_resolutions"] = res_total
    # ③ 下步方向 + ④ 待裁决
    out["next_steps"] = _llm_next_steps(out) if llm else _rule_next_steps(out)
    out["decisions_needed"] = _decisions(out)
    out_dir = DATA / "summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / (tag + ".json")
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["_path"] = str(fp)
    return out


def _rule_next_steps(out: dict) -> list:
    p = out["problems"]
    steps = []
    if p["low_judge_days"]:
        steps.append(
            "评审低分 %d 天：优先归因 %s"
            % (len(p["low_judge_days"]), p["low_judge_days"][0].get("date"))
        )
    if p["discipline_blocks"]:
        steps.append(
            "纪律拦截 %d 次：核对 tier_gate 参数是否需重校准" % p["discipline_blocks"]
        )
    if p["meeting_disagreements"]:
        steps.append("复盘会分歧 %d 次：跟踪决议执行率" % p["meeting_disagreements"])
    if out["performance"]["fills"] == 0:
        steps.append("周期内零成交：检查信号源新鲜度与 exec 链路")
    return steps or ["按既有纪律继续执行，维持当前策略组合"]


def _llm_next_steps(out: dict) -> list:
    try:
        import urllib.request

        brief = json.dumps(
            {k: out.get(k) for k in ("performance", "problems")}, ensure_ascii=False
        )[:1200]
        prompt = (
            "你是投研经理。基于以下周期总结数据，给出下阶段 3-5 条工作方向，"
            "每条一行、以 - 开头、不超过 40 字：\n%s" % brief
        )
        body = json.dumps(
            {
                "model": LLM_MODEL,
                "stream": False,
                "think": False,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        req = urllib.request.Request(
            OLLAMA, data=body, headers={"Content-Type": "application/json"}
        )
        d = json.loads(safe_urlopen(req, timeout=120).read().decode("utf-8"))
        msg = d.get("message", {}).get("content", "")
        if "</think>" in msg:
            msg = msg.split("</think>", 1)[1]
        lines = [
            ln.strip()[2:].strip()
            for ln in msg.splitlines()
            if ln.strip().startswith("-")
        ]
        return lines[:5] or _rule_next_steps(out)
    except Exception:  # noqa: BLE001
        return _rule_next_steps(out)


def _decisions(out: dict) -> list:
    """需老田拍板项：shadow 策略订阅中 / 低分连续 / 回撤超阈值。"""
    need = []
    try:
        import sys as _s

        _s.path.insert(0, str(HERE.parent.parent / "portal"))
        import subscriptions as _sub

        for pf in _sub.portfolios().get("portfolios") or []:
            for sid in pf.get("_shadow") or []:
                need.append("组合含 shadow 态策略 %s：是否继续保留订阅？" % sid)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/period_summary.py", exc)
    eq = out["performance"]["equity"]
    if len(eq) >= 2 and eq[-1]["total"] < eq[0]["total"] * 0.95:
        need.append("周期回撤超 5%%：是否降低仓位上限或暂停新增订阅？")
    return need or ["无待裁决事项"]


def _push(out: dict) -> tuple:
    portal = HERE.parent.parent / "portal"
    sys.path.insert(0, str(portal))
    from feishu_card_v2 import send_card

    perf = out["performance"]
    chg = (
        ("%.2f%%" % perf["change_pct"])
        if perf["change_pct"] is not None
        else "数据不足"
    )

    def layer(title, md, expanded=False):
        return {
            "tag": "collapsible_panel",
            "expanded": expanded,
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": md}],
        }

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {
                "tag": "plain_text",
                "content": "%s 总结 · %s"
                % ("周报" if out["kind"] == "weekly" else "月报", out["tag"]),
            },
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**📈 成绩**\n净值变化 %s · 成交 %d 笔（买 %d）"
                    % (chg, perf["fills"], perf["buys"]),
                },
            },
            {"tag": "hr"},
            layer(
                "② 存在的问题",
                "\n".join(
                    "- %s 评审 %s（%s）" % (d["date"], d["score"], d["grade"])
                    for d in out["problems"]["low_judge_days"]
                )
                + "\n- 纪律拦截 %d 次 · 复盘会分歧 %d 次"
                % (
                    out["problems"]["discipline_blocks"],
                    out["problems"]["meeting_disagreements"],
                )
                or "（无）",
            ),
            layer(
                "③ 下步工作方向", "\n".join("- " + s for s in out["next_steps"]), True
            ),
            layer("④ 待你裁决", "\n".join("- " + s for s in out["decisions_needed"])),
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "模拟盘周期总结 · 详见门户"}
                ],
            },
            # 联动：按钮 → 机器人指令回路
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📊 持仓线位/下指令"},
                        "type": "primary",
                        "value": {"tag": "er:list"},
                    },
                ],
            },
        ],
    }
    return send_card(card)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="period_summary")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--weekly", action="store_true")
    g.add_argument("--monthly", action="store_true")
    ap.add_argument("--push", action="store_true", help="推飞书手机")
    ap.add_argument("--llm", action="store_true", help="下步方向 LLM 生成")
    ap.add_argument(
        "--pf",
        default="all",
        help="账户口径：all=聚合 main+全部已订阅组合 trader 账本"
        "（默认，2026-10-12 起；剔除 us_qingping USD 独立"
        "账本），或指定 account_id（main/chaser/taishan…）"
        "看单账户",
    )
    a = ap.parse_args(argv)
    kind = "weekly" if a.weekly else "monthly"
    s, e, tag = _period(kind)
    if tag is None:  # 月末守卫：非月末日的 monthly 触发静默退出
        print(json.dumps({"ok": True, "skipped": "not month-end"}, ensure_ascii=False))
        return 0
    out = build(kind, llm=a.llm, account=a.pf)
    if a.push and out.get("ok"):
        ok, msg = _push(out)
        out["push"] = {"ok": bool(ok), "msg": str(msg)}
        # M6 审查修复：推送结果回写落盘（原实现只进 stdout，计划任务
        # 重定向缺失时即丢），且推送失败不再假绿返回 0。
        try:
            _fp = out.get("_path")
            if _fp:
                from pathlib import Path as _Path

                _Path(_fp).write_text(
                    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
                )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/period_summary.py", exc)
    print(
        json.dumps(
            {k: v for k, v in out.items() if k != "performance" or True},
            ensure_ascii=False,
        )[:800]
    )
    if not out.get("ok"):
        return 1
    if a.push and out.get("push") and not out["push"].get("ok"):
        return 1  # M6 审查修复：推送失败非零，计划任务可见
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
