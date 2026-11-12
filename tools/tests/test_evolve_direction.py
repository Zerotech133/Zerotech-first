"""方向防线：参数类只记账不自动提建议；未登记方向默认按参数类处理。

必须过变异检验 —— 当前事件账只有 1 天，quorum=3 本身就会挡住任何 pattern，
只测「没有新建议」证明不了拒提逻辑走了预期分支。所以这里构造 3 个独立
data_day 把 quorum 喂饱，再分别看登记类、未登记类、pipeline 类的去向。
"""

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
SRC = REPO / "tools" / "preopen" / "evolve.py"

# 日期字面量一律码点构造：Write/Edit 通道会把数字串味（跨会话铁律）。
_DAY_PREFIX = "".join(chr(c) for c in (50, 48, 50, 54, 49, 49, 49))


def _days(n):
    return [_DAY_PREFIX + chr(49 + k) for k in range(n)]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)          # 真实加载，不用 exec（__file__ 要存在）
    return mod


def _sandbox(mod, pattern, n=3):
    tmp = Path(tempfile.mkdtemp(prefix="zb_evolve_"))
    ev, pp, ex = tmp / "events", tmp / "props", tmp / "exp"
    for d in (ev, pp, ex):
        d.mkdir(parents=True)
    for day in _days(n):
        doc = {"data_day": day, "candidates": [],
               "drift": [{"id": pattern, "title": "t", "evidence": "e",
                          "so_what": "s", "next": "n", "exec_day": day}]}
        (ev / (day + ".json")).write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    mod.EV_DIR, mod.PP_DIR, mod.EXP_DIR = str(ev), str(pp), str(ex)
    return tmp


def _run(mod, pattern, n=3):
    tmp = _sandbox(mod, pattern, n)
    try:
        made, skip = mod.propose()
        return made, skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_registered_param():
    """显式登记为 param 的 pattern，喂饱 quorum 也不许提调参建议。"""
    made, skip = _run(_load(SRC, "evo_registered"), "SCORE_RANK_INVERTED")
    assert made == [], "param 类竟然产出了建议：%s" % made
    assert ("SCORE_RANK_INVERTED", 3) in skip["param_blocked"], \
        "没走 param_blocked 分支，可能在 quorum 处被挡（假通过）：%s" % skip
    assert not skip["below_quorum"], "被 quorum 挡住说明没测到方向分支：%s" % skip


def case_unregistered_default():
    """未登记的 pattern 必须默认按 param 处理，不能绕过拒提。"""
    made, skip = _run(_load(SRC, "evo_unregistered"), "BRAND_NEW_DETECTOR")
    assert made == [], "未登记方向放行了建议：%s" % made
    assert ("BRAND_NEW_DETECTOR", 3) in skip["param_blocked"], \
        "未登记类没落进 param_blocked：%s" % skip


def case_pipeline_allowed():
    """反向用例：pipeline 方向喂饱 quorum 必须能提，否则门禁是见谁都拦。"""
    made, skip = _run(_load(SRC, "evo_pipeline"), "FALLBACK_MASK")
    assert len(made) == 1, "pipeline 类也被拦了，防线等于全关：made=%s skip=%s" % (made, skip)
    assert made[0]["pattern"] == "FALLBACK_MASK"


def case_quorum_still_works():
    """门槛本身不能因为改了默认值而失效：n=1 的 pipeline 仍不许提。"""
    made, skip = _run(_load(SRC, "evo_quorum"), "FALLBACK_MASK", n=1)
    assert made == [], "quorum 失效了"
    assert any(x[0] == "FALLBACK_MASK" for x in skip["below_quorum"]), skip


def case_mutation_must_be_caught():
    """变异检验：把默认方向改回 unknown，未登记 pattern 必须能提建议。

    如果变异后仍提不出来，说明上面 case_unregistered_default 是碰巧通过，
    根本没走到方向判定分支。
    """
    src = SRC.read_text(encoding="utf-8")
    needle = "direction, layer = DIRECTION.get(pat, UNKNOWN_DIRECTION)"
    assert src.count(needle) == 1, "变异锚点不唯一，count=%d" % src.count(needle)
    mutant_src = src.replace(
        needle, 'direction, layer = DIRECTION.get(pat, ("unknown", "未分类"))')
    tmp = Path(tempfile.mkdtemp(prefix="zb_mutant_"))
    try:
        mutant = tmp / "evolve_mutant.py"
        mutant.write_text(mutant_src, encoding="utf-8")
        made, skip = _run(_load(mutant, "evo_mutant"), "BRAND_NEW_DETECTOR")
        assert len(made) == 1, \
            "变异后仍提不出建议 → 测试没走到方向分支（假通过）：made=%s skip=%s" % (made, skip)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CASES = [
    ("显式 param 拒提", case_registered_param),
    ("未登记默认 param", case_unregistered_default),
    ("pipeline 可提（反向）", case_pipeline_allowed),
    ("quorum 仍生效", case_quorum_still_works),
    ("变异检验能抓到", case_mutation_must_be_caught),
]


def main():
    ok = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  %s" % name)
            ok += 1
        except Exception as e:
            print("  FAIL  %s -> %s: %s" % (name, type(e).__name__, e))
    print("\n%d/%d 通过" % (ok, len(CASES)))
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
