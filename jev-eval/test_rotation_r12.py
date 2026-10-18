# -*- coding: utf-8 -*-
# K-STOCK-405 守卫测试：死信 TTL 清理 + 三元组归档（monkeypatch 路径，不碰生产文件）
#
# D4 修复（2026-10-18）：原为脚本式测试——主体在模块级直接执行并以
# sys.exit(1 if FAIL else 0) 收尾，全仓 pytest 收集期 import 即抛
# SystemExit → INTERNALERROR。现改为：主体包进 _run_script()
# （收集期零执行），pytest 经 test_rotation_r12() 驱动；
# 脚本单跑能力保留（python test_rotation_r12.py 仍以退出码报告结果）。
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# K-STOCK-406：路径锚相对测试文件定位（worktree 开发期/merge 后主仓均跑得动；
# 原 .worktrees/K-STOCK-405 硬编码在 worktree 清场后失效）
WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JEV_DIR = os.path.join(WT, "jev-eval")

_MISSING = object()


def _snapshot(obj, names):
    """记录 obj 上将被替换属性的原值（缺失以 _MISSING 标记）。"""
    return {n: getattr(obj, n, _MISSING) for n in names}


def _restore(obj, saved):
    """还原 _snapshot 记录的属性（pytest 套件内多脚本共享进程，防串扰）。"""
    for n, v in saved.items():
        if v is _MISSING:
            if hasattr(obj, n):
                delattr(obj, n)
        else:
            setattr(obj, n, v)


def _run_script():
    """执行原脚本主体；返回失败项数量（0=全过）。"""
    sys.path.insert(0, JEV_DIR)
    old_cwd = os.getcwd()
    os.chdir(JEV_DIR)
    FAIL = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            FAIL.append(name)

    def dts(days_ago):
        return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")

    G_ = None
    S_ = None
    saved_g = {}
    saved_s = {}
    try:
        import jev_gate as G
        import signal_review_gate as S

        # 先存后还原：套件内多脚本共享进程，防 monkeypatch 串扰
        saved_g = _snapshot(G, ("DEAD_LETTER", "_last_trim"))
        saved_s = _snapshot(S, ("TRIPLES", "ARCHIVE", "_last_archive"))
        G_ = G
        S_ = S

        TMP = Path(tempfile.gettempdir())

        # ── 死信 TTL 清理 ────────────────────────────────────────
        dl = TMP / "_k405_dl.jsonl"
        dl.write_text("\n".join([
            json.dumps({"ts": dts(40), "alert_id": "old", "final": True}),
            json.dumps({"ts": dts(2), "alert_id": "new", "final": True}),
            json.dumps({"ts": "not-a-date", "alert_id": "badts", "final": True}),  # 坏 ts 保留
            json.dumps({"alert_id": "nots", "final": True}),                        # 缺 ts 保留
            "",
        ]), encoding="utf-8")
        G.DEAD_LETTER = dl
        G._last_trim = ""
        G._trim_dead_letter()
        kept = json.loads("[%s]" % ",".join(l for l in dl.read_text(encoding="utf-8").splitlines() if l.strip()))
        check("死信：40 天前清理", all(r["alert_id"] != "old" for r in kept))
        check("死信：2 天内保留", any(r["alert_id"] == "new" for r in kept))
        check("死信：坏 ts 保留", any(r["alert_id"] == "badts" for r in kept))
        check("死信：缺 ts 保留", any(r["alert_id"] == "nots" for r in kept))
        sz_after = dl.stat().st_size
        G._trim_dead_letter()  # 同日第二次调用：节流不重写
        check("死信：按日节流不重复重写", dl.stat().st_size == sz_after)
        dl.unlink()
        G._trim_dead_letter()
        check("死信：文件缺失 no-op", True)

        # ── 三元组归档 ───────────────────────────────────────────
        tp = TMP / "_k405_tp.jsonl"
        ar = TMP / "_k405_ar.jsonl"
        tp.write_text("\n".join([
            json.dumps({"ts": dts(40), "candidate_id": "t1", "status": "reviewed", "decision": "FOCUS", "ret_nd": 0.05}),
            json.dumps({"ts": dts(2), "candidate_id": "t2", "status": "reviewed", "decision": "FOCUS", "ret_nd": 0.03}),
            json.dumps({"ts": dts(40), "candidate_id": "t3", "status": "reviewed", "decision": "WATCH"}),  # 未回填永不归档
            json.dumps({"ts": dts(40), "candidate_id": "t4", "status": "pending", "decision": "?"}),         # 非终端不归档
            json.dumps({"ts": "bad", "candidate_id": "t5", "status": "reviewed", "decision": "FOCUS", "ret_nd": 0.01}),  # 坏 ts 保守保留
            "",
        ]), encoding="utf-8")
        S.TRIPLES = tp
        S.ARCHIVE = ar
        S._last_archive = ""
        n = S._maybe_archive_triples()
        check("归档：终端且超 30 天 =1 条", n == 1)
        live = [json.loads(l) for l in tp.read_text(encoding="utf-8").splitlines() if l.strip()]
        arch = [json.loads(l) for l in ar.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("归档：活文件剩 4 条", len(live) == 4)
        check("归档：t1 已移出活文件", all(r["candidate_id"] != "t1" for r in live))
        check("归档：t2(新)/t3(未回填)/t4(非终端)/t5(坏ts) 全保留",
              {r["candidate_id"] for r in live} == {"t2", "t3", "t4", "t5"})
        check("归档：归档文件仅 t1", len(arch) == 1 and arch[0]["candidate_id"] == "t1")
        check("归档：按日节流第二次=0", S._maybe_archive_triples() == 0)

        # ── _iter_triples：归档+活文件合并去重 ────────────────────
        with tp.open("a", encoding="utf-8") as f:  # 追加 t1 到活文件（t2-t5 仍在）
            f.write(json.dumps({"ts": dts(1), "candidate_id": "t1", "status": "reviewed", "decision": "FOCUS", "ret_nd": 0.09}) + "\n")
        S._last_archive = ""  # 复位节流（仅影响归档函数）
        it = S._iter_triples()
        recs = [json.loads(l) for l in it]
        check("合并：唯一记录=5（t1 去重合并）", len(recs) == 5)
        t1 = [r for r in recs if r["candidate_id"] == "t1"]
        check("合并：同 candidate 活文件胜出(ret=0.09)", len(t1) == 1 and t1[0]["ret_nd"] == 0.09)
        tp.unlink(); ar.unlink()
        check("合并：文件缺失=空", S._iter_triples() == [])
    finally:
        os.chdir(old_cwd)
        if G_ is not None:
            _restore(G_, saved_g)
        if S_ is not None:
            _restore(S_, saved_s)

    print("FAILURES: %d" % len(FAIL))
    return len(FAIL)


def test_rotation_r12():
    """pytest 入口：脚本主体零失败才通过（收集期零执行）。"""
    assert _run_script() == 0, "K-STOCK-405 守卫存在 FAIL 项，见上方 PASS/FAIL 明细"


if __name__ == "__main__":
    sys.exit(1 if _run_script() else 0)
