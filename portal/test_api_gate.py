# -*- coding: utf-8 -*-
"""周一门判只读投影单测（仿 test_api_tickets.py 判例）。

红线：测试绝不读真实 data/shadow/monday_checkpoint.log——tmp 文件注入，
pytest -q 全绿。
运行：python -m pytest portal/test_api_gate.py -q
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_gate  # noqa: E402

_BAR = "=" * 56
_THIN = "-" * 56


def _block(ts="2026-10-17 23:16:35", exit_code=0, items=None, conclusion="7/7"):
    """造一个真实形态的门判块（仿 monday_checkpoint.log 勘察结构）。"""
    if items is None:
        items = [
            (
                "PASS",
                "影子首跑已发生（最新交易日 > 2026-10-14）",
                "最新=2026-10-14，首跑未发生（PENDING 中性）",
            ),
            ("SKIP", "schema_version/dk/degraded 三项（待首跑后验收）", ""),
            (
                "PASS",
                "replay_trace 锚点回放（bc6d91172b0a）",
                "verdict=OK 命中=2 缺环=无",
            ),
            ("PASS", "perf_ledger 行数>0 且 perf_key 唯一", "25 行 / 25 键"),
            (
                "FAIL",
                "任务 AgentShadowEvalWeekly",
                "state=Queued last=2026/10/17 23:17 result=0",
            ),
        ]
    lines = [_BAR, "M5 刀4 周一观察点护航巡检 %s" % ts, _BAR]
    for tag, name, detail in items:
        if detail:
            lines.append("[%s] %s —— %s" % (tag, name, detail))
        else:
            lines.append("[%s] %s" % (tag, name))
    lines += [
        _THIN,
        "结论: %s PASS——观察点门全过" % conclusion,
        "EXIT=%d" % exit_code,
        "",
    ]
    return "\n".join(lines)


def _mk_log(tmp_path, text, name="monday_checkpoint.log"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class TestParseBlock:
    def test_single_block_pass(self, tmp_path):
        p = _mk_log(tmp_path, _block(exit_code=0))
        g = api_gate.load_gate(log_path=p)
        assert g["ok"] is True and g["available"] is True
        assert g["latest"]["date"] == "2026-10-17"
        assert g["latest"]["verdict"] == "pass"
        keys = [i["key"] for i in g["latest"]["items"]]
        assert "replay_trace" in keys and "perf_ledger" in keys
        assert "AgentShadowEvalWeekly" in keys
        # 5 个门判项（SKIP 也投影，ok=False 只给 FAIL）
        assert len(g["latest"]["items"]) == 5
        by_key = {i["key"]: i for i in g["latest"]["items"]}
        assert by_key["replay_trace"]["ok"] is True
        assert by_key["AgentShadowEvalWeekly"]["ok"] is False
        assert by_key["AgentShadowEvalWeekly"]["detail"].startswith("[FAIL]")

    def test_skip_item_ok_and_marked(self, tmp_path):
        p = _mk_log(tmp_path, _block(exit_code=0))
        g = api_gate.load_gate(log_path=p)
        skip = [i for i in g["latest"]["items"] if i["detail"].startswith("[SKIP]")]
        assert len(skip) == 1 and skip[0]["ok"] is True

    def test_fail_via_exit(self, tmp_path):
        p = _mk_log(tmp_path, _block(exit_code=1))
        assert api_gate.load_gate(log_path=p)["latest"]["verdict"] == "fail"

    def test_fail_via_conclusion_when_no_exit(self, tmp_path):
        text = (
            _block(exit_code=0)
            .replace("EXIT=0\n", "")
            .replace("结论: 7/7", "结论: 6/7")
        )
        p = _mk_log(tmp_path, text)
        assert api_gate.load_gate(log_path=p)["latest"]["verdict"] == "fail"

    def test_pure_chinese_name_key_fallback(self, tmp_path):
        p = _mk_log(
            tmp_path,
            _block(items=[("PASS", "纯中文名的门", "detail 文本")], exit_code=0),
        )
        items = api_gate.load_gate(log_path=p)["latest"]["items"]
        assert items[0]["key"] == "item1" and items[0]["name"] == "纯中文名的门"


class TestLoadGate:
    def test_missing_file_pending_path(self, tmp_path):
        g = api_gate.load_gate(log_path=tmp_path / "nope.log")
        assert g["ok"] is True and g["available"] is False
        assert "周一 17:25 门判后自动点亮" in g["reason"]
        assert g["latest"] is None and g["history"] == []

    def test_empty_file_no_blocks(self, tmp_path):
        p = _mk_log(tmp_path, "")
        g = api_gate.load_gate(log_path=p)
        assert g["ok"] is True and g["available"] is False
        assert g["latest"] is None

    def test_history_newest_first_capped(self, tmp_path):
        text = "\n".join(
            _block(ts="2026-10-%02d 10:00:00" % d, exit_code=d % 2)
            for d in range(1, 13)
        )  # 12 块
        p = _mk_log(tmp_path, text)
        g = api_gate.load_gate(log_path=p)
        assert g["available"] is True
        assert len(g["history"]) == 10  # ≤10 条
        assert g["history"][0]["date"] == "2026-10-12"  # 新在前
        assert g["history"][-1]["date"] == "2026-10-03"
        assert g["latest"]["date"] == "2026-10-12"

    def test_bad_block_without_items_skipped(self, tmp_path):
        text = (
            _BAR
            + "\nM5 刀4 周一观察点护航巡检 2026-10-16 09:00:00\n"
            + _BAR
            + "\n"
            + _THIN
            + "\nEXIT=0\n\n"
            + _block(ts="2026-10-17 23:16:35", exit_code=0)
        )
        p = _mk_log(tmp_path, text)
        g = api_gate.load_gate(log_path=p)
        assert g["available"] is True
        assert g["latest"]["date"] == "2026-10-17"  # 空块被跳过
        assert len(g["history"]) == 1

    def test_duplicate_keys_deduped(self, tmp_path):
        items = [("PASS", "同门 一", "a"), ("PASS", "同门 二", "b")]
        p = _mk_log(tmp_path, _block(items=items, exit_code=0))
        keys = [i["key"] for i in api_gate.load_gate(log_path=p)["latest"]["items"]]
        assert keys[0] == "item1" and keys[1] == "item2"  # 纯中文退化为序号

    def test_contract_fields(self, tmp_path):
        g = api_gate.load_gate(log_path=tmp_path / "none.log")
        for k in ("ok", "available", "generated_at", "latest", "history"):
            assert k in g
        p = _mk_log(tmp_path, _block(exit_code=0))
        g2 = api_gate.load_gate(log_path=p)
        for k in ("date", "verdict", "items"):
            assert k in g2["latest"]
        for it in g2["latest"]["items"]:
            assert set(it) == {"key", "name", "ok", "detail"}
        assert all(isinstance(i["ok"], bool) for i in g2["latest"]["items"])

    def test_bad_bytes_fail_visible_not_crash(self, tmp_path):
        p = _mk_log(tmp_path, _block(exit_code=0))
        raw = p.read_bytes()
        raw += b"\xff\xfe broken bytes\n"
        p.write_bytes(raw)
        g = api_gate.load_gate(log_path=p)  # errors="replace"，不抛错
        assert g["available"] is True

    def test_log_missing_and_jsonl_irrelevant(self, tmp_path):
        """日志路径指向一个目录（而非文件）→ available:false 不抛错。"""
        d = tmp_path / "logdir"
        d.mkdir()
        g = api_gate.load_gate(log_path=d)
        assert g["ok"] is False or g["available"] is False
