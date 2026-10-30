# -*- coding: utf-8 -*-
"""研究院课题到 Legion Worker 工单的受控编排器。"""
import argparse
import json
import re
import urllib.request
from pathlib import Path

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

ROOT = Path(__file__).resolve().parent.parent
TOPICS = ROOT / "agent_platform" / "data" / "academy" / "research_topics.json"

STAGES = ["intake", "hypothesis", "evidence", "validation", "review", "joint_review", "decision"]


def load_topics():
    data = json.loads(TOPICS.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("topics", [])


def current_plan(topic):
    stage = topic.get("status", "intake")
    for row in topic.get("worker_plan", {}).get("stages", []):
        if row.get("stage") == stage:
            return row
    return None


def build_payload(topic):
    row = current_plan(topic)
    if not row:
        raise ValueError("课题当前阶段无 Worker 计划: %s" % topic.get("status"))
    tid = "ACA-%s-%s" % (topic["topic_id"].replace("-", ""), topic["status"])
    tid = re.sub(r"[^A-Za-z0-9_-]", "_", tid)[:40]
    # devtask 的 --project 校验要求注册项目线；研究院编排器默认不附加项目线，
    # 由 Legion API 的 general 线承接，避免把课题阶段和研发 worktree 强耦合。
    return {
        "tid": tid,
        "agent": row["agent"],
        "desc": "%s：%s（课题 %s）" % (row["stage"], row["task"], topic["title"]),
        "project": "general",
        "worker": True,
        "acceptance": [
            "产出文件存在且非空",
            "结论引用输入事实",
            "列出限制和后续动作",
            "执行过程有 heartbeat 记录",
        ],
        "topic_id": topic["topic_id"],
        "stage": row["stage"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="AC-20261017-LCS1")
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--dispatch", action="store_true")
    args = ap.parse_args()
    topic = next((x for x in load_topics() if x.get("topic_id") == args.topic), None)
    if not topic:
        raise SystemExit("课题不存在: %s" % args.topic)
    payload = build_payload(topic)
    if args.print or not args.dispatch:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.dispatch:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:8096/api/legion/dispatch", data=body, headers={"Content-Type": "application/json"})
        with safe_urlopen(req, timeout=30) as response:
            print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
