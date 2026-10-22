"""council_route.py — 议会厅工作流路由器（2026-10-22）。

流程（老田 23:28 定稿的工作流）：
  议事（议会厅三席位表态）→ 试工（一席位先行 N 步留痕）→ 监读（其余席位读文档与过程）
  → 定稿（老田第三方拍板）→ 分发（按职责面路由开工）→ 收口（互审+回收）

本工具实现「定稿→分发」的路由判定：
  - 依据 SEAT_CARD.json 的 L2 duty（职责面）+ 任务标签（阶段/领域）建议派工组合
  - 只产建议不直接派单（派单走 devtask new，由老田或归口面执行）
  - 诚实纪律：职责卡读不到/无法匹配时如实报，不硬派

用法：
    python council_route.py --proposal docs/plans/xxx.md   # 从提案文书提取并给路由建议
    python council_route.py --tags dev,quant --stage build  # 直接按标签路由
"""
from __future__ import annotations

import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))

# 职责面路由表（对齐协同方案 §三 与 SEAT_CARD）：
# 每类任务 → 主执行 + 协同 + 监读 + 归口（全部为建议，老田可改）
ROUTE_TABLE = {
    # 标签组合 → {lead: 主执行, assist: 协同, review: 监读, gate: 归口}
    "dev":      {"lead": "workbuddy", "assist": ["zhaoyun"], "review": ["zhuge"], "gate": "tianfeng"},
    "govern":   {"lead": "zhaoyun",  "assist": [], "review": ["tianfeng"], "gate": "laotian"},
    "design":   {"lead": "tianfeng", "assist": ["zhuge"], "review": ["zhaoyun"], "gate": "laotian"},
    "quant":    {"lead": "workbuddy", "assist": [], "review": ["zhuge", "tianfeng"], "gate": "laotian"},
    "intel":    {"lead": "workbuddy", "assist": ["zhaoyun"], "review": ["tianfeng"], "gate": "laotian"},
    "ops":      {"lead": "zhaoyun",  "assist": ["workbuddy"], "review": ["tianfeng"], "gate": "laotian"},
    "content":  {"lead": "workbuddy", "assist": ["zhuge"], "review": ["tianfeng"], "gate": "laotian"},
    "docs":     {"lead": "tianfeng", "assist": [], "review": ["zhaoyun"], "gate": "laotian"},
}

SEAT_CN = {"workbuddy": "WorkBuddy（施工面）", "zhaoyun": "赵云（治理面）",
           "zhuge": "诸葛亮（建议面）", "tianfeng": "田丰（归口面）", "laotian": "老田（决策面）"}

TAG_PAT = re.compile(r"tags[:：]\s*([a-z,\s]+)", re.I)


def load_seat_cards() -> dict:
    """读三席位职责卡真源（读不到如实报空，路由退化为标签表）。"""
    cards = {}
    homes = {
        "tianfeng": os.path.expanduser("~/.hermes/profiles/tianfeng"),
        "zhaoyun": os.path.expanduser("~/.hermes/profiles/zhaoyun"),
        "zhuge": os.path.join(REPO, "legion_agents", "zhuge", "workspace"),
    }
    for seat, home in homes.items():
        p = os.path.join(home, "SEAT_CARD.json")
        if os.path.isfile(p):
            try:
                cards[seat] = json.load(open(p, encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cards[seat] = None
        else:
            cards[seat] = None
    return cards


def extract_tags(proposal_path: str) -> list[str]:
    """从提案文书头部提取 tags: 行；没有则返回空（不猜）。"""
    try:
        with open(proposal_path, encoding="utf-8") as f:
            head = f.read(3000)
    except OSError:
        return []
    m = TAG_PAT.search(head)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def route(tags: list[str]) -> dict:
    """按标签合并路由建议：主执行取最高频 lead，其余并集。"""
    matched = [ROUTE_TABLE[t] for t in tags if t in ROUTE_TABLE]
    if not matched:
        return {"error": f"标签 {tags} 无路由匹配（可用：{sorted(ROUTE_TABLE)}）；不硬派，请补标签或人工指定"}
    lead_votes: dict[str, int] = {}
    for m in matched:
        lead_votes[m["lead"]] = lead_votes.get(m["lead"], 0) + 1
    lead = max(lead_votes, key=lead_votes.get)
    assist = sorted({a for m in matched for a in m["assist"] if a != lead})
    review = sorted({r for m in matched for r in m["review"]})
    gate = matched[-1]["gate"]
    return {"lead": lead, "assist": assist, "review": review, "gate": gate,
            "matched_tables": sorted({t for t in tags if t in ROUTE_TABLE}),
            "seats_cn": {k: SEAT_CN[k] for k in [lead] + assist + review + [gate] if k in SEAT_CN}}


def dispatch_plan(tags: list[str], ticket: str = "") -> dict:
    """产出完整分发计划：路由 + devtask 派单命令模板 + 会议厅登记项。"""
    r = route(tags)
    if "error" in r:
        return r
    plan = {
        "ticket": ticket or "（老田定稿时分配）",
        "workflow_stage": "定稿→分发",
        "route": r,
        "dispatch": {
            "lead_cmd": f'python tools/devtask.py new --desc "…" --owner {r["lead"]} --project （线）',
            "note": "派单由归口面（田丰）或老田执行；本工具只产建议不直接建单",
        },
        "monitoring": {
            "workbudd试工留痕": "worktree + git commit（过程可被监读席位回放）",
            "监读方式": "review 席位读工单 worktree diff 与提交流水",
            "回收": "合并后 devtask 守卫提示 worktree remove（S1 收工即收口）",
        },
        "gate": "分发完成≠验收完成：收口仍需 gate 席位复核 + 互审单（分歧回议会厅）",
    }
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="议会厅工作流路由器（只产建议）")
    ap.add_argument("--proposal", help="提案文书路径（提取 tags 行）")
    ap.add_argument("--tags", help="逗号分隔标签（dev/govern/design/quant/intel/ops/content/docs）")
    ap.add_argument("--ticket", default="", help="工单号（可后补）")
    args = ap.parse_args(argv)
    tags = []
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    elif args.proposal:
        tags = extract_tags(args.proposal)
        if not tags:
            print(f"[WARN] {args.proposal} 头部无 tags: 行——不猜，改用 --tags 指定")
    if not tags:
        ap.print_help()
        return 2
    cards = load_seat_cards()
    missing = [s for s, c in cards.items() if c is None]
    plan = dispatch_plan(tags, args.ticket)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if missing:
        print(f"[NOTE] 职责卡缺失席位：{missing}（路由已退化为标签表，仍可用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
