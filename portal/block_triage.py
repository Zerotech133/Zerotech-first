# -*- coding: utf-8 -*-
"""通知队列 blocked 存量分诊只读分析器（FB-G 工位，2026-10-20）。

用途：/api/notify/queue 投影存在 MAX_ITEMS=200 页面级截断（api_notify.py），
blocked 真实存量需离线直取。本脚本：
  1. import portal/api_notify.py 的队列组装入口（notify_queue_payload 及
     decision_items / flow_items / deadline_items 四个数据源函数），直接
     import 调用——不起服务、不触网、全程 mode=ro 只读，不写任何账本；
  2. 绕开 200 截断：直调四个数据源函数取全量条目后按 state=blocked 过滤
     （payload 的 states 计数在截断前完成，故 states.blocked 即真实全量，
     本脚本用它做对账）；
  3. 统计：原因分布 / 账龄分桶（按 created_at，兜底 send_at）/ 来源×kind
     交叉分布 / 重复度（同 title+target、同 title 两视图）/ 今日成功覆盖
     交叉检查；
  4. 输出：--out <md> 落分诊报告、--json <路径> 落全量明细，幂等可重跑。

closed on failure 纪律：任何数据源不可达/异常不崩——errors 透出、显式打
印「closed on failure: ...」并以非零码退出；对账不一致同样非零退出，
不假绿。零决策权：本脚本只产出统计与建议，不执行任何清理/重发。

用法：
  python portal/block_triage.py --out <md路径> [--json <json路径>] [--root <AIOS_ROOT>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

CST = timezone(timedelta(hours=8))

PORTAL_DIR = Path(__file__).resolve().parent


# ---------- 基础工具 ----------


def parse_ts(s) -> datetime | None:
    """宽松时间解析；失败返回 None（不抛）。"""
    t = str(s or "").strip()
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/block_triage.py", exc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t[:19], fmt)
        except ValueError:
            continue
    return None


def age_days(it: dict, now: datetime) -> float | None:
    dt = parse_ts(it.get("created_at") or it.get("send_at"))
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def age_bucket(d: float | None) -> str:
    if d is None:
        return "时间不可解析"
    if d < 1:
        return "<1d"
    if d < 3:
        return "1-3d"
    if d < 7:
        return "3-7d"
    return ">7d"


# ---------- 原因归类 ----------


def reason_bucket(it: dict) -> str:
    """why 字段逐一归类：按来源用其机器可读字段/原因模板归桶。"""
    src = str(it.get("source") or "")
    reason = str(it.get("reason") or "")
    if src == "decision_db":
        return "决策DB·outcome=%s" % (str(it.get("outcome") or "") or "空(未知)")
    if src == "push_flow":
        m = re.search(r"channel=(\S+)", reason)
        return "发送流水ok=0·channel=%s" % (m.group(1) if m else "未标注")
    if src == "deadline_flag":
        m = re.search(r"状态 (\S+?)（", reason)
        return "数据锚未就绪·status=%s" % (m.group(1) if m else "未标注")
    return "%s·未分类" % (src or "未知来源")


# ---------- 建议处置 ----------


def build_suggestions(blocked: list, covered_keys: set) -> tuple[dict, dict]:
    """对每条 blocked 给建议（带依据 key）；返回 (逐条建议, 汇总计数)。

    建议 key 与依据（全部锚定 api_notify.py 代码事实/账本交叉证据）：
      R1 过期丢弃候选  —— 同(kind,target)今日已有 ok=1 live 发送留痕
                          （payload auto 区今日聚合），失败行信息已被覆盖；
                          push_flow blocked 本身仅近 7 天回看窗口。
      R2 过期归档候选  —— 决策行账龄 >7d（超出流水回看窗口），且队列
                          只读无自动重发通道（api_notify 零决策权）。
      R3 重发候选      —— 投递层失败（dead_letter 证据）且 <=3d 且今日
                          无成功覆盖，发送意图明确（有 target/kind）。
      R4 需人工核实    —— outcome=attempted/unknown_after_attempt，
                          map_decision_state 诚实呈现不猜成功，真实送达未知。
      R5 需人工看      —— deadline 旗标：上游数据锚未就绪，队列无法自愈；
                          escalated 升级件优先。
      R6 按簇合并      —— 同(title,target) 计数>=2 的重复项按簇合并处置。
    """
    per_item = {}
    counts = Counter()
    dup_keys = {k for k, v in defaultdict(list, {}).items()}  # placeholder，下面重算
    clusters = defaultdict(list)
    for it in blocked:
        clusters[(str(it.get("title") or ""), str(it.get("target") or ""))].append(it)
    dup_keys = {k for k, v in clusters.items() if len(v) >= 2}

    for it in blocked:
        src = str(it.get("source") or "")
        outcome = str(it.get("outcome") or "")
        key = (str(it.get("kind") or ""), str(it.get("target") or ""))
        tg = (str(it.get("title") or ""), str(it.get("target") or ""))
        ab = age_bucket(age_days(it, datetime.now(CST)))
        sug, why = "", ""
        if src == "deadline_flag":
            sug, why = (
                "R5",
                "数据锚未就绪属上游数据问题，队列只读无法自愈；需人工巡检数据链",
            )
        elif src == "decision_db" and outcome in ("attempted", "unknown_after_attempt"):
            sug, why = (
                "R4",
                "状态映射诚实呈现不猜成功（api_notify 头注），真实送达未知，需对照 dead_letters 核实",
            )
        elif key in covered_keys:
            sug, why = (
                "R1",
                "同(kind,target)今日已有 ok=1 live 发送留痕（队列 auto 区今日聚合），失败行信息已被覆盖",
            )
        elif ab == ">7d":
            sug, why = (
                "R2",
                "账龄>7d 已超出 push_flow 近7天回看窗口时效，队列只读无自动重发通道",
            )
        elif (
            ab in ("<1d", "1-3d")
            and str(it.get("evidence_delivery") or "") == "dead_letter"
        ):
            sug, why = (
                "R3",
                "投递层失败（dead_letter 凭证）且在近效期内、今日无成功覆盖，可人工确认后重发",
            )
        else:
            sug, why = "R3", "近效期投递/发送失败且无成功覆盖，默认归入人工确认重发候选"
        if tg in dup_keys and sug in ("R1", "R2", "R3"):
            sug, why = (
                "R6",
                why
                + "；且同(title,target)重复簇（n=%d），建议按簇合并一次性处置"
                % len(clusters[tg]),
            )
        per_item[it["id"]] = {"suggestion": sug, "why": why, "age_bucket": ab}
        counts[sug] += 1
    return per_item, counts


SUGGESTION_LABELS = {
    "R1": "可过期丢弃（已被今日成功发送覆盖）",
    "R2": "可过期归档（账龄>7d）",
    "R3": "重发候选（近效期，需人工确认）",
    "R4": "需人工核实送达真相",
    "R5": "需人工看（数据锚未就绪）",
    "R6": "按簇合并处置（重复项）",
}


# ---------- 主流程 ----------


def collect(root_override: str | None):
    """直调 api_notify 四个数据源函数 + payload，全只读。"""
    if root_override:
        os.environ["AIOS_ROOT"] = root_override
    if str(PORTAL_DIR) not in sys.path:
        sys.path.insert(0, str(PORTAL_DIR))
    import api_notify  # noqa: F401  # 导入即用（模块 import 副作用），不起服务不触网

    errors: list = []
    decisions = api_notify.decision_items(errors)
    flow = api_notify.flow_items(errors)
    deadlines = api_notify.deadline_items(errors)
    matrix, drills = api_notify.matrix_items(errors)
    payload = api_notify.notify_queue_payload()
    return (errors, decisions, flow, deadlines, matrix, drills, payload)


def analyze(args):
    now = datetime.now(CST)
    (errors, decisions, flow, deadlines, matrix, drills, payload) = collect(args.root)

    all_items = decisions + flow + deadlines + matrix
    blocked = [it for it in all_items if it.get("state") == "blocked"]
    blocked.sort(
        key=lambda it: (
            -(  # 新→旧
                (
                    parse_ts(it.get("created_at") or it.get("send_at"))
                    or datetime(1970, 1, 1, tzinfo=CST)
                ).timestamp()
            )
        )
    )

    # ---- 对账（G4）：真实全量 vs payload.states.blocked（截断前计数）----
    payload_blocked = int(payload.get("states", {}).get("blocked", -1))
    reconciled = payload_blocked == len(blocked)
    src_meta = payload.get("meta", {}).get("sources", {})

    # ---- ① 原因分布 ----
    reason_dist = Counter(reason_bucket(it) for it in blocked)

    # ---- ② 账龄分桶 ----
    age_dist = Counter(age_bucket(age_days(it, now)) for it in blocked)

    # ---- ③ 来源×kind 交叉 ----
    cross = Counter(
        (str(it.get("source") or "?"), str(it.get("kind") or "?")) for it in blocked
    )

    # ---- ④ 重复度：同(title,target) 与 同(title) 两视图 ----
    by_tt, by_t = defaultdict(list), defaultdict(list)
    for it in blocked:
        t, tg = str(it.get("title") or ""), str(it.get("target") or "")
        by_tt[(t, tg)].append(it)
        by_t[t].append(it)
    dup_tt = {k: v for k, v in by_tt.items() if len(v) >= 2}
    dup_t = {k: v for k, v in by_t.items() if len(v) >= 2}
    mergeable = sum(len(v) - 1 for v in dup_tt.values())

    # ---- 今日成功覆盖交叉（供 R1 依据）----
    covered_keys = {
        (str(it.get("kind") or ""), str(it.get("target") or ""))
        for it in payload.get("items", [])
        if it.get("state") == "auto" and str(it.get("id") or "").startswith("flowauto:")
    }
    per_item, sug_counts = build_suggestions(blocked, covered_keys)

    # ---- 结构化结果 ----
    result = {
        "generated_at": now.isoformat(timespec="seconds"),
        "aios_root": str(Path(os.environ.get("AIOS_ROOT", "C:/aios"))),
        "reconciliation": {
            "full_blocked_by_sources": len(blocked),
            "payload_states_blocked": payload_blocked,
            "match": reconciled,
            "payload_meta_sources": src_meta,
            "payload_truncated": payload.get("meta", {}).get("truncated"),
            "payload_errors": payload.get("errors", []),
        },
        "reason_distribution": dict(reason_dist.most_common()),
        "age_distribution": dict(age_dist),
        "cross_source_kind": {"%s | %s" % k: v for k, v in cross.most_common()},
        "duplicates": {
            "by_title_target": {
                "clusters": len(dup_tt),
                "items_in_clusters": sum(len(v) for v in dup_tt.values()),
                "mergeable_savings": mergeable,
                "top": [
                    {
                        "title": k[0],
                        "target": k[1],
                        "count": len(v),
                        "ids": [x["id"] for x in v],
                    }
                    for k, v in sorted(dup_tt.items(), key=lambda kv: -len(kv[1]))[:20]
                ],
            },
            "by_title": {
                "clusters": len(dup_t),
                "items_in_clusters": sum(len(v) for v in dup_t.values()),
                "top": [
                    {
                        "title": k,
                        "count": len(v),
                        "targets": sorted({x["target"] for x in v}),
                    }
                    for k, v in sorted(dup_t.items(), key=lambda kv: -len(kv[1]))[:20]
                ],
            },
        },
        "suggestion_counts": {
            "%s %s" % (k, SUGGESTION_LABELS[k]): v
            for k, v in sorted(sug_counts.items())
        },
        "source_errors": errors,
        "items": [
            {
                "id": it["id"],
                "source": it.get("source"),
                "kind": it.get("kind"),
                "target": it.get("target"),
                "title": it.get("title"),
                "created_at": it.get("created_at"),
                "age_days": (
                    round(age_days(it, now), 2)
                    if age_days(it, now) is not None
                    else None
                ),
                "age_bucket": per_item[it["id"]]["age_bucket"],
                "outcome": it.get("outcome"),
                "reason": it.get("reason"),
                "basis": it.get("basis"),
                "agent_source": it.get("agent_source"),
                "caller": it.get("caller"),
                "evidence_delivery": it.get("evidence_delivery"),
                "suggestion": per_item[it["id"]]["suggestion"],
                "suggestion_label": SUGGESTION_LABELS.get(
                    per_item[it["id"]]["suggestion"], ""
                ),
                "why": per_item[it["id"]]["why"],
            }
            for it in blocked
        ],
    }

    # ---- closed on failure 判定：blocked 能力源（决策DB/发送流水）全部
    # 缺失或不可读时，空报告不可冒充真实空态，显式失败提示 ----
    src_bad = [e for e in errors if ("不存在" in e or "读取失败" in e)]
    blocked_capable_missing = (
        not blocked
        and any("决策 DB" in e for e in src_bad)
        and any("发送流水" in e for e in src_bad)
    )
    if blocked_capable_missing:
        msg = (
            "closed on failure: blocked 能力源（决策DB/发送流水）全部缺失"
            "或不可读，零输入不可冒充真实空态——%s" % "; ".join(errors)
        )
        result["closed_on_failure"] = msg
        return result, False
    if not reconciled:
        result["closed_on_failure"] = (
            "closed on failure: 对账不一致——全量 blocked=%d vs "
            "payload.states.blocked=%d，拒绝出报告" % (len(blocked), payload_blocked)
        )
        return result, False
    result["closed_on_failure"] = ""
    return result, True


# ---------- 报告渲染 ----------


def _tbl(headers, rows):
    out = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render_md(r: dict) -> str:
    L = []
    ap = L.append
    ap("# 通知队列 blocked 存量分诊报告（%s）" % r["generated_at"][:10])
    ap("")
    ap("> 工位：FB-G · 只读分诊，未执行任何清理/重发/账本改动。")
    ap(
        "> 取数方式：离线 import `portal/api_notify.py` 的队列组装入口 "
        "`notify_queue_payload()` 与四个数据源函数（decision_items / "
        "flow_items / deadline_items），直调全量条目后按 state=blocked 过滤，"
        "**绕开 MAX_ITEMS=200 页面截断**；不起服务、不触网、全程只读。"
    )
    ap("> 数据根：`%s`" % r["aios_root"])
    ap("")
    ap("## 一、总量与对账（数字证据）")
    ap("")
    ap(
        _tbl(
            ["指标", "数值"],
            [
                [
                    "全量 blocked（四源直取过滤）",
                    r["reconciliation"]["full_blocked_by_sources"],
                ],
                [
                    "payload.states.blocked（截断前计数）",
                    r["reconciliation"]["payload_states_blocked"],
                ],
                [
                    "对账结果",
                    "一致 ✔"
                    if r["reconciliation"]["match"]
                    else "不一致 ✘（closed on failure）",
                ],
                ["payload 截断标志", r["reconciliation"]["payload_truncated"]],
                [
                    "payload 各源条数",
                    json.dumps(
                        r["reconciliation"]["payload_meta_sources"], ensure_ascii=False
                    ),
                ],
                [
                    "payload errors",
                    json.dumps(
                        r["reconciliation"]["payload_errors"], ensure_ascii=False
                    ),
                ],
            ],
        )
    )
    ap("")
    ap(
        "注：`/api/notify/queue` 页面实际只能展示 200 条且 review 优先截断，"
        "本报告为截断前真实全量。"
    )
    ap("")
    ap("## 二、原因分布（why 逐一归类）")
    ap("")
    rows = [
        [k, v, "%.1f%%" % (100.0 * v / len(r["items"]))]
        for k, v in r["reason_distribution"].items()
    ] or [["（空）", 0, "0%"]]
    ap(_tbl(["原因桶（来源·机器可读字段/原因模板）", "条数", "占比"], rows))
    ap("")
    ap("## 三、账龄分桶（按 created_at，兜底 send_at）")
    ap("")
    order = ["<1d", "1-3d", "3-7d", ">7d", "时间不可解析"]
    rows = [
        [b, r["age_distribution"].get(b, 0)]
        for b in order
        if r["age_distribution"].get(b)
    ]
    rows += [[k, v] for k, v in r["age_distribution"].items() if k not in order]
    ap(_tbl(["账龄桶", "条数"], rows or [["（空）", 0]]))
    ap("")
    ap(
        "口径注：decision_db 取决策创建时间；push_flow 取流水行 ts（失败发生时）；"
        "deadline_flag 取旗标 generated_at（看门狗周期重写，**反映旗标新鲜度而非"
        "事件起点**，其真实账龄应读 reason 内「连续未就绪 N 次」）。"
    )
    ap("")
    ap("## 四、来源 × kind 交叉分布")
    ap("")
    rows = [[k, v] for k, v in r["cross_source_kind"].items()] or [["（空）", 0]]
    ap(_tbl(["source | kind", "条数"], rows))
    ap("")
    ap("## 五、重复度与可合并项")
    ap("")
    d = r["duplicates"]["by_title_target"]
    ap(
        "同 (title, target) 重复簇：%d 簇，覆盖 %d 条，**可合并节省 %d 条**。"
        % (d["clusters"], d["items_in_clusters"], d["mergeable_savings"])
    )
    ap("")
    if d["top"]:
        ap(
            _tbl(
                ["title", "target", "条数", "成员 id（前6）"],
                [
                    [x["title"][:60], x["target"], x["count"], ", ".join(x["ids"][:6])]
                    for x in d["top"]
                ],
            )
        )
        ap("")
    dt = r["duplicates"]["by_title"]
    ap(
        "同 title 跨接收方：%d 簇（同内容多目标，如需广播去重可另行合并，"
        "本报告仅计数不并入可合并节省）。" % dt["clusters"]
    )
    if dt["top"]:
        ap("")
        ap(
            _tbl(
                ["title", "条数", "目标数"],
                [
                    [x["title"][:60], x["count"], len(x["targets"])]
                    for x in dt["top"][:10]
                ],
            )
        )
    ap("")
    ap("## 六、建议处置清单（每条建议均带依据，见 JSON 明细逐条 why 字段）")
    ap("")
    ap(
        _tbl(
            ["建议", "含义", "条数", "依据（锚定代码/账本事实）"],
            [
                [
                    "R1 过期丢弃",
                    "已被今日成功发送覆盖",
                    r["suggestion_counts"].get("R1 " + SUGGESTION_LABELS["R1"], 0),
                    "同(kind,target)今日 ok=1 live 留痕（auto 区今日聚合），失败行信息已被覆盖；push_flow blocked 仅近7天回看",
                ],
                [
                    "R2 过期归档",
                    "账龄>7d",
                    r["suggestion_counts"].get("R2 " + SUGGESTION_LABELS["R2"], 0),
                    "超出 push_flow 近7天回看窗口时效；队列只读无自动重发通道（api_notify 零决策权）",
                ],
                [
                    "R3 重发候选",
                    "近效期需人工确认",
                    r["suggestion_counts"].get("R3 " + SUGGESTION_LABELS["R3"], 0),
                    "dead_letter 投递凭证 + 近效期 + 今日无成功覆盖，发送意图明确",
                ],
                [
                    "R4 需人工核实",
                    "送达真相未知",
                    r["suggestion_counts"].get("R4 " + SUGGESTION_LABELS["R4"], 0),
                    "outcome=attempted/unknown_after_attempt，map_decision_state 诚实呈现不猜成功",
                ],
                [
                    "R5 需人工看",
                    "数据锚未就绪",
                    r["suggestion_counts"].get("R5 " + SUGGESTION_LABELS["R5"], 0),
                    "上游数据链问题，队列无法自愈；escalated 件优先",
                ],
                [
                    "R6 按簇合并",
                    "重复项合并处置",
                    r["suggestion_counts"].get("R6 " + SUGGESTION_LABELS["R6"], 0),
                    "同(title,target) n>=2，逐条重发属重复劳动",
                ],
            ],
        )
    )
    ap("")
    ap(
        "全量逐条明细（id/账龄/建议/依据）见 JSON：字段 "
        "`items[].suggestion / suggestion_label / why`。"
    )
    ap("")
    if r.get("source_errors"):
        ap("数据源读取注记（fail-visible 透出，不影响已取到的源）：")
        ap("")
        for e in r["source_errors"]:
            ap("- %s" % e)
        ap("")
    ap("## 七、治理选项（待老田拍板，本工位不擅自执行任何清理）")
    ap("")
    ap("### 方案 A：T+7 自动过期归档")
    ap("- 做法：blocked 条目账龄 >7 天且无后续成功覆盖 → 定期移入归档账本出队。")
    ap(
        "- 影响面：约 R2+超龄 R1/R6 条目出队；队列只读投影需新增归档过滤规则与归档账本；不删任何原始账。"
    )
    ap(
        "- 前置条件：新增归档账本与开关；对 deadline 类不适用（数据锚问题不随时间消失）。"
    )
    ap("")
    ap("### 方案 B：按簇批量重发放行")
    ap(
        "- 做法：同(title,target) 重复簇去重合并后，近效期 R3/R6 条目经人工确认批量重发一次。"
    )
    ap(
        "- 影响面：立即消化近效期积压；需占用发送通道配额，存在重复打扰风险（按簇去重可缓解）。"
    )
    ap("- 前置条件：复用现有 push 通道 + 白名单/配额闸门；重发前人工抽验内容时效。")
    ap("")
    ap("### 方案 C：维持人工分诊、只治增量")
    ap("- 做法：存量不动（仅在队列展示层加重复簇折叠与账龄标注），增量走既有闸门。")
    ap(
        "- 影响面：零风险零改动；blocked 存量继续占墙，页面 200 截断下尾部不可见问题仍在。"
    )
    ap("- 前置条件：接受存量可见性损失；建议至少先做展示层截断改进。")
    ap("")
    ap("---")
    ap("")
    ap("**待老田拍板。** 以上为只读分诊结论，未执行任何清理、重发或账本改动。")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="通知队列 blocked 存量只读分诊（离线，不起服务）"
    )
    ap.add_argument("--out", help="分诊报告 md 输出路径")
    ap.add_argument("--json", dest="json_path", help="全量明细 JSON 输出路径")
    ap.add_argument("--root", help="覆盖 AIOS_ROOT 数据根（默认 C:/aios）")
    args = ap.parse_args(argv)

    try:
        result, ok = analyze(args)
    except Exception as e:  # noqa: BLE001
        print("closed on failure: 分析器异常 %r" % e, file=sys.stderr)
        return 2

    if args.json_path:
        p = Path(args.json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print("JSON 明细已写: %s（%d 条 blocked）" % (p, len(result["items"])))
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_md(result), encoding="utf-8")
        print("报告已写: %s" % p)
    if not args.out and not args.json_path:
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != "items"},
                ensure_ascii=False,
                indent=1,
            )
        )

    if not ok:
        print(
            result.get("closed_on_failure") or "closed on failure: 未知",
            file=sys.stderr,
        )
        return 3
    rec = result["reconciliation"]
    print(
        "对账通过: 全量 blocked=%d == payload.states.blocked=%d "
        "(truncated=%s)"
        % (
            rec["full_blocked_by_sources"],
            rec["payload_states_blocked"],
            rec["payload_truncated"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
