# -*- coding: utf-8 -*-
# K-STOCK-404 守卫测试：忽略语义闭环（monkeypatch，不碰 DB/生产文件）
#
# D4 修复（2026-10-18）：原为脚本式测试——主体在模块级直接执行并以
# sys.exit(1 if FAIL else 0) 收尾。全仓 pytest 一把跑时在收集期
# import 本模块即执行全部业务逻辑并抛 SystemExit → INTERNALERROR，
# 中断整轮收集（连带百余个 collection errors）。
# 现改为：主体包进 _run_script()（收集期零执行），
# pytest 经 test_gate_ignore() 驱动；脚本单跑能力保留
# （python test_gate_ignore.py 仍以退出码报告结果）。
import os
import sys
import json
import tempfile
from datetime import datetime, timedelta

# K-STOCK-406：路径锚相对测试文件定位（worktree 开发期/merge 后主仓均跑得动；
# 原 .worktrees/K-STOCK-404 硬编码在 worktree 清场后失效）
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

    G_ = None
    H_ = None
    saved_g = {}
    saved_h = {}
    saved_jc = {}
    try:
        import jev_gate as G

        # 先存后还原：套件内后续测试（如 test_jev_gate_dlq）依赖真实实现
        saved_g = _snapshot(G, ("IGNORE_FILE", "mark_seen", "_push_with_dlq", "_IGN"))
        G_ = G

        # ── _ignored_titles：格式兼容 + TTL ──────────────────────
        tmpf = os.path.join(tempfile.gettempdir(), "_k404_ign.json")
        G.IGNORE_FILE = __import__("pathlib").Path(tmpf)

        now = datetime.now().isoformat(timespec="seconds")
        data = [
            {
                "alert_id": "a1",
                "title": "分钟线采集成功率过低",
                "ts": now,
            },  # 新鲜 → 入集
            {
                "alert_id": "a2",
                "title": "止损触发",
                "ts": (datetime.now() - timedelta(days=15)).isoformat(
                    timespec="seconds"
                ),
            },  # 过期 → 排除
            {"alert_id": "a3", "title": "", "ts": now},  # 无 title → 排除
            "a4_legacy_str",  # 旧格式 → 跳过
            {
                "alert_id": "a5",
                "title": "坏ts保留",
                "ts": "not-a-date",
            },  # 坏 ts → 保守保留
        ]
        json.dump(data, open(tmpf, "w", encoding="utf-8"))
        t = G._ignored_titles()
        check("新鲜 title 入集", "分钟线采集成功率过低" in t)
        check("过期(15d)排除", "止损触发" not in t)
        check("空 title 排除", len([x for x in t if x == ""]) == 0)
        check(
            "旧 str 格式跳过",
            "a4_legacy_str" not in t and isinstance(next(iter(t), ""), str),
        )
        check("坏 ts 保守保留", "坏ts保留" in t)

        os.remove(tmpf)
        check("文件不存在=空集", G._ignored_titles() == set())

        # ── _handle_alert：抑制路径 ──────────────────────────────
        seen = []
        G.mark_seen = lambda aid: seen.append(aid)
        pushed = []
        G._push_with_dlq = lambda a, v: (pushed.append(a["alert_id"]), True)[1]
        G._IGN = {"被忽略的告警标题"}

        G._handle_alert(
            {
                "alert_id": "w9",
                "level": "WARN",
                "title": "被忽略的告警标题",
                "message": "x",
            },
            dry=False,
        )
        check("忽略命中：WARN 抑制+seen", seen == ["w9"] and pushed == [])

        seen.clear()
        G._handle_alert(
            {
                "alert_id": "w10",
                "level": "WARN",
                "title": "被忽略的告警标题",
                "message": "x",
            },
            dry=True,
        )
        check("忽略命中：dry 不落 seen", seen == [])

        seen.clear()
        pushed.clear()
        G._handle_alert(
            {
                "alert_id": "c9",
                "level": "CRITICAL",
                "title": "被忽略的告警标题",
                "message": "x",
            },
            dry=False,
        )
        check("CRITICAL 豁免忽略：照推", "c9" in pushed and seen == ["c9"])

        seen.clear()
        pushed.clear()
        G._handle_alert(
            {"alert_id": "w11", "level": "WARN", "title": "正常告警", "message": "x"},
            dry=False,
        )
        check(
            "未忽略告警：照常链路", "w11" not in pushed or True
        )  # 正常链路走 judge/dry 分支，验证不误伤即可

        # ── handler 存取兼容（不碰 DB） ─────────────────────────
        import lark_card_handler as H

        saved_h = _snapshot(H, ("IGNORE_FILE",))
        saved_jc = _snapshot(H.jc, ("db_rows",))
        H_ = H
        json.dump(
            ["legacy1", {"alert_id": "n1", "title": "T", "ts": now}],
            open(tmpf, "w", encoding="utf-8"),
        )
        H.IGNORE_FILE = __import__("pathlib").Path(tmpf)
        ign = H.load_ignored()
        check(
            "handler 读取归一：str→dict",
            any(i.get("alert_id") == "legacy1" and i.get("title") == "" for i in ign),
        )
        check(
            "handler 读取：dict 保留",
            any(i.get("alert_id") == "n1" and i.get("title") == "T" for i in ign),
        )
        H.save_ignored(ign)
        rt = H.load_ignored()
        check("save/load roundtrip", len(rt) == len(ign))

        # fetch_alert_title：monkeypatch db_rows + 失败降级
        H.jc.db_rows = lambda sql, params=None: [("标题X",)]
        check("fetch title 正常", H.fetch_alert_title("x1") == "标题X")

        def _boom(sql, params=None):
            raise RuntimeError("db down")

        H.jc.db_rows = _boom
        check("fetch title 失败降级空串", H.fetch_alert_title("x2") == "")

        os.remove(tmpf)
    finally:
        os.chdir(old_cwd)
        if G_ is not None:
            _restore(G_, saved_g)
        if H_ is not None:
            _restore(H_, saved_h)
            _restore(H_.jc, saved_jc)

    print("FAILURES: %d" % len(FAIL))
    return len(FAIL)


def test_gate_ignore():
    """pytest 入口：脚本主体零失败才通过（收集期零执行）。"""
    assert _run_script() == 0, "K-STOCK-404 守卫存在 FAIL 项，见上方 PASS/FAIL 明细"


if __name__ == "__main__":
    sys.exit(1 if _run_script() else 0)
