# -*- coding: utf-8 -*-
"""曹操下属能力迭代体系（2026-10-29 WO-SEAT-ITER）。

把每个席位的能力当成**可迭代对象**：
  v1 基线 → 目标能力 → 学习材料 → 实践工单 → 评估门 → v2（写回历史）
真源：`agent_platform/config/seat_iterations/<SEAT>.json`（schema=seat_iteration/v1）
不虚报：评估 passed 必须有 evidence；AI 不代签导师意见。

用法：
  python -X utf8 tools/seat_iteration.py --init
  python -X utf8 tools/seat_iteration.py --status DEV7
  python -X utf8 tools/seat_iteration.py --check
  python -X utf8 tools/seat_iteration.py --json
  python -X utf8 tools/seat_iteration.py --out docs/dev/capability_iterations.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / "agent_platform" / "config" / "seat_iterations"
ORG = ROOT / "agent_platform" / "registry" / "org.json"
SCHEMA = "seat_iteration/v1"

# 每席迭代目标（人工策展：角色域 → 本轮要补的能力 → 实践载体 → 评估门）
TARGETS = {
    "ARCH1": ("研发计划拆解与验收标准",
              "总纲 §5/§6 + WBS 模板",
              "下一项目 WBS→工单→验收样例",
              "WBS 样例评审通过 + 计划可执行"),
    "DEV1": ("质量门与 code review 判据",
             "devtask 质量门 + AGENTS 红线",
             "评审 3 张工单并出 review 结论",
             "评审结论有据 + 0 假绿"),
    "DEV6": ("数据管道与可观测",
             "dev_metrics_daily + 指标口径",
             "P0-TRACE 单落地",
             "验收 rc0 + 指标可解释"),
    "DEV7": ("标准化交付（L1/L2 已启动）",
             "seat_learning DEV7 计划 L1–L4",
             "WO-P0-DEV7-WIKI + LS10 冒烟",
             "4 模块 + 2 实践单 + 导师 pass"),
    "DEV2": ("模型评估与降级策略",
             "model_tiers + fallback 链",
             "选 1 席做 fallback 演练",
             "演练记录 + 成本/延迟可解释"),
    "DEV3": ("策略数据契约与回测口径",
             "v1.1 白名单/字段契约",
             "v1.1 策略适配器（LV-02）",
             "单测 + 抽样一致 + 脱敏审计"),
    "DEV4": ("风控判据与 Go-No-Go 复核",
             "backtest_registry + 判据冻结",
             "v1.1 风控适配器（LV-03）",
             "单测 + Go-No-Go 口径确认"),
    "DEV5": ("前端设计 tokens 与组件规范",
             "tokens.css + unify_nav + 设计规范",
             "1 个页面组件化改造",
             "三件套 PASS + 无自绘色"),
    "DEV8": ("数据可视化与空错态",
             "作战屏 v1/v1.1 页面",
             "v1.1 真实数据前端渲染",
             "有/无真源两态 PASS"),
    "OPS1": ("部署/重启演练与五要素",
             "start_portal_api.ps1 + 重启规范",
             "一次受控重启演练（审批后）",
             "重启前后验证 + 回滚预案"),
    "DOC1": ("文档/索引自动化",
             "gen_docs_index + 文档规范",
             "v1.1 文档 + 索引刷新",
             "索引无漂移 + 链接可点"),
}
MENTORS = {"ARCH1": "A0", "DEV1": "ARCH1", "DEV5": "ARCH1",
           "DEV6": "DEV1", "DEV7": "DEV1", "DEV2": "DEV1", "OPS1": "DEV1",
           "DEV8": "DEV5", "DEV3": "DEV5", "DEV4": "DEV5", "DOC1": "DEV5"}
PRACTICE = {
    "DEV7": "WO-P0-DEV7-WIKI-20261027",
    "DEV1": "WO-P0-DEV1-MONDAY-20261027",
    "DEV5": "WO-P0-DEV5-EVALCASES-20261027",
    "DEV6": "WO-P0-DEV6-TRACE-20261027",
    "DEV8": "WO-P0-DEV8-METRICS-20261027",
}


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _profiles(root):
    org = _load(Path(root) / "agent_platform" / "registry" / "org.json", {})
    for d in (org or {}).get("departments") or []:
        if d.get("dept") == "dev":
            return {p.get("agent_id"): p for p in d.get("agent_profiles") or []
                    if isinstance(p, dict) and p.get("agent_id")}
    return {}


def _today():
    import time
    return time.strftime("%Y-%m-%d")


def _iter_path(root, seat):
    return Path(root) / "agent_platform" / "config" / "seat_iterations" / (seat + ".json")


def init(root=None, overwrite=False):
    root = Path(root or ROOT)
    profs = _profiles(root)
    made = []
    for seat, (target, material, practice, gate) in TARGETS.items():
        p = _iter_path(root, seat)
        if p.exists() and not overwrite:
            continue
        prof = profs.get(seat, {})
        attrs = prof.get("attributes") or {}
        data = {
            "schema": SCHEMA,
            "seat": seat,
            "cn_name": prof.get("cn_name") or "",
            "role_title": prof.get("role_title") or "",
            "discipline": prof.get("discipline") or "",
            "group": prof.get("group") or "",
            "grade": prof.get("grade") or "",
            "mentor": MENTORS.get(seat, ""),
            "version": 1,
            "baseline": {
                "specialties": list(attrs.get("specialties") or []),
                "certified": False,
                "level": "v1",
            },
            "iteration": {
                "id": "IT1",
                "start": _today(),
                "target_capability": target,
                "learning": {"material": material, "status": "planned",
                             "evidence": ""},
                "practice": {"tid": PRACTICE.get(seat, "待定（下一任务）"),
                             "acceptance": gate, "status": "planned",
                             "evidence": ""},
                "assessment": {"method": "导师评审 + 工单验收 + 证据",
                               "mentor": MENTORS.get(seat, ""),
                               "status": "planned", "evidence": ""},
            },
            "history": [],
            "updated_at": _today(),
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
        made.append(seat)
    return made


def load_all(root=None):
    root = Path(root or ROOT)
    out = []
    for seat in TARGETS:
        d = _load(_iter_path(root, seat), None)
        if d is not None:
            out.append(d)
    return out


def validate(data):
    errs = []
    if not isinstance(data, dict):
        return ["计划不是对象"]
    for k in ("schema", "seat", "version", "baseline", "iteration"):
        if k not in data:
            errs.append("缺字段 %s" % k)
    if data.get("schema") != SCHEMA:
        errs.append("schema 应为 %s" % SCHEMA)
    it = data.get("iteration") or {}
    for k in ("id", "target_capability", "learning", "practice", "assessment"):
        if k not in it:
            errs.append("iteration 缺 %s" % k)
    for part in ("learning", "practice", "assessment"):
        x = it.get(part) or {}
        if x.get("status") == "passed" and not str(x.get("evidence") or "").strip():
            errs.append("%s 标 passed 但无 evidence" % part)
    if it.get("assessment", {}).get("status") == "passed":
        if not str(it.get("assessment", {}).get("evidence") or "").strip():
            errs.append("assessment passed 无 evidence")
    return errs


def check(root=None):
    root = Path(root or ROOT)
    items = load_all(root)
    errs = []
    if len(items) != len(TARGETS):
        errs.append("迭代配置缺：%d/%d" % (len(items), len(TARGETS)))
    for d in items:
        for e in validate(d):
            errs.append("%s: %s" % (d.get("seat"), e))
    return errs


def render_md(root=None):
    items = load_all(root)
    lines = ["# 曹操下属能力迭代（每人一个迭代）", "",
             "| 席位 | 角色 | 域 | 版本 | 本轮目标能力 | 学习材料 | 实践载体 | 导师 | 状态 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for d in sorted(items, key=lambda x: x["seat"]):
        it = d["iteration"]
        lines.append("| %s %s | %s | %s | v%s | %s | %s | %s | %s | %s |"
                     % (d["seat"], d.get("cn_name", ""), d.get("role_title", ""),
                        d.get("discipline", ""), d.get("version"),
                        it.get("target_capability", ""),
                        (it.get("learning") or {}).get("material", ""),
                        (it.get("practice") or {}).get("tid", ""),
                        d.get("mentor", ""),
                        (it.get("assessment") or {}).get("status", "planned")))
    lines += ["", "> 规则：v1 基线 → 学习/实践/评估 → 导师 pass + 证据 → version+1；"
                  "AI 不代签、评估无证据不得标 passed。"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--status")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--root")
    args = ap.parse_args(argv)
    root = args.root or ROOT
    if args.init:
        made = init(root, args.overwrite)
        print("initialized:", ", ".join(made) or "all exist")
    if args.status:
        d = _load(_iter_path(root, args.status.upper()), None)
        if d is None:
            print("no iteration for", args.status)
            return 1
        print(json.dumps(d, ensure_ascii=False, indent=1))
    if args.json:
        print(json.dumps(load_all(root), ensure_ascii=False, indent=1))
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_md(root), encoding="utf-8")
        print("written", args.out)
    if args.check:
        errs = check(root)
        for e in errs:
            print("ERR", e)
        print("ITER_CHECK", "PASS" if not errs else "FAIL")
        return 0 if not errs else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
