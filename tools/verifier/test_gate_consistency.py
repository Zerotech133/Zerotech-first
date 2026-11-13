#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gate_consistency.py - 旁路校验器的变异检验与阴性对照

判据（项目铁律：测试要过变异检验）
    把被测逻辑改回「不检查」的旧行为，断言必须失败（KILLED）。
    不失败说明断言没走到那条分支，属于假通过。
    另设阴性对照：原样加载的变体行为必须与生产版逐字一致。

加载方式用 importlib.util.spec_from_file_location 真实加载变体文件，
不用 exec（exec 没有 __file__，会让 SELF_PATH 自检失真）。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate_consistency as prod  # noqa: E402

SRC = (HERE / "gate_consistency.py").read_text(encoding="utf-8")

# (名称, 唯一锚点, 替换后的旧行为, 期望消失的 category, 夹具构造函数)
MUTATIONS = [
    ("M1 取消自报上限一致性重算",
     "if abs(net - upper) > TOL_PCT:",
     "if False:",
     "SELF_REPORT_MISMATCH",
     lambda m: m.check_precheck(_bad_upper(), "X")),
    ("M2 取消 prev_median=0 兜底",
     'if med == 0 and sv == "ok":',
     "if False:",
     "COVERAGE_SKIPPED",
     lambda m: m.check_health(_bad_cover(), prod.D_TARGET, "X")),
    ("M3 取消目标日外部锚点",
     "if target != file_target:",
     "if False:",
     "TARGET_MISMATCH",
     lambda m: m.check_health(_bad_target(), prod.D_TARGET, "X")),
    ("M4 取消断供与无事件区分",
     "if mx and mx != file_target and sv in SET_OK_VERDICTS:",
     "if False:",
     "SILENT_DEGRADE",
     lambda m: m.check_health(_bad_lag(), prod.D_TARGET, "X")),
    ("M5 取消顶层参照系检查",
     "if univ and univ != file_target and top not in LAG_VERDICTS:",
     "if False:",
     "REFERENCE_FRAME",
     lambda m: m.check_health(_bad_ref(), prod.D_TARGET, "X")),
    ("M6 取消口径自证检查",
     'if not sc.get("reference"):',
     "if False:",
     "NO_SELFCHECK",
     lambda m: m.check_precheck(_bad_selfcheck(), "X")),
]


def _bad_upper() -> dict:
    d = prod._good_doc()
    d["cap"]["upper_bound_pct"] = 99.0
    return d


def _bad_cover() -> dict:
    h = prod._good_health()
    h["sets"][0]["prev_median"] = 0
    return h


def _bad_target() -> dict:
    h = prod._good_health()
    h["target"] = prod.D_PREV
    return h


def _bad_lag() -> dict:
    h = prod._good_health()
    h["sets"][0]["max_in_table"] = prod.D_PREV
    h["sets"][0]["verdict"] = "no_event"
    return h


def _bad_ref() -> dict:
    h = prod._good_health()
    h["universal_max"] = prod.D_PREV
    return h


def _bad_selfcheck() -> dict:
    d = prod._good_doc()
    d.pop("selfcheck")
    return d


def _load(src: str, name: str, td: Path):
    p = td / ("%s.py" % name)
    p.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, str(p))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _cats(mod, fn) -> set[str]:
    return {h["category"] for h in fn(mod)}


def main() -> int:
    rows = []
    with tempfile.TemporaryDirectory(prefix="gc_mut_") as _td:
        td = Path(_td)

        # 阴性对照：原样加载，行为必须与生产版一致
        ctrl = _load(SRC, "gc_ctrl", td)
        for name, _, _, _, fn in MUTATIONS:
            pc, cc = _cats(prod, fn), _cats(ctrl, fn)
            rows.append(("CTRL " + name, "identical", pc == cc,
                         "prod=%s ctrl=%s" % (sorted(pc), sorted(cc))))

        for i, (name, anchor, repl, want, fn) in enumerate(MUTATIONS):
            n = SRC.count(anchor)
            if n != 1:
                rows.append((name, "anchor", False, "锚点 count=%d（须为 1）" % n))
                continue
            mutated = SRC.replace(anchor, repl, 1)
            if repl not in mutated or anchor in mutated:
                rows.append((name, "anchor", False, "替换后新串校验失败"))
                continue
            mod = _load(mutated, "gc_mut_%d" % i, td)
            pc, mc = _cats(prod, fn), _cats(mod, fn)
            killed = (want in pc) and (want not in mc)
            rows.append((name, "KILLED" if killed else "SURVIVED", killed,
                         "prod=%s mut=%s" % (sorted(pc), sorted(mc))))

        # M7：把整个 G1 检查改成 no-op 早退，self-test 必须判定验证器失效
        noop_anchor = chr(10).join([
            "def check_precheck(doc: dict, subject: str) -> list[dict]:",
            "    hits: list[dict] = []",
        ])
        noop_repl = chr(10).join([
            "def check_precheck(doc: dict, subject: str) -> list[dict]:",
            "    return []  # [mutant] no-op: 什么都没检查",
            "    hits: list[dict] = []",
        ])
        n = SRC.count(noop_anchor)
        if n == 1:
            m = _load(SRC.replace(noop_anchor, noop_repl, 1), "gc_mut_noop", td)
            p_ok, m_ok = prod.self_test()["all_ok"], m.self_test()["all_ok"]
            rows.append(("M7 整体 no-op 必须被 self-test 抓到", "KILLED",
                         p_ok and not m_ok,
                         "prod_all_ok=%s mut_all_ok=%s" % (p_ok, m_ok)))
        else:
            rows.append(("M7 整体 no-op 必须被 self-test 抓到", "anchor", False,
                         "锚点 count=%d" % n))

    print("%-38s %-10s %s" % ("CASE", "RESULT", "EVIDENCE"))
    for name, status, ok, ev in rows:
        print("%-38s %-10s %s" % (name, status if ok else "SURVIVED", ev[:120]))

    n_ok = sum(1 for _, _, ok, _ in rows if ok)
    n_ctrl = sum(1 for r in rows if r[0].startswith("CTRL "))
    n_mut = len(rows) - n_ctrl
    print("\n合计 %d 项：通过 %d；阴性对照 %d 项；变异 %d 项" % (
        len(rows), n_ok, n_ctrl, n_mut))
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
