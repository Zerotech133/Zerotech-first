# -*- coding: utf-8 -*-
"""三件验证器工具自身的变异检验：把被测逻辑改回旧行为，断言必须失败。

按项目铁律：
    · 单锚点替换前断言 count==1，替换后断言新串存在。
    · 多行补丁一律落盘成 .py 再执行，不走 heredoc。
    · 断言通过前先确认它走的是预期分支；不失败 = 没测到那条分支。
    · 变异体只在 tempdir 加载，绝不写回生产文件。

覆盖 6 条：
    M1 契约缺类不拦        -> 必须仍报 INCOMPLETE 才对（漏拦 = KILLED 失败）
    M2 shortcut 不记录      -> no-op 假通过被吞掉
    M3 probe 命中恒 False   -> 捷径探测全部变「无命中」
    M4 registry 去掉幂等    -> 重复跑新增行
    M5 隔离判定恒 False     -> v1 批次被放进训练集
    M6 无事件也强行挂账     -> 凭空开一天流程账

阴性对照：未触发变异的正常输入，生产版与变异版必须逐项一致（不误伤）。

用法：python tools/verifier/test_verifier_tools.py   （也可被 pytest 收集）
退出码：0 全部 KILLED / 2 有变异存活或阴性对照不一致
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import verifier_contract as vc  # noqa: E402
import shortcut_probe as sp  # noqa: E402
import verifier_registry as vr  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="vc_mut_"))
_N = [0]


def _load(path: Path, name: str):
    _N[0] += 1
    spec = importlib.util.spec_from_file_location("%s_%d" % (name, _N[0]), str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mutate(src_path: Path, old: str, new: str, tag: str) -> Path:
    src = src_path.read_text(encoding="utf-8")
    assert src.count(old) == 1, "%s 锚点 count=%d" % (tag, src.count(old))
    mutated = src.replace(old, new, 1)
    assert new in mutated, "%s 替换后新串不存在" % tag
    p = _TMP / ("%s.py" % tag)
    p.write_text(mutated, encoding="utf-8", newline="\n")
    return p


def _contract_with_only_oracle(mod) -> dict:
    g = mod.build_guards()[0]
    c = mod.Contract(g)
    c.cases = [x for x in c.cases if x.kind == "oracle"]
    return c.verify()


def _probe_hits(mod) -> int:
    return len([r for r in mod.probe_g0_shortcuts() if r["hit"]])


def _idempotence(mod, tag: str) -> int:
    p = _TMP / ("idem_%s.jsonl" % tag)
    mod.REGISTRY = p
    rec = {"guard_id": "X", "rules_version": "v", "exec_day": "d"}
    mod._append_jsonl(p, rec, ("guard_id", "rules_version", "exec_day"))
    mod._append_jsonl(p, rec, ("guard_id", "rules_version", "exec_day"))
    return len(mod._read_jsonl(p))


def _quarantine(mod, tmp: Path) -> bool:
    p = tmp / "q.jsonl"
    mod.QUARANTINE = p
    batch = {"quarantine_id": "v1|20261113|x", "rules_version": "v1",
             "exec_day": "20261113", "scope": "whole_batch",
             "training_usable": False}
    mod._append_jsonl(p, batch, ("quarantine_id",))
    return mod.is_quarantined("v1", "20261113")


def _attach_status(mod) -> str:
    try:
        return str(mod.attach_to_event("20261113", apply=False).get("status"))
    except Exception as exc:  # noqa: BLE001 变异体崩溃也算「行为不同」
        return "CRASH:%s" % type(exc).__name__


def test_all_mutations_killed():
    cases = []

    # M1：契约缺类检查被绕过
    old = '        if missing:\n            verdict = "INCOMPLETE"'
    new = '        if False and missing:\n            verdict = "INCOMPLETE"'
    m1 = _load(_mutate(vc.HERE / "verifier_contract.py", old, new, "m1"), "vc_c")
    prod = _contract_with_only_oracle(vc)
    mut = _contract_with_only_oracle(m1)
    cases.append(("M1 契约缺类不拦", prod["verdict"], mut["verdict"],
                  prod["verdict"] == "INCOMPLETE" and mut["verdict"] != "INCOMPLETE"))

    # M2：no-op 假通过不再记为 shortcut
    old = '            shortcut = (c.kind in ("no_op", "unfinished") and passed is True)'
    new = '            shortcut = False'
    m2 = _load(_mutate(vc.HERE / "verifier_contract.py", old, new, "m2"), "vc_c")
    g0 = vc.build_guards()[0]
    prod = vc.Contract(g0).verify()
    mut = m2.Contract(m2.build_guards()[0]).verify()
    cases.append(("M2 shortcut 不记录", len(prod["shortcuts"]), len(mut["shortcuts"]),
                  len(prod["shortcuts"]) > 0 and len(mut["shortcuts"]) == 0
                  and prod["no_op_survived"] and not mut["shortcuts"]))

    # M3：捷径命中恒 False
    old = "            hit = bool(passed)"
    new = "            hit = False"
    m3 = _load(_mutate(sp.HERE / "shortcut_probe.py", old, new, "m3"), "sp")
    prod, mut = _probe_hits(sp), _probe_hits(m3)
    cases.append(("M3 probe 命中恒 False", prod, mut, prod >= 3 and mut == 0))

    # M4：登记账去掉幂等
    old = "    for r in rows:\n        if tuple(r.get(k) for k in key_fields) == key:\n            return \"REPLAYED\"\n"
    new = ""
    m4 = _load(_mutate(vr.HERE / "verifier_registry.py", old, new, "m4"), "vr")
    prod, mut = _idempotence(vr, "prod"), _idempotence(m4, "mut")
    cases.append(("M4 registry 去掉幂等", prod, mut, prod == 1 and mut == 2))

    # M5：整批隔离恒不排除
    old = "                return True\n    return False"
    new = "                return False\n    return False"
    m5 = _load(_mutate(vr.HERE / "verifier_registry.py", old, new, "m5"), "vr")
    prod = _quarantine(vr, _TMP / "a")
    mut = _quarantine(m5, _TMP / "b")
    cases.append(("M5 隔离判定恒 False", prod, mut, prod is True and mut is False))

    # M6：无事件也强行挂账
    old = "    if not ev:\n        return {"
    new = "    if False and not ev:\n        return {"
    m6 = _load(_mutate(vr.HERE / "verifier_registry.py", old, new, "m6"), "vr")
    prod, mut = _attach_status(vr), _attach_status(m6)
    cases.append(("M6 无事件强行挂账", prod, mut,
                  prod == "NO_EVENT_FOR_DAY" and mut != "NO_EVENT_FOR_DAY"))

    # 阴性对照：未触发变异的输入，生产版与变异版必须一致
    neg = [
        ("N1 v2 不被误隔离", vr.is_quarantined("v2", "20261113"),
         m5.is_quarantined("v2", "20261113")),
        ("N2 v1 其他日不受整批隔离", vr.is_quarantined("v1", "20261112"),
         m5.is_quarantined("v1", "20261112")),
        ("N3 阴性对照 probe 仍未命中",
         [r["hit"] for r in sp.probe_g0_shortcuts() if not r["expected_hit"]],
         [r["hit"] for r in m3.probe_g0_shortcuts() if not r["expected_hit"]]),
    ]
    for name, a, b in neg:
        assert a == b, "阴性对照不一致（误伤）：%s prod=%r mut=%r" % (name, a, b)

    killed = 0
    for name, a, b, ok in cases:
        print("%-28s prod=%-24s mut=%-24s %s" % (
            name, str(a)[:24], str(b)[:24], "KILLED" if ok else "*** SURVIVED ***"))
        killed += int(ok)
    print("\n阴性对照 %d/%d 一致；变异 %d/%d KILLED"
          % (len(neg), len(neg), killed, len(cases)))
    assert killed == len(cases), "有变异存活：断言没走到预期分支"
    return killed


def main() -> int:
    try:
        killed = test_all_mutations_killed()
        print("MUTATION_RESULT %d/6 KILLED" % killed)
        return 0
    finally:
        shutil.rmtree(str(_TMP), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
