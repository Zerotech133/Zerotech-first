# -*- coding: utf-8 -*-
"""知识图谱 CI 门禁（C2）：基于 ontology.gate() 的阻断器。

把 B1 的"漂移观测面板"升级为可裁决、可阻断的 CI 门禁：
  · 观测（validate/snapshot）→ 裁决（gate）→ 阻断（exit_code 0/1）
  · 支持落 JSON / JUnit XML 报告，供 CI 平台（GitHub Actions / GitLab CI /
    Jenkins / 本地 pre-commit）直接消费
  · --strict 档：WARN 也按 FAIL（零容忍，用于关键发布分支）

退出码约定（CI 标准）：
  0 = PASS（无 FAIL 级规则）
  1 = FAIL（存在 FAIL 级规则，或 Neo4j 不可达，或 --strict 下存在 WARN）

依赖：ontology.py（受治理 TBox + gate 裁决）、kg_schema.py（Neo4j 凭据）。

用法：
  python kg_gate.py                         # 跑门禁，打印报告，按结果退出
  python kg_gate.py --report gate.json      # 额外落 JSON 报告
  python kg_gate.py --junit gate.xml        # 落 JUnit XML（CI 消费）
  python kg_gate.py --strict                # WARN 也按 FAIL（更严）
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ontology as O  # noqa: E402

_LEVEL_MARK = {"PASS": "OK ", "WARN": "!! ", "FAIL": "XX "}
_LEVEL_WORD = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}


def _print_report(g):
    verdict = "PASS" if g["passed"] else "FAIL"
    bar = "=" * 62
    print(bar)
    print("KG Gate  TBox v%s   裁决=%s   exit=%d"
          % (g.get("tbox_version"), verdict, g["exit_code"]))
    print("checked_at=%s" % g.get("checked_at"))
    print("-" * 62)
    for r in g["rules"]:
        mark = _LEVEL_MARK.get(r["level"], "?  ")
        print("  [%s] %-16s %-4s actual=%-12s thr=%s"
              % (mark, r["id"], _LEVEL_WORD.get(r["level"], r["level"]),
                 str(r["actual"]), r["threshold"]))
        print("          %s" % r["msg"])
    print("-" * 62)
    print(bar)


def _write_json(path, g):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=2)


def _write_junit(path, g):
    """JUnit XML：每条规则一个 testcase；FAIL -> <failure>，WARN -> <system-out>。"""
    fails = sum(1 for r in g["rules"] if r["level"] == "FAIL")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<testsuite name="kg_gate" tests="%d" failures="%d">'
                 % (len(g["rules"]), fails))
    for r in g["rules"]:
        lines.append('  <testcase name="%s" classname="kg_gate">' % r["id"])
        if r["level"] == "FAIL":
            lines.append('    <failure message="%s">actual=%s threshold=%s</failure>'
                         % (r["msg"], r["actual"], r["threshold"]))
        elif r["level"] == "WARN":
            lines.append('    <system-out>WARN actual=%s threshold=%s</system-out>'
                         % (r["actual"], r["threshold"]))
        lines.append('  </testcase>')
    lines.append('</testsuite>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description="知识图谱 CI 门禁（C2）")
    ap.add_argument("--report", help="额外落 JSON 报告路径")
    ap.add_argument("--junit", help="落 JUnit XML 报告路径（CI 消费）")
    ap.add_argument("--strict", action="store_true",
                    help="WARN 也按 FAIL（更严，用于关键发布分支）")
    a = ap.parse_args(argv)

    g = O.gate()

    if a.strict:
        # strict：存在 WARN 也降级为不通过（保留原始 level 供报告）
        has_warn = any(r["level"] == "WARN" for r in g["rules"])
        if has_warn and g["passed"]:
            # 仅当原本 passed（无 FAIL）时，WARN 才决定降级
            g["passed"] = False
            g["exit_code"] = 1

    _print_report(g)
    if a.report:
        _write_json(a.report, g)
        print("JSON 报告已写：%s" % a.report)
    if a.junit:
        _write_junit(a.junit, g)
        print("JUnit 报告已写：%s" % a.junit)
    return g["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
