"""issues_store.py — OS 问题账本（问题生命周期管理：发现→认领→解决→清除归档）。

设计：
- issues.jsonl 一条一问题：id/severity/board/owner/status/raised/fixed/close_note
- severity: P1(阻断)/P2(伤害)/P3(改善)；status: open→doing→fixed→archived（archived=清除归档）
- 与议会厅联动：--from-council 把定稿任务反向登记；解决走 --fix id+note
- 智能体自运作：list --json 给 8798 端点；席位按 owner 领单
纪律：清除=archived（账本保留可溯），永不物理删除；每条带来源证据路径。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "data", "issues.jsonl")

_SEV = ("P1", "P2", "P3")
_ST = ("open", "doing", "fixed", "archived")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load(status: str | None = None, n: int = 200) -> list[dict]:
    if not os.path.isfile(LEDGER):
        return []
    rows = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda r: (r.get("severity", "P3"), r.get("raised", "")), reverse=False)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows[:n]


def next_id() -> str:
    return "OS-%s-%03d" % (time.strftime("%m%d"), len(load(n=9999)) + 1)


def add(title: str, severity: str = "P2", board: str = "OS", owner: str = "",
        evidence: str = "", note: str = "") -> dict:
    assert severity in _SEV, "severity must be P1/P2/P3"
    rid = next_id()
    rec = {"id": rid, "title": title[:80], "severity": severity, "board": board[:16],
           "owner": owner[:12] or "待认领", "status": "open",
           "raised": _now(), "evidence": evidence[:200], "note": note[:300],
           "history": [{"at": _now(), "ev": "raised"}]}
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def update(rid: str, status: str | None = None, owner: str | None = None,
           note: str = "") -> dict:
    rows = load(n=9999)
    hit = next((r for r in rows if r["id"] == rid), None)
    if not hit:
        raise KeyError("问题不存在：" + rid)
    if status:
        assert status in _ST, "status 非法"
        hit["status"] = status
    if owner:
        hit["owner"] = owner[:12]
    if note:
        hit["note"] = (hit.get("note", "") + " | " + note)[:300]
    hit["history"].append({"at": _now(), "ev": status or ("owner→" + hit["owner"]) or "note"})
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)
    return hit


def stats() -> dict:
    rows = load(n=9999)
    out = {"total": len(rows)}
    for s in _ST:
        out[s] = sum(1 for r in rows if r.get("status") == s)
    for p in _SEV:
        out[p + "_open"] = sum(1 for r in rows if r.get("severity") == p and r.get("status") in ("open", "doing"))
    return out


def seed_from_map() -> int:
    """把作战图(os_map)半成品问题池初始化入账——一次性种子。"""
    seeds = [
        ("intel_workbench 证据流断（E2E 在途），页面在数据链未闭环", "P2", "情报", "", "os_map 半成品①"),
        ("ide/sandbox/stack 与 dev 线两套视图，未合并研发工作台", "P2", "研发", "", "os_map 半成品②"),
        ("flow/perf/strong 数据源薄，留砍待裁定", "P3", "研发", "", "os_map 半成品③"),
        ("ledger/dataops 未接 gen_health_card 真数据", "P2", "审计", "", "os_map 半成品④"),
        ("案卷五屏空态未挂路由（E3 时机待定）", "P3", "情报", "", "os_map 半成品⑤"),
        ("os_entry v6/v8 旧评审稿在线，入口混乱", "P3", "驾驶舱", "", "os_map 半成品⑥"),
        ("home/index 旧首页与驾驶舱并存", "P3", "驾驶舱", "", "os_map 半成品⑦"),
        ("demo 族页面混现网（quant_v2_demo 等）", "P3", "驾驶舱", "", "os_map 半成品⑧"),
        ("政策/科技仅标题级，全文不可检索、无影响标注", "P2", "情报", "", "os_map 颗粒度"),
        ("商业关系仅名片级，无跟进事件流", "P2", "商业", "", "os_map 颗粒度"),
        ("内容生产无选题变更史与热点对标记录", "P3", "内容", "", "os_map 颗粒度"),
        ("天气仅当前值，无3日预报与出行联动", "P3", "环境", "", "os_map 颗粒度"),
        ("辩论轮次未结构化（现为文本流）", "P3", "会议", "", "os_map 颗粒度"),
    ]
    known = {r["title"] for r in load(n=9999)}
    n = 0
    for t, sev, board, owner, ev in seeds:
        if t not in known:
            add(t, sev, board, owner, ev)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", help='JSON {"title","severity","board","owner","evidence"}')
    ap.add_argument("--update", help='JSON {"id","status","owner","note"}')
    ap.add_argument("--seed-map", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.seed_map:
        print("seeded:", seed_from_map())
        return 0
    if a.add:
        print(json.dumps(add(**json.loads(a.add)), ensure_ascii=False))
        return 0
    if a.update:
        print(json.dumps(update(**json.loads(a.update)), ensure_ascii=False))
        return 0
    if a.json:
        print(json.dumps({"stats": stats(), "open": load("open"), "doing": load("doing"),
                          "fixed": load("fixed", n=20)}, ensure_ascii=False))
        return 0
    print(json.dumps(stats(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
