# -*- coding: utf-8 -*-
# K-ORG-080/N7: 工单成本台账（token/模型/时长/轮次四元组 + 周汇总）
# 数据源: portal/data/llm_usage.jsonl（K-ORG-091 铺的管道，agent_coder._log_usage 落账）
# 边界: 只读不改账本; fail-open（坏行跳过/文件缺失提示后退出 0）; 无网络
# 归因规则: e2e_pipeline 落的 label 形如 "<TID> 产码(返修)?"（K-ORG-072 冻结面原文），
#           故 label 含 TID 即可包含匹配归因到工单; 自由文本 label（如 QA 的
#           "G3台账验收"）不可归因，用 --label <pattern> 手动查（降级路径）。
# 智谱积分估算: anthropic 通道单次 ≈4.5 积分（K-ORG-038 判例 4~5 积分取中），标注「估算」。
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

# channel → 模型档说明（channel 是模型档的代理，K-ORG-080/N7 勘察结论）
CHANNEL_DESC = {
    "anthropic": "glm-4.6 包年",
    "cloud": "glm-4-flash 免费",
    "gpt6": "gpt6",
    "ollama": "本地 9b",
    "claude": "claude_glm",   # agent_coder 落账 channel 实值是 "claude"
}
_TID_RE = re.compile(r"K-[\w-]+")          # TID 含连字符（K-ORG-064 式）
_ANTHROPIC_CREDIT_PER_CALL = 4.5           # K-ORG-038 判例取中，估算


def _usage_path():
    # 账本固定在 <仓根>/portal/data/llm_usage.jsonl（与 agent_coder._log_usage 同源）
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, os.pardir, "portal", "data", "llm_usage.jsonl")


def load_records(path=None):
    # 读账本 → list[dict]; 坏行跳过; 文件不存在返回 []（fail-open）
    path = path or _usage_path()
    if not os.path.exists(path):
        return []
    recs = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("label") is not None:
                recs.append(d)
    return recs


def _parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _tid_of(label):
    # label 内嵌 TID 才可归因（含 TID 即包含匹配）; 自由文本返回 None
    m = _TID_RE.search(str(label))
    return m.group(0) if m else None


def _agg(recs):
    # 聚合四元组: 次数 / in_tok / out_tok / 总时长 / 按 channel 分列 / 积分估算
    by_ch = {}
    for r in recs:
        ch = r.get("channel", "?")
        c = by_ch.setdefault(ch, {"calls": 0, "in_tok": 0, "out_tok": 0, "elapsed": 0.0})
        c["calls"] += 1
        c["in_tok"] += int(r.get("in_tok") or 0)
        c["out_tok"] += int(r.get("out_tok") or 0)
        c["elapsed"] += float(r.get("elapsed") or 0.0)
    n_anth = by_ch.get("anthropic", {}).get("calls", 0)
    return {
        "calls": len(recs),
        "in_tok": sum(c["in_tok"] for c in by_ch.values()),
        "out_tok": sum(c["out_tok"] for c in by_ch.values()),
        "elapsed": round(sum(c["elapsed"] for c in by_ch.values()), 1),
        "channels": by_ch,
        "credit_est": round(n_anth * _ANTHROPIC_CREDIT_PER_CALL, 1),
    }


def usage_for(tid, path=None):
    # 单工单四元组（label 包含 TID 匹配）; 无数据返回 None
    recs = [r for r in load_records(path) if _tid_of(r.get("label")) == tid]
    return _agg(recs) if recs else None


def usage_for_label(pattern, path=None):
    # 降级路径: 手动按 label 子串查询（自由文本 label 不可归因 tid 时用）
    recs = [r for r in load_records(path) if pattern in str(r.get("label"))]
    return _agg(recs) if recs else None


def usage_recent_days(n=7, path=None):
    # 近 n 天按日聚合（周汇总用），返回 {日期: agg}
    days = {}
    for r in load_records(path):
        dt = _parse_ts(r.get("ts", ""))
        if dt is None:
            continue
        key = dt.strftime("%Y-%m-%d")
        if dt.date() >= date.today() - timedelta(days=n - 1):
            days.setdefault(key, []).append(r)
    return {k: _agg(v) for k, v in sorted(days.items())}


def usage_total(n=7, path=None):
    # 近 n 天聚合总量（供 e2e_report 等外部 import 消费）
    return _tot(usage_recent_days(n, path))


def summary_line(tag="成本台账", total=None):
    # K-ORG-080/N7 报告行: 「成本台账: N 次调用 · in X tok · out Y tok · ≈Z 积分(估算)」
    if not total or total.get("calls", 0) == 0:
        return "%s: 无数据" % tag
    return ("%s: %d 次调用 · in %d tok · out %d tok · ≈%s 积分(估算)"
            % (tag, total["calls"], total["in_tok"], total["out_tok"], total["credit_est"]))


def _fmt_agg(tag, a):
    lines = ["%s: %d 次调用 · in %d tok · out %d tok · 总时长 %.1fs · ≈%s 积分(估算·K-ORG-038 判例×4.5/次)"
             % (tag, a["calls"], a["in_tok"], a["out_tok"], a["elapsed"], a["credit_est"])]
    for ch, c in sorted(a["channels"].items()):
        lines.append("  %-10s(%s): %d 次 · in %d · out %d · %.1fs"
                     % (ch, CHANNEL_DESC.get(ch, "?"), c["calls"], c["in_tok"], c["out_tok"], c["elapsed"]))
    return lines


def _tot(days):
    t = {"calls": 0, "in_tok": 0, "out_tok": 0, "elapsed": 0.0, "credit_est": 0.0}
    for a in days.values():
        t["calls"] += a["calls"]
        t["in_tok"] += a["in_tok"]
        t["out_tok"] += a["out_tok"]
        t["elapsed"] += a["elapsed"]
        t["credit_est"] += a["credit_est"]
    t["elapsed"] = round(t["elapsed"], 1)
    t["credit_est"] = round(t["credit_est"], 1)
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(description="K-ORG-080/N7 工单成本台账 (read-only, fail-open)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--tid", help="单工单四元组（label 含 TID 归因）")
    g.add_argument("--week", action="store_true", help="近 7 天按日汇总")
    g.add_argument("--all", action="store_true", help="全量按 TID 分组")
    ap.add_argument("--label", help="降级: 手动按 label 子串查询四元组")
    ap.add_argument("--json", action="store_true", help="结构化输出（供 e2e_report/判读卡消费）")
    ap.add_argument("--data", help=argparse.SUPPRESS)  # 测试用临时账本路径
    args = ap.parse_args(argv)

    recs = load_records(args.data)
    if args.data and not recs and not os.path.exists(args.data or ""):
        print("提示: 账本不存在 %s（fail-open 退出）" % args.data)
        return 0
    if not os.path.exists(args.data or _usage_path()):
        print("提示: 账本不存在 %s（fail-open 退出）" % _usage_path())
        return 0

    if args.tid:
        out = {"tid": args.tid, "usage": usage_for(args.tid, args.data)}
    elif args.label:
        out = {"label": args.label, "usage": usage_for_label(args.label, args.data)}
    elif args.week:
        days = usage_recent_days(7, args.data)
        out = {"week": {k: v for k, v in days.items()}, "total": _tot(days)}
    elif args.all:
        groups = {}
        for r in recs:
            groups.setdefault(_tid_of(r.get("label")) or "(未归因)", []).append(r)
        out = {"all": {k: _agg(v) for k, v in sorted(groups.items())}}
    else:
        ap.error("需指定 --tid / --week / --all / --label 之一")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    # 纯文本表格
    if "usage" in out:
        print("\n".join(_fmt_agg("工单 %s" % (out.get("tid") or out.get("label")), out["usage"]))
              if out["usage"] else "无数据（label 不可归因或无该 TID 记录；可用 --label 手动查）")
    elif "week" in out:
        print("日期        次数  in_tok   out_tok  时长(s)  积分(估算)")
        for k, a in out["week"].items():
            print("%s  %4d  %7d  %7d  %6.1f  %6.1f" % (k, a["calls"], a["in_tok"], a["out_tok"], a["elapsed"], a["credit_est"]))
        t = out["total"]
        print("合计        %4d  %7d  %7d  %6.1f  %6.1f" % (t["calls"], t["in_tok"], t["out_tok"], t["elapsed"], t["credit_est"]))
    else:
        for k, a in out["all"].items():
            print("\n".join(_fmt_agg(k, a)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
