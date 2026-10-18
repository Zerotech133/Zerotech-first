# -*- coding: utf-8 -*-
"""门 3 独立复核工具（tools/gate3_verdict.py）的判据测试。

为什么这份测试重要：这个工具的**唯一价值**是当 `monday_checkpoint.py`
本身坏掉时给出第二意见。如果它的判据口径与巡检悄悄分叉，两份结论就会
互相打架（或者更糟：一致地错），那它比没有更危险。所以这里逐条锁死：

1. **项数口径**：首跑前核心 7 项 / 首跑后 11 项——与巡检 SUMMARY 行同口径，
   只有这样交叉核对才比得出东西（曾因把 SKIP 计入 RESULTS 导致 10 vs 7
   的假不一致，差点误判「有一方坏了」）；
2. **篮子判据比原巡检更严**：4 标的 + 1 重复行（共 5 行）必须判 FAIL，
   而旧的 `== BASKET_N` 会判 PASS；
3. **三层字段判据**（schema / dk 六字段 / degraded 联动）各自独立可失败；
4. **增补项不污染核心项数**：坏行检查等记 EXTRA，不影响与巡检的比对；
5. **只读红线**：工具跑完不写任何文件（用 mtime + 目录快照断言）。
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import gate3_verdict as gv  # noqa: E402

MONDAY = "2026-10-19"
V0_DAY = gv.LAST_V0_DAY
SCRIPT = TOOLS / "gate3_verdict.py"
BASKET = ("600519", "000858", "601318", "300750", "600036")


def _row(sym, dt=MONDAY, **kw):
    row = {"date": dt, "symbol": sym, "status": "done",
           "schema_version": "m5.v1", "parse_ok": True, "degraded": False,
           "run_id": "abcdef123456"}
    for f in gv.DK_FIELDS:
        row[f] = "x"
    row.update(kw)
    return row


def _basket(n=5, **kw):
    return [_row(s, **kw) for s in BASKET[:n]]


def _v0_rows():
    """存量 v0 行（日期 = 末日，首跑未发生态）。"""
    return [_row("600519", dt=V0_DAY, schema_version="")]


def _reset():
    gv.RESULTS.clear()
    gv.EXTRA.clear()


def _fails(store=None):
    return [n for n, v, _ in (store if store is not None else gv.RESULTS)
            if v == gv.FAIL]


# ------------------------------------------------------------ 首跑两态

def test_first_run_pending_non_monday_is_neutral():
    """非周一且首跑未发生 → PENDING 中性（PASS），不拉低退出码。"""
    _reset()
    assert gv.judge_ledger(_v0_rows(), "2026-10-18", is_monday=False) is False
    assert _fails() == [], "非周一不该把 PENDING 判成 FAIL"
    assert len(gv.RESULTS) == 1, "首跑未发生态只登记首跑这一项"


def test_first_run_missing_on_monday_is_fail():
    """周一且首跑仍未落账 → 观察点门未兑现，必须 FAIL。"""
    _reset()
    assert gv.judge_ledger(_v0_rows(), MONDAY, is_monday=True) is False
    assert _fails(), "周一首跑未发生必须 FAIL"


def test_first_run_detected_when_latest_exceeds_v0():
    _reset()
    assert gv.judge_ledger(_basket(), MONDAY, is_monday=True) is True


# ------------------------------------------------------------ 篮子判据

def test_full_basket_five_symbols_passes():
    _reset()
    gv.judge_ledger(_basket(), MONDAY, is_monday=True)
    assert _fails() == []


def test_partial_basket_fails():
    """4 票落账 = 还没跑完，判 FAIL。"""
    _reset()
    gv.judge_ledger(_basket(4), MONDAY, is_monday=True)
    assert any("done 行数" in n for n in _fails())


def test_duplicate_row_four_symbols_fails():
    """★5 行但只有 4 个标的（1 条重复）——旧巡检 `== BASKET_N` 会判 PASS，
    本工具必须判 FAIL：重复写入不是「齐备」。"""
    _reset()
    rows = _basket(4) + [_row(BASKET[0])]
    assert len(rows) == 5
    gv.judge_ledger(rows, MONDAY, is_monday=True)
    assert any("done 行数" in n for n in _fails()), "4 标的 + 1 重复不得判齐备"


def test_failed_rows_do_not_count_as_done():
    """failed 行不参与 done 计数；4 done + 1 failed 必须 FAIL 且文案点明。"""
    _reset()
    rows = _basket(4) + [_row(BASKET[4], status="failed")]
    gv.judge_ledger(rows, MONDAY, is_monday=True)
    names = [n for n, v, _ in gv.RESULTS if v == gv.FAIL]
    assert names, "4 done + 1 failed 必须 FAIL"
    details = [d for n, v, d in gv.RESULTS if "done 行数" in n][0]
    assert "非 done 1 票" in details, "FAIL 文案须点明有非 done 票，否则会被误读成没跑完"


# ------------------------------------------------------------ 字段三层

def test_schema_version_missing_fails():
    _reset()
    gv.judge_ledger(_basket(4) + [_row(BASKET[4], schema_version="v0")],
                    MONDAY, is_monday=True)
    assert any("schema_version" in n for n in _fails())


def test_dk_field_missing_fails():
    _reset()
    bad = _row(BASKET[4])
    del bad["dk_error"]
    gv.judge_ledger(_basket(4) + [bad], MONDAY, is_monday=True)
    assert any("dk_*" in n for n in _fails())


def test_degraded_linkage_violation_fails():
    """parse_ok=False 但 degraded 不为 True → 刀 1 扩义被破坏。"""
    _reset()
    gv.judge_ledger(_basket(4) + [_row(BASKET[4], parse_ok=False, degraded=False)],
                    MONDAY, is_monday=True)
    assert any("degraded" in n for n in _fails())


def test_degraded_linkage_ok_when_consistent():
    _reset()
    gv.judge_ledger(_basket(4) + [_row(BASKET[4], parse_ok=False, degraded=True)],
                    MONDAY, is_monday=True)
    assert _fails() == []


# ------------------------------------------------------------ 项数口径

def test_core_count_before_first_run_is_seven():
    """首跑未发生：首跑(1) + replay(1) + perf(2) + 任务(3) = 7。
    与巡检 SUMMARY 行同口径——SKIP 不入账，所以 replay 必须给实判。"""
    _reset()
    gv.judge_ledger(_v0_rows(), "2026-10-18", is_monday=False)
    gv.judge_perf([{"perf_key": "a", "run_id": "abcdef123456"}], 0)
    gv.check("replay", gv.PASS)
    for n in gv.TASK_NAMES:
        gv.check(n, gv.PASS)
    assert len(gv.RESULTS) == 7, "首跑前核心项必须是 7（与巡检 SUMMARY 同口径）"


def test_core_count_after_first_run_is_eleven():
    """首跑已发生：首跑+行数+schema+dk+degraded(5) + replay(1) + perf(2) + 任务(3) = 11。"""
    _reset()
    gv.judge_ledger(_basket(), MONDAY, is_monday=True)
    gv.judge_perf([{"perf_key": "a", "run_id": "abcdef123456"}], 0)
    gv.check("replay", gv.PASS)
    for n in gv.TASK_NAMES:
        gv.check(n, gv.PASS)
    assert len(gv.RESULTS) == 11, "首跑后核心项必须是 11"


def test_skip_items_not_counted():
    """SKIP 只打印不入账——否则项数会比巡检多，交叉核对永远对不上。"""
    _reset()
    gv.check("x", gv.SKIP)
    assert gv.RESULTS == [] and gv.EXTRA == []


def test_extra_items_do_not_affect_core_count():
    _reset()
    gv.check("add-on", gv.FAIL, extra=True)
    assert gv.RESULTS == [] and len(gv.EXTRA) == 1


# ------------------------------------------------------------ 交叉核对

def test_cross_check_matches_when_same_numbers(tmp_path):
    _reset()
    gv.judge_ledger(_v0_rows(), "2026-10-18", is_monday=False)
    gv.judge_perf([{"perf_key": "a", "run_id": "abcdef123456"}], 0)
    gv.check("replay", gv.PASS)
    for n in gv.TASK_NAMES:
        gv.check(n, gv.PASS)
    log = tmp_path / "mc.log"
    log.write_text("=" * 56 + "\n结论: 7/7 PASS——观察点门全过\nEXIT=0 日志=x\n",
                   encoding="utf-8")
    gv.cross_check(log)
    assert not [n for n, v, _ in gv.EXTRA if v == gv.FAIL], "7 vs 7 应判一致"


def test_cross_check_flags_disagreement(tmp_path):
    """巡检说 6/7 而本工具 7/7 → 必须报不一致（有一方坏了）。"""
    _reset()
    gv.judge_ledger(_v0_rows(), "2026-10-18", is_monday=False)
    gv.judge_perf([{"perf_key": "a", "run_id": "abcdef123456"}], 0)
    gv.check("replay", gv.PASS)
    for n in gv.TASK_NAMES:
        gv.check(n, gv.PASS)
    log = tmp_path / "mc.log"
    log.write_text("=" * 56 + "\n结论: 6/7 PASS（FAIL: x）\nEXIT=1\n", encoding="utf-8")
    gv.cross_check(log)
    assert [n for n, v, _ in gv.EXTRA if v == gv.FAIL], "结论不一致必须报 FAIL"


def test_cross_check_skips_without_log(tmp_path):
    _reset()
    gv.judge_ledger(_v0_rows(), "2026-10-18", is_monday=False)
    gv.cross_check(tmp_path / "nope.log")
    assert gv.EXTRA == [], "日志不存在应 SKIP 而非 FAIL"


# ------------------------------------------------------------ load_jsonl

def test_load_jsonl_counts_bad_lines(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text('{"a":1}\n\nnot-json\n[1,2]\n{"b":2}\n', encoding="utf-8")
    rows, bad = gv.load_jsonl(p)
    assert len(rows) == 2 and bad == 2, "坏行必须计数而非静默"


def test_load_jsonl_missing_file_is_empty(tmp_path):
    rows, bad = gv.load_jsonl(tmp_path / "nope.jsonl")
    assert rows == [] and bad == 0


# ------------------------------------------------------------ 早跑误判防护

def test_early_run_warning_on_monday_with_partial_basket(capsys):
    """周一只有 3 票时，工具会判 FAIL——必须显式提示「这可能只是还没跑完」。
    两条判据形态一样、结论相反，是门 3 最容易误判的一类。"""
    gv._warn_early_run(_basket(3), MONDAY, is_monday=True)
    out = capsys.readouterr().out
    assert "还没跑完" in out and "3/5" in out


def test_early_run_warning_silent_when_basket_complete(capsys):
    gv._warn_early_run(_basket(5), MONDAY, is_monday=True)
    assert capsys.readouterr().out == ""


def test_early_run_warning_silent_on_non_monday(capsys):
    """非周一没有「首跑窗口」概念，不该弹这个提示。"""
    gv._warn_early_run(_basket(3), "2026-10-18", is_monday=False)
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------ CLI + 只读红线

def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=120)


def _write_isolated_base(tmp_path, rows):
    base = tmp_path / "ap"
    (base / "data" / "shadow").mkdir(parents=True)
    (base / "data" / "shadow" / "shadow_ledger.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    (base / "data" / "perf_ledger.jsonl").write_text(
        json.dumps({"perf_key": "k1", "run_id": "abcdef123456"}) + "\n",
        encoding="utf-8")
    return base


def test_cli_reports_four_core_items_when_tasks_skipped(tmp_path):
    """隔离目录 + --skip-tasks + --no-cross-check：
    首跑(1) + replay(1) + perf(2) = 4 项（任务三项 SKIP 不入账）。
    这里刻意断言「4」而不是「7」——正是为了锁死「SKIP 不计数」这条口径，
    它决定了交叉核对能不能与巡检的 SUMMARY 行对上。"""
    base = _write_isolated_base(tmp_path, _v0_rows())
    links = tmp_path / "links.jsonl"
    links.write_text("", encoding="utf-8")
    r = _run_cli("--base", str(base), "--date", "2026-10-18",
                 "--skip-tasks", "--no-cross-check", "--trace-links", str(links))
    assert "核心判据 4 项" in r.stdout, r.stdout
    assert "退出码" in r.stdout
    # 空 trace_links 下 replay 必然 NOT_FOUND → 这条必须判 FAIL，不允许放过
    assert "replay_trace 锚点回放" in r.stdout and "FAIL" in r.stdout
    # 退出码契约（U 路对齐后锁死）：存在 FAIL → 1
    assert r.returncode == 1, r.stdout


def test_cli_exit_zero_when_all_pass(tmp_path):
    """全过（含 replay 命中）→ 退出码 0。合法 trace link 构造自
    trace_link.py 的 SEG_REQUIRED['shadow'] 锚点字段，命中 run_id 即 verdict=OK。"""
    base = _write_isolated_base(tmp_path, _basket())
    link = {"schema_version": "m4p1.v1", "link_id": "0123456789abcdef",
            "chain": {"shadow": {"run_id": "abcdef123456",
                                 "symbol": "600519",
                                 "trade_date": MONDAY}},
            "source": "test", "declared_at": ""}
    links = tmp_path / "links.jsonl"
    links.write_text(json.dumps(link, ensure_ascii=False) + "\n", encoding="utf-8")
    r = _run_cli("--base", str(base), "--date", MONDAY,
                 "--skip-tasks", "--no-cross-check", "--trace-links", str(links))
    assert "退出码 0" in r.stdout, r.stdout
    assert r.returncode == 0, r.stdout


def test_cli_missing_data_is_exit_one_not_two(tmp_path):
    """★退出码契约锁死：数据缺失（账本不存在）判 FAIL → 1，绝不是 2。
    第二意见工具不允许「无法判定」中性放行——旧 docstring 宣称的
    「2=无法判定」无代码路径，2026-10-18 U 路裁决改 docstring 对齐代码。"""
    base = tmp_path / "ap"
    (base / "data" / "shadow").mkdir(parents=True)  # 只建目录，不落账本
    r = _run_cli("--base", str(base), "--date", "2026-10-18",
                 "--skip-tasks", "--no-cross-check", "--trace-links", "")
    assert r.returncode == 1, r.stdout
    assert "退出码 1" in r.stdout, r.stdout


def test_cli_is_read_only(tmp_path):
    """红线：工具跑完不改任何文件（账本 mtime 与内容必须原样）。"""
    base = tmp_path / "ap"
    (base / "data" / "shadow").mkdir(parents=True)
    led = base / "data" / "shadow" / "shadow_ledger.jsonl"
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in _basket()) + "\n"
    led.write_text(payload, encoding="utf-8")
    perf = base / "data" / "perf_ledger.jsonl"
    perf.write_text(json.dumps({"perf_key": "k1", "run_id": "abcdef123456"}) + "\n",
                    encoding="utf-8")
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (led, perf)}
    links = tmp_path / "links.jsonl"
    links.write_text("", encoding="utf-8")
    _run_cli("--base", str(base), "--date", MONDAY,
             "--skip-tasks", "--no-cross-check", "--trace-links", str(links))
    after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (led, perf)}
    assert before == after, "复核工具不得写账本（改 mtime 也不行）"
    assert sorted(x.name for x in (base / "data" / "shadow").iterdir()) == \
        ["shadow_ledger.jsonl"], "不得新增任何文件"
