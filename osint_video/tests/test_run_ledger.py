"""轮次台账测试。

重点不在"能不能存"，而在**判据说的是不是真话**：
- 空转轮次不能被当成实跑（否则趋势拿 0 当基线）
- 前提不同的两轮不能被比较（否则"换库"会被读成"聚类劣化"）
- 判定器降级必须报 fail 而不是 warn（数字看起来仍然合理，这是最危险的一类）

``check_round`` 刻意不判"事件数应该等于几"，所以这里也不测这种事——测了就
等于把某次语料的结果钉成永久期望。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge import run_ledger as rl

ROOT = Path(__file__).resolve().parents[1]

#: 变异测试开关。子进程必须清掉它，否则递归复制自己（见 ``_run_variant``）。
MUTATION_FLAG = "OSINT_MUTATION"


def _payload(**over) -> dict:
    """一轮正常 payload 的最小形态。"""
    base = {
        "units_scanned": 8,
        "stories_created": 2,
        "stories_reused": 3,
        "members_added": 8,
        "units_skipped_existing": 0,
        "unrelated": 1,
        "judge_calls": 4,
        "judge_failures": 0,
        "anchors_loaded": 8,
        "cross_window_merged": 4,
        "borderline": 2,
        # 明细列表：与真实 payload 一致（不是计数），确保"不落台账"的断言有意义
        "borderline_samples": [],
        "borderline_rechecked": 2,
        "borderline_promoted": 1,
        "manual_overrides": 0,
        "pairs_observed": 27,
        "elapsed_ms": 1200,
        "dry_run": False,
        "degraded": False,
        "degrade_reason": None,
        "borderline_judge": "llm",
        "cross_window": True,
        "judge_name": "llm-ollama-v2",
        "review_queue_written": 1,
        "review_backlog": {
            "total": 2,
            "pending": 2,
            "decided": 0,
            "oldest_days": 0.3,
            "unknown_age": 0,
            "alert": None,
        },
    }
    base.update(over)
    return base


def _record(**over) -> dict:
    ctx = {
        "db_name": "osint",
        "window_days": 14,
        "limit": 2000,
    }
    ctx.update(over.pop("context", {}))
    payload = _payload(**over)
    return rl.build_record(
        payload,
        db_name=ctx["db_name"],
        window_days=ctx["window_days"],
        limit=ctx["limit"],
        recorded_at=datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc),
    )


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


def _by_code(findings, code) -> rl.Finding:
    for finding in findings:
        if finding.code == code:
            return finding
    raise AssertionError(f"没有 {code}，实际有：{sorted(_codes(findings))}")


# ---------------------------------------------------------------- 记录结构 --


class TestBuildRecord:
    def test_noop_is_derived_from_units_scanned(self):
        """空转轮次必须自证：它没有算任何东西，不能与实跑混为一谈。"""
        record = _record(units_scanned=0)
        assert record["noop"] is True
        assert _record(units_scanned=3)["noop"] is False

    def test_detail_samples_are_not_persisted(self):
        """配对明细不落台账：那里有事实标题，台账不该成为事件表的第二份副本。

        ``borderline_samples`` 与 ``borderline_promoted_samples`` **都是**明细列表
        （各受 ``BORDERLINE_SAMPLE_LIMIT`` 限制），不是计数——曾误以为前者是计数
        而写进白名单，只有靠这条断言才暴露。真正要的计数是 ``borderline`` 与
        ``borderline_promoted``。
        """
        payload = _payload()
        payload["borderline_samples"] = [{"left": "a", "right": "b", "title": "某公司涨价"}]
        record = rl.build_record(
            payload,
            db_name="osint",
            window_days=14,
            limit=2000,
            recorded_at=datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc),
        )
        serialized = json.dumps(record, ensure_ascii=False)
        assert "borderline_promoted_samples" not in serialized
        assert "borderline_samples" not in serialized
        assert "某公司涨价" not in serialized
        # 计数仍在
        assert record["metrics"]["borderline"] == 2

    def test_backlog_is_flattened_for_trend(self):
        """积压从嵌套结构拍平成指标：跨轮趋势只关心 pending 这一个数。"""
        record = _record()
        assert record["metrics"]["review_pending"] == 2
        assert record["metrics"]["review_alert"] is None

    def test_context_carries_comparability_premise(self):
        """只存结果不存前提，就没法判断两轮可比不可比。"""
        ctx = _record()["context"]
        for key in ("db_name", "window_days", "limit", "dry_run", "cross_window"):
            assert key in ctx


# ---------------------------------------------------------------- 读写行为 --


class TestPersistence:
    def test_append_then_load_roundtrip(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        rl.append_round(path, _record())
        rl.append_round(path, _record(stories_created=5))
        rows = rl.load_rounds(path)
        assert len(rows) == 2
        assert rows[-1]["metrics"]["stories_created"] == 5

    def test_missing_file_is_empty_not_error(self, tmp_path):
        """首轮之前台账不存在，这是正常状态而不是故障。"""
        assert rl.load_rounds(tmp_path / "nope.jsonl") == []

    def test_corrupt_line_is_skipped_and_counted(self, tmp_path):
        """一行坏掉不能让整段历史读不出来，但坏行本身要能被发现。"""
        path = tmp_path / "ledger.jsonl"
        rl.append_round(path, _record())
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{这不是 JSON\n")
        rl.append_round(path, _record(stories_created=9))

        rows = rl.load_rounds(path)
        assert len(rows) == 2
        health = rl.ledger_health(path)
        assert health == {"lines": 3, "parsed": 2, "corrupt": 1}

    def test_legacy_row_without_noop_field_is_derived_on_read(self, tmp_path):
        """旧行没有 noop 字段（后加的），读时必须派生，否则空转被当成实跑。"""
        path = tmp_path / "ledger.jsonl"
        legacy = _record(units_scanned=0)
        legacy.pop("noop")
        path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
        assert rl.load_rounds(path)[0]["noop"] is True

    def test_append_failure_raises_oserror(self, tmp_path):
        """写不进去必须抛：让调用方决定降级，而不是台账自己吞掉变成永远健康。"""
        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(OSError):
            rl.append_round(blocker / "sub" / "ledger.jsonl", _record())


# ---------------------------------------------------------------- 基线选择 --


class TestFindBaseline:
    def test_same_context_matches(self):
        current = _record()
        history = [_record(stories_created=1)]
        baseline, reason = rl.find_baseline(current, history)
        assert baseline is not None
        assert reason == ""

    def test_noop_history_is_skipped(self):
        """空转轮次指标全 0，拿它当基线会把"断流后恢复"读成"处理量激增"。"""
        current = _record()
        history = [_record(units_scanned=8, stories_created=3), _record(units_scanned=0)]
        baseline, _ = rl.find_baseline(current, history)
        assert baseline is not None
        assert baseline["metrics"]["stories_created"] == 3

    def test_different_db_is_not_comparable(self):
        """不同库的事件数天然不同，必须拒绝比较并说出差在哪个字段。"""
        current = _record()
        history = [_record(context={"db_name": "osint_calib"})]
        baseline, reason = rl.find_baseline(current, history)
        assert baseline is None
        assert "db_name" in reason

    def test_different_window_is_not_comparable(self):
        current = _record()
        history = [_record(context={"window_days": 60})]
        baseline, reason = rl.find_baseline(current, history)
        assert baseline is None
        assert "window_days" in reason

    def test_empty_history_explains_first_round(self):
        baseline, reason = rl.find_baseline(_record(), [])
        assert baseline is None
        assert "首轮" in reason

    def test_older_but_matching_round_is_found_before_giving_up(self):
        """中途改一次窗口再改回来，不该让趋势断掉。"""
        current = _record()
        history = [
            _record(stories_created=4),
            _record(context={"window_days": 60}),
        ]
        baseline, _ = rl.find_baseline(current, history)
        assert baseline is not None
        assert baseline["metrics"]["stories_created"] == 4


# ---------------------------------------------------------------- 体检判据 --


class TestCheckRound:
    def test_healthy_round_reports_ok(self):
        findings = rl.check_round(_record(), [])
        assert all(f.level == "ok" for f in findings)
        assert "cross_window.merged" in _codes(findings)

    def test_degraded_judge_is_fail_not_warn(self):
        """降级后数字仍然合理，只是含义变了——必须判 fail，不能只提醒。"""
        record = _record(degraded=True, degrade_reason="embedding 不可用")
        finding = _by_code(rl.check_round(record, []), "judge.degraded")
        assert finding.level == "fail"
        assert "embedding" in finding.message

    def test_judge_failures_are_warned(self):
        """失败对回落规则判定（不是冒充无关），所以是 warn 而不是 fail。"""
        finding = _by_code(rl.check_round(_record(judge_failures=3), []), "judge.failures")
        assert finding.level == "warn"
        assert "3" in finding.message

    def test_zero_merge_with_anchors_is_warned(self):
        """有锚点却零合并，与"根本没锚点"是两件事，必须分开报。"""
        record = _record(anchors_loaded=8, cross_window_merged=0)
        finding = _by_code(rl.check_round(record, []), "cross_window.zero_merge")
        assert finding.level == "warn"

    def test_zero_anchor_is_ok_not_warn(self):
        """窗外没有已归属事实是预期内的，报成告警会让真实告警失效。"""
        record = _record(anchors_loaded=0, cross_window_merged=0)
        finding = _by_code(rl.check_round(record, []), "cross_window.no_anchor")
        assert finding.level == "ok"

    def test_borderline_not_rechecked_is_warned(self):
        """复核开着却不调用，通常是预热失败或全命中台账，得说出来。"""
        record = _record(borderline=2, borderline_rechecked=0, borderline_promoted=0)
        finding = _by_code(rl.check_round(record, []), "borderline.not_rechecked")
        assert finding.level == "warn"

    def test_borderline_off_is_ok_with_p0_note(self):
        record = _record(borderline_judge="off", borderline_rechecked=0, borderline_promoted=0)
        finding = _by_code(rl.check_round(record, []), "borderline.off")
        assert finding.level == "ok"

    def test_backlog_alert_is_forwarded_not_recomputed(self):
        """台账只转述队列的告警文案，不自己重算阈值——否则两处结论会分裂。"""
        record = _record()
        record["metrics"]["review_alert"] = "待确认积压 25 条（上限 20）"
        finding = _by_code(rl.check_round(record, []), "review.backlog_alert")
        assert finding.level == "warn"
        assert "25" in finding.message

    def test_pending_increase_is_warned(self):
        current = _record()
        current["metrics"]["review_pending"] = 5
        history = [_record()]
        finding = _by_code(rl.check_round(current, history), "trend.pending_up")
        assert finding.level == "warn"
        assert "+3" in finding.message

    def test_pending_flat_is_ok(self):
        findings = rl.check_round(_record(), [_record()])
        assert _by_code(findings, "trend.pending").level == "ok"

    def test_cross_window_regression_is_warned(self):
        """跨窗合并从 4 降到 0 是最典型的静默回归。"""
        current = _record(cross_window_merged=0, anchors_loaded=8)
        history = [_record(cross_window_merged=4)]
        assert _by_code(
            rl.check_round(current, history), "trend.cross_window_lost"
        ).level == "warn"

    def test_first_round_says_no_baseline(self):
        """首轮必须明说"没有基线"，不能让人以为趋势已经比过了。"""
        findings = rl.check_round(_record(), [])
        assert "trend.no_baseline" in _codes(findings)


class TestNoopRound:
    def test_noop_round_short_circuits_structural_checks(self):
        """空转轮次没算东西，跨窗/临界带判据全部无意义，不该输出。"""
        record = _record(units_scanned=0, anchors_loaded=0, cross_window_merged=0)
        findings = rl.check_round(record, [])
        assert _codes(findings) == {"round.noop"}

    def test_noop_round_still_reports_backlog(self):
        """积压是文件状态，与本轮算了多少无关——空转时照样要看。"""
        record = _record(units_scanned=0)
        record["metrics"]["review_alert"] = "待确认积压 25 条（上限 20）"
        assert "review.backlog_alert" in _codes(rl.check_round(record, []))

    def test_noop_streak_is_reported_as_fact_not_verdict(self):
        """只报"连续 N 轮"，不判"N 轮算异常"：调度频率不在仓库里，阈值无从谈起。"""
        record = _record(units_scanned=0)
        history = [_record(), _record(units_scanned=0), _record(units_scanned=0)]
        finding = _by_code(rl.check_round(record, history), "round.noop")
        assert "连续空转 2 轮" in finding.message
        assert finding.level == "ok"


# ---------------------------------------------------------------- 变异测试 --

def _run_variant(tmp_path: Path, old: str, new: str) -> tuple[bool, str]:
    """把 ``run_ledger.py`` 里的 ``old`` 换成 ``new``，跑本文件用例。

    Returns:
        ``(是否全绿, 末行摘要)``。全绿表示变异**没被捕获**，即判据有缺口。
    """
    work = tmp_path / "mut"
    if work.exists():
        shutil.rmtree(work)
    # ``run_ledger.py`` 只依赖标准库，所以只造最小包结构。
    # 注意 ``knowledge/__init__.py`` 必须写成空文件：仓库里那份会再导出
    # item_processor → collectors 整条依赖链，复制整个包会连带一长串无关依赖
    # （第一次实现就是复制了三个包，结果 collection error 25 秒才暴露）。
    # 若将来 run_ledger 引入内部依赖，这里会立刻炸——属于"失败即暴露"，好过静默失真。
    (work / "knowledge").mkdir(parents=True)
    (work / "knowledge" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(ROOT / "knowledge" / "run_ledger.py", work / "knowledge" / "run_ledger.py")
    (work / "tests").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), work / "tests" / Path(__file__).name)
    # 给 work 一个自己的 pytest 配置，把 rootdir 钉死在这里。否则 pytest 会一路
    # 向上探测到临时目录的祖先，加载到与本仓库无关的配置（表现为间歇性
    # collection error，单跑必过、连跑偶发——排查成本很高）。
    (work / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    target = work / "knowledge" / "run_ledger.py"
    source = target.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"变异锚点在源码中出现 {source.count(old)} 次，必须唯一：{old!r}")
    target.write_text(source.replace(old, new), encoding="utf-8")

    # 子进程里必须清掉开关：否则它会再跑一遍变异类 → 递归复制 → 永远跑不完。
    env = {k: v for k, v in os.environ.items() if k != MUTATION_FLAG}
    proc = subprocess.run(
        # -p no:cacheprovider：work 目录挂在 pytest 的临时根下，多个子进程并发时
        # 会竞争同一个 .pytest_cache（表现为间歇性 "1 error" 且单跑必过）。
        [sys.executable, "-m", "pytest", "tests/" + Path(__file__).name, "-q", "-x",
         "-p", "no:cacheprovider"],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    summary = tail[-1] if tail else ""
    # 变异把模块写坏会导致 collection error，那种情况一条用例都没跑，
    # "没失败"是假象而不是"变异未被捕获"。必须看到真正的 failed 才算数。
    if "error" in summary and "failed" not in summary:
        detail = (proc.stdout + proc.stderr)[-1500:]
        raise AssertionError(f"变异导致收集期错误，不是断言捕获：{summary}\n{detail}")
    return proc.returncode == 0, summary


#: 每一项都是"改坏了也不会报错、只会静默少报"的判据——正是最该被抓住的一类。
_VARIANTS: tuple[tuple[str, str, str], ...] = (
    (
        "noop_always_false",
        '"noop": not metrics.get("units_scanned"),',
        '"noop": False,',
    ),
    (
        "baseline_keeps_noop",
        'if skip_noop and candidate.get("noop"):',
        "if False:",
    ),
    (
        "context_compare_always_equal",
        "if lv != rv:",
        "if False:",
    ),
    (
        "degraded_downgraded_to_warn",
        '"fail",\n                "judge.degraded",',
        '"warn",\n                "judge.degraded",',
    ),
    (
        "pending_increase_ignored",
        "        if delta > 0:",
        "        if False:",
    ),
    (
        "noop_round_not_short_circuited",
        'if current.get("noop"):',
        "if False:",
    ),
    (
        "legacy_row_not_normalized",
        'if "noop" not in row:',
        "if False:",
    ),
)


@pytest.mark.skipif(
    not os.environ.get(MUTATION_FLAG),
    reason=f"变异测试要复制 3 个包并跑 7 轮子进程（约 4-5 分钟），用 {MUTATION_FLAG}=1 触发",
)
class TestMutation:
    @pytest.mark.parametrize("name,old,new", _VARIANTS, ids=[v[0] for v in _VARIANTS])
    def test_variant_is_caught(self, tmp_path, name, old, new):
        all_green, summary = _run_variant(tmp_path, old, new)
        assert not all_green, f"变异 {name} 未被任何用例捕获（{summary}）"
