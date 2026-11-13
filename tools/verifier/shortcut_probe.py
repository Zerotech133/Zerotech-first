# -*- coding: utf-8 -*-
"""shortcut_probe/v1 —— 门禁捷径离线探测（solver 角色）。

对应智谱 GLM-5.3 的「求解器轨迹发现并封堵奖励捷径」：让一个探针专门去找
「不改决策也能让验证器给分」的路径。这里不训练、不改策略，只做离线判定。

与 verifier_contract 的分工：
    verifier_contract  检查守卫「有没有三类夹具、夹具会不会假通过」（契约层）
    shortcut_probe     主动枚举「决策没变但门禁变绿」的路径（搜索层）

硬边界（本轮授权范围）：
    · 只读：不写任何生产文件、不写业务账本、不写账户/流水/抑制令。
    · 零外发：全程无网络调用、无 subprocess 外联、无 LLM。
    · 临时夹具只在 tempfile 目录建 parquet，随用随删。
    · 唯一落盘是 --json-out 指定的回执，默认仓外 deliverables/verifier/。

每条 probe 必须声明 decision_changed=False。若决策真的变好而门禁变绿，
那是正确行为，不算捷径；只有「决策完全没动、门禁却放行」才记 hit=True。
阴性对照（expected_hit=False）用于证明探针本身有判别力，不是逢输入必报命中。

用法：
    python tools/verifier/shortcut_probe.py --json-out <path>
退出码：0 探测完成（有命中也返回 0，命中是发现不是故障；只对探测异常返回 3）
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from verifier_contract import (  # noqa: E402
    DH_PATH, EP_PATH, EXEC_DAY, PREV_DAY, ROOT,
    _load, _valid_doc, _write_df,
)

RULES_VERSION = "shortcut_probe/v1"

# 命中分类：决定后续封哪个洞，不把「逻辑漏洞」和「配置可改」混成一类。
CATEGORY = {
    "SP-G0-01": "reference_frame",          # 用被测对象当参照系
    "SP-G0-02": "coverage_gap",             # 无历史中位数时覆盖率整段跳过
    "SP-G0-03": "silently_degraded",        # 上游断供被说成「当日无事件」
    "SP-G0-04": "mutable_gate_config",      # 判据常量可被外部放宽
    "SP-G0-05": "negative_control",
    "SP-G0-06": "negative_control",
    "SP-G1-01": "self_reported_field",      # 自报上限无外部校验
    "SP-G1-02": "movable_threshold",        # 放弃线可自由下调
    "SP-G1-03": "warn_only",                # 关键项只给 warn 不阻断
    "SP-G1-04": "self_reported_field",
    "SP-G1-05": "negative_control",
    "SP-G1-06": "negative_control",
}


def _g0_world(pd, d: Path, per_day: dict):
    _write_df(d, "daily_delta.parquet", "trade_date", per_day, pd)


def probe_g0_shortcuts() -> list[dict]:
    import pandas as pd

    dh = _load(DH_PATH, "sp_data_health")
    t = dict(id="daily", name="日线行情", use="捷径探测",
             file="daily_delta.parquet", dcol="trade_date", kind="daily")

    def decide(per_day: dict, target: str, cover: float | None = None,
               event: bool = False, daily_lag: bool = False,
               fake_mtime: bool = False) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="sp_g0_") as td:
            d = Path(td)
            _g0_world(pd, d, per_day)
            if fake_mtime:
                import os
                import time
                now = time.time()
                os.utime(str(d / "daily_delta.parquet"), (now, now))
            dh.DATA = str(d)
            old = dh.COVER_MIN_RATIO
            if cover is not None:
                dh.COVER_MIN_RATIO = cover
            tt = dict(t)
            if event:
                tt["kind"] = "event"
            try:
                it = dh.inspect_one(tt, target, daily_lag)
            finally:
                dh.COVER_MIN_RATIO = old
            return bool(it["ok"]), "verdict=%s n=%d med=%d max=%s" % (
                it["verdict"], it["n_at_target"], it["prev_median"],
                it["max_in_table"])

    older = "".join(chr(c) for c in (50, 48, 50, 54, 49, 49, 48, 57))
    probes = [
        dict(id="SP-G0-01", name="目标日回退为表内最新日（用被测对象当参照系）",
             patch="target := max(table.date)，数据实际停在上一日",
             fn=lambda: decide({PREV_DAY: 100}, PREV_DAY),
             expected_hit=True),
        dict(id="SP-G0-02", name="目标日仅 1 行且无历史中位数",
             patch="prev_median=0 使覆盖率检查整段跳过",
             fn=lambda: decide({EXEC_DAY: 1}, EXEC_DAY),
             expected_hit=True),
        dict(id="SP-G0-03", name="事件表落后被判「当日无事件」",
             patch="daily_all_lag=False 时 event 表 max<target 直接 ok",
             fn=lambda: decide({PREV_DAY: 100}, EXEC_DAY, event=True),
             expected_hit=True),
        dict(id="SP-G0-04", name="调低覆盖率阈值让不足变达标",
             patch="COVER_MIN_RATIO := 0（放宽闸门，不改任何数据）",
             fn=lambda: decide({older: 100, PREV_DAY: 100, EXEC_DAY: 3},
                               EXEC_DAY, cover=0.0),
             expected_hit=True),
        dict(id="SP-G0-05", name="阴性对照：外部给定目标日 + 真零采集",
             patch="target=EXEC_DAY 且表内最新仍为 PREV_DAY",
             fn=lambda: decide({PREV_DAY: 100}, EXEC_DAY),
             expected_hit=False),
        dict(id="SP-G0-06", name="阴性对照：mtime 伪装成今天但内容停在旧日",
             patch="os.utime 把文件 mtime 改成当前时刻，表内日期列不动",
             fn=lambda: decide({PREV_DAY: 100}, EXEC_DAY, fake_mtime=True),
             expected_hit=False),
    ]
    return _run(probes, "G0")


def probe_g1_shortcuts() -> list[dict]:
    ep = _load(EP_PATH, "sp_experiment_precheck")

    def verdict(doc: dict) -> str:
        return ep.verdict_of(doc)[0]

    def make(**over):
        d = _valid_doc()
        for k, v in over.items():
            if k == "cap.upper_bound_pct":
                d["cap"]["upper_bound_pct"] = v
            elif k == "cap.spread_gross_pct":
                d["cap"]["inputs"]["spread_gross_pct"] = v
            elif k == "abandon.threshold_pct":
                d["abandon"]["threshold_pct"] = v
            elif k == "cap.method":
                d["cap"]["method"] = v
            else:
                raise KeyError(k)
        return d

    probes = [
        dict(id="SP-G1-01", name="自报上限与底层 spread 矛盾",
             patch="upper_bound_pct := 99，spread_gross_pct 仍是 -5",
             fn=lambda: (verdict(make(**{"cap.upper_bound_pct": 99.0,
                                         "cap.spread_gross_pct": -5.0})) == "pass",
                         "verdict=%s" % verdict(make(**{
                             "cap.upper_bound_pct": 99.0,
                             "cap.spread_gross_pct": -5.0}))),
             expected_hit=True),
        dict(id="SP-G1-02", name="只调放弃线让本该劝退的单子放行",
             patch="upper=-0.041 不变，threshold_pct := -10",
             fn=lambda: (verdict(make(**{"cap.upper_bound_pct": -0.041,
                                         "abandon.threshold_pct": -10.0})) == "pass",
                         "verdict=%s" % verdict(make(**{
                             "cap.upper_bound_pct": -0.041,
                             "abandon.threshold_pct": -10.0}))),
             expected_hit=True),
        dict(id="SP-G1-03", name="口径自证与成本敏感性全缺，仅 warn",
             patch="删除 selfcheck / cost_sensitivity / attempts",
             fn=lambda: _g1_missing(ep),
             expected_hit=True),
        dict(id="SP-G1-04", name="底层 spread 改成 -99，自报上限不动",
             patch="spread_gross_pct := -99，upper_bound_pct 仍 0.371",
             fn=lambda: (verdict(make(**{"cap.spread_gross_pct": -99.0})) == "pass",
                         "verdict=%s" % verdict(make(**{
                             "cap.spread_gross_pct": -99.0}))),
             expected_hit=True),
        dict(id="SP-G1-05", name="阴性对照：删掉 if_false 应判不完整",
             patch="falsify.if_false := []",
             fn=lambda: _g1_drop(ep, "falsify", "if_false"),
             expected_hit=False),
        dict(id="SP-G1-06", name="阴性对照：标了校正却不给方法应硬拦",
             patch="attempts.correction.applied := True, method 仍 none",
             fn=lambda: _g1_bad_correction(ep),
             expected_hit=False),
    ]
    return _run(probes, "G1")


def _g1_missing(ep):
    d = _valid_doc()
    d.pop("selfcheck", None)
    d.pop("cost_sensitivity", None)
    d.pop("attempts", None)
    v, msg = ep.verdict_of(d)
    errors, warns = ep.check(d)
    return v == "pass", "verdict=%s errors=%d warns=%d | %s" % (
        v, len(errors), len(warns), msg[:80])


def _g1_drop(ep, sec, key):
    d = _valid_doc()
    d[sec][key] = []
    v, msg = ep.verdict_of(d)
    return v == "pass", "verdict=%s | %s" % (v, msg[:80])


def _g1_bad_correction(ep):
    d = _valid_doc()
    d["attempts"]["total_trials"] = 6
    d["attempts"]["correction"] = {"applied": True, "method": "none"}
    v, msg = ep.verdict_of(d)
    errors, _ = ep.check(d)
    return v == "pass", "verdict=%s errors=%d | %s" % (v, len(errors), msg[:80])


def _run(probes: list[dict], gate: str) -> list[dict]:
    out = []
    for p in probes:
        try:
            passed, evidence = p["fn"]()
            hit = bool(passed)
            err = ""
        except Exception as exc:  # noqa: BLE001
            passed, hit, evidence, err = False, False, "", "%s: %s" % (
                type(exc).__name__, exc)
        out.append({
            "id": p["id"], "gate": gate, "name": p["name"],
            "category": CATEGORY.get(p["id"], "unclassified"),
            "decision_changed": False,
            "input_patch": p["patch"],
            "gate_passed": bool(passed),
            "hit": hit,
            "expected_hit": p["expected_hit"],
            "control_ok": (hit == p["expected_hit"]),
            "evidence": evidence,
            "probe_error": err,
            "reproduce": "python tools/verifier/shortcut_probe.py --only %s" % p["id"],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="门禁捷径离线探测")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--only", default=None, help="只跑指定 probe id（复现用）")
    args = ap.parse_args()

    rows = probe_g0_shortcuts() + probe_g1_shortcuts()
    if args.only:
        rows = [r for r in rows if r["id"] == args.only]
        if not rows:
            print("未知 probe id：%s" % args.only)
            return 3
    errors = [r for r in rows if r["probe_error"]]
    hits = [r for r in rows if r["hit"]]
    controls = [r for r in rows if not r["expected_hit"]]
    positives = [r for r in rows if r["expected_hit"]]
    rep = {
        "schema": "shortcut_probe_report/v1",
        "rules_version": RULES_VERSION,
        "exec_day": EXEC_DAY,
        "boundaries": {"read_only": True, "network": False,
                       "business_writes": False, "subprocess": False},
        "n_probes": len(rows),
        "n_hit": len(hits),
        "n_negative_controls": len(controls),
        "n_control_ok": len([r for r in controls if r["control_ok"]]),
        "n_positive_expected": len(positives),
        "n_positive_hit": len([r for r in positives if r["hit"]]),
        "n_probe_error": len(errors),
        "hit_ids": [r["id"] for r in hits],
        "probes": rows,
    }
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("%-12s %-6s %-8s %-8s %s" % ("PROBE", "GATE", "命中", "对照", "说明"))
    for r in rows:
        print("%-12s %-6s %-8s %-8s %s" % (
            r["id"], r["gate"], "YES" if r["hit"] else "no",
            ("OK" if r["control_ok"] else "BAD") if not r["expected_hit"] else "-",
            r["name"]))
    print("\n共 %d 条捷径路径：命中 %d 条；阴性对照 %d 条中 %d 条按预期未命中；"
          "探测异常 %d 条" % (
              rep["n_probes"], rep["n_hit"], rep["n_negative_controls"],
              rep["n_control_ok"], rep["n_probe_error"]))
    print("命中即发现，不是故障；本轮换 0 个生产字节、0 次外发。")
    return 3 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
