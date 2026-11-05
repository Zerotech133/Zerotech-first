"""标定新鲜度戳：钉住"什么才算语料变了"。

这个模块的价值全在**判据**上，所以测试重点不是"能存能读"，而是四类容易搞错
的边界：

1. 行序变化不能算语料变化（否则告警一天响一次，没人再看）；
2. ``truth`` 为 null 与"这一行没有真值"必须区分（前者是合法的独立事件结论）；
3. 两类标定互不覆盖（只重跑一半时不能显示全绿）；
4. 戳损坏要显式报损坏，不能退化成"从未标定"（两者处置方式不同）。

外加变异测试脚本，确认这些断言不是空转。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from knowledge import calibration_stamp as stamp

ROOT = Path(__file__).resolve().parents[1]

#: 变异开关：默认关，避免日常全量多跑三分钟。
MUTATION_FLAG = "OSINT_MUTATION"


def rows(*pairs) -> list[dict]:
    return [{"unit_key": k, "truth": v} for k, v in pairs]


# ------------------------------------------------------------ 指纹判据 --


class TestFingerprintRows:
    def test_order_does_not_matter(self):
        """DB 返回顺序不保证稳定；顺序变了不能报"语料变了"。"""
        a = rows(("s1", "S1"), ("s2", "S2"), ("s3", None))
        b = rows(("s3", None), ("s1", "S1"), ("s2", "S2"))
        assert stamp.fingerprint_rows(a) == stamp.fingerprint_rows(b)

    def test_truth_change_flips_fingerprint(self):
        before = rows(("s1", "S1"), ("s2", "S2"))
        after = rows(("s1", "S1"), ("s2", "S9"))
        assert stamp.fingerprint_rows(before) != stamp.fingerprint_rows(after)

    def test_added_unit_flips_fingerprint(self):
        before = rows(("s1", "S1"))
        after = rows(("s1", "S1"), ("s2", "S1"))
        assert stamp.fingerprint_rows(before) != stamp.fingerprint_rows(after)

    def test_null_truth_differs_from_absent_row(self):
        """null 是"独立事件"这一合法结论，不能与"没有真值标注"同码。"""
        with_null = rows(("s1", "S1"), ("s2", None))
        without = rows(("s1", "S1"))
        assert stamp.fingerprint_rows(with_null) != stamp.fingerprint_rows(without)
        # 计数也要能区分：否则只靠指纹比对时人会以为少的那条是被合并了
        assert stamp.fingerprint_rows(with_null)[1] == 2
        assert stamp.fingerprint_rows(without)[1] == 1

    def test_null_becoming_real_value_flips(self):
        """把独事实补上真值，属语料变更，必须过期。"""
        before = rows(("s1", None))
        after = rows(("s1", "S1"))
        assert stamp.fingerprint_rows(before) != stamp.fingerprint_rows(after)

    def test_empty_string_truth_is_not_null(self):
        """空串是合法真值（历史脏数据），不能与 null 混为一谈。"""
        empty = stamp.fingerprint_rows(rows(("s1", "")))
        null = stamp.fingerprint_rows(rows(("s1", None)))
        assert empty != null

    def test_whitespace_around_truth_is_ignored(self):
        """``extension->>'truth_story'`` 的取值可能带空格，不该造成假过期。"""
        assert stamp.fingerprint_rows(rows(("s1", " S1 "))) == stamp.fingerprint_rows(
            rows(("s1", "S1"))
        )

    def test_empty_key_rows_are_skipped(self):
        assert stamp.fingerprint_rows(rows(("", "S1"))) == stamp.fingerprint_rows([])

    def test_stable_across_calls(self):
        data = rows(("s1", "S1"), ("s2", None))
        assert stamp.fingerprint_rows(data) == stamp.fingerprint_rows(data)


# -------------------------------------------------------------- 戳读写 --


class TestStampIO:
    def test_write_then_verify_ok(self, tmp_path):
        path = tmp_path / "stamp.json"
        stamp.write_stamp(
            stamp.KIND_MARGIN, fingerprint="sha256:aaa", units=42, path=path
        )
        status, message = stamp.verify(stamp.KIND_MARGIN, "sha256:aaa", path=path)
        assert status == stamp.STATUS_OK
        assert "有效" in message

    def test_missing_stamp_is_missing_not_stale(self, tmp_path):
        status, _ = stamp.verify(stamp.KIND_MARGIN, "sha256:aaa", path=tmp_path / "n.json")
        assert status == stamp.STATUS_MISSING

    def test_fingerprint_change_is_stale(self, tmp_path):
        path = tmp_path / "stamp.json"
        stamp.write_stamp(stamp.KIND_MARGIN, fingerprint="sha256:aaa", path=path)
        status, message = stamp.verify(stamp.KIND_MARGIN, "sha256:bbb", path=path)
        assert status == stamp.STATUS_STALE
        assert "过期" in message

    def test_kinds_do_not_overwrite_each_other(self, tmp_path):
        """只重跑提示词标定，不能顺手把带宽标定刷新成"有效"。"""
        path = tmp_path / "stamp.json"
        stamp.write_stamp(stamp.KIND_MARGIN, fingerprint="sha256:aaa", path=path)
        stamp.write_stamp(stamp.KIND_PROMPT, fingerprint="sha256:bbb", path=path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data[stamp.KIND_MARGIN]["fingerprint"] == "sha256:aaa"
        assert data[stamp.KIND_PROMPT]["fingerprint"] == "sha256:bbb"

        # 语料变了：带宽跟着过期，而提示词戳仍在（未被顺手覆盖）
        assert stamp.verify(stamp.KIND_MARGIN, "sha256:ccc", path=path)[0] == (
            stamp.STATUS_STALE
        )
        assert stamp.verify(stamp.KIND_PROMPT, "sha256:bbb", path=path)[0] == (
            stamp.STATUS_OK
        )

    def test_probe_mismatch_is_stale(self, tmp_path):
        path = tmp_path / "stamp.json"
        stamp.write_stamp(
            stamp.KIND_PROMPT,
            fingerprint="sha256:aaa",
            detail={"probe_fingerprint": "sha256:p1"},
            path=path,
        )
        ok, _ = stamp.verify(
            stamp.KIND_PROMPT, "sha256:aaa", probe="sha256:p1", path=path
        )
        assert ok == stamp.STATUS_OK
        stale, message = stamp.verify(
            stamp.KIND_PROMPT, "sha256:aaa", probe="sha256:p2", path=path
        )
        assert stale == stamp.STATUS_STALE
        assert "探针集" in message

    def test_probe_none_skips_probe_check(self, tmp_path):
        """探针集缺失（None）时不因探针判过期——否则离线环境会一直红。"""
        path = tmp_path / "stamp.json"
        stamp.write_stamp(
            stamp.KIND_PROMPT,
            fingerprint="sha256:aaa",
            detail={"probe_fingerprint": "sha256:p1"},
            path=path,
        )
        assert stamp.verify(stamp.KIND_PROMPT, "sha256:aaa", probe=None, path=path)[0] == (
            stamp.STATUS_OK
        )

    def test_corrupt_stamp_is_corrupt_not_missing(self, tmp_path):
        """损坏必须显式报损坏：静默当空会让"过期"退化成"从未标定"。"""
        path = tmp_path / "stamp.json"
        path.write_text("{not json", encoding="utf-8")
        status, message = stamp.verify(stamp.KIND_MARGIN, "sha256:aaa", path=path)
        assert status == stamp.STATUS_CORRUPT
        assert "损坏" in message

    def test_write_over_corrupt_stamp_rebuilds(self, tmp_path):
        """戳损坏不该卡住重跑标定：旧内容已无信息，直接重建。"""
        path = tmp_path / "stamp.json"
        path.write_text("{not json", encoding="utf-8")
        stamp.write_stamp(stamp.KIND_MARGIN, fingerprint="sha256:aaa", path=path)
        assert stamp.verify(stamp.KIND_MARGIN, "sha256:aaa", path=path)[0] == (
            stamp.STATUS_OK
        )

    def test_unknown_kind_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            stamp.write_stamp("nope", fingerprint="x", path=tmp_path / "s.json")

    def test_write_is_atomic_no_tmp_left(self, tmp_path):
        path = tmp_path / "stamp.json"
        stamp.write_stamp(stamp.KIND_MARGIN, fingerprint="sha256:aaa", path=path)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_written_file_ends_with_newline(self, tmp_path):
        """缺结尾换行会让下次 append/人工编辑把两行 JSON 粘成非法内容。"""
        path = tmp_path / "stamp.json"
        stamp.write_stamp(stamp.KIND_MARGIN, fingerprint="sha256:aaa", path=path)
        assert path.read_text(encoding="utf-8").endswith("\n")

    def test_detail_roundtrip(self, tmp_path):
        path = tmp_path / "stamp.json"
        stamp.write_stamp(
            stamp.KIND_MARGIN,
            fingerprint="sha256:aaa",
            units=50,
            detail={"recommended_margin": 0.05, "missed": 0},
            path=path,
        )
        entry = stamp.read_stamp(path)[stamp.KIND_MARGIN]
        assert entry["units"] == 50
        assert entry["detail"]["recommended_margin"] == 0.05
        assert entry["at"].endswith("Z")


class TestSnapshot:
    def test_stale_beats_missing(self):
        snap = stamp.StampSnapshot(
            margin=(stamp.STATUS_MISSING, ""), prompt=(stamp.STATUS_STALE, "")
        )
        assert snap.worst == stamp.STATUS_STALE

    def test_corrupt_beats_stale(self):
        snap = stamp.StampSnapshot(
            margin=(stamp.STATUS_CORRUPT, ""), prompt=(stamp.STATUS_STALE, "")
        )
        assert snap.worst == stamp.STATUS_CORRUPT

    def test_all_ok(self):
        snap = stamp.StampSnapshot(
            margin=(stamp.STATUS_OK, ""), prompt=(stamp.STATUS_OK, "")
        )
        assert snap.worst == stamp.STATUS_OK


# ------------------------------------------------------------ 变异测试 --


def _run_variant(tmp_path: Path, old: str, new: str) -> tuple[bool, str]:
    """把源码里 ``old`` 换成 ``new``，跑本文件的用例，返回 ``(是否全绿, 摘要)``。"""
    work = tmp_path / "mut"
    if work.exists():
        shutil.rmtree(work)
    for name in ("knowledge", "common", "config"):
        shutil.copytree(ROOT / name, work / name, ignore=shutil.ignore_patterns("__pycache__"))
    (work / "tests").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), work / "tests" / Path(__file__).name)

    target = work / "knowledge" / "calibration_stamp.py"
    source = target.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"变异锚点在源码中出现 {source.count(old)} 次，必须唯一：{old!r}")
    target.write_text(source.replace(old, new), encoding="utf-8")

    # 子进程里必须清掉开关：否则它会再跑一遍变异类 → 递归复制 → 永远跑不完
    # （第一次实现就踩了这个：表现为半小时后 SIGTERM，而不是任何断言失败）。
    env = {k: v for k, v in os.environ.items() if k != MUTATION_FLAG}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/" + Path(__file__).name, "-q", "-x"],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    summary = tail[-1] if tail else ""
    # 防止"变异把模块写坏导致 collection error"被当成"变异被捕获"：那种情况
    # 一条用例都没跑，断言全绿是假象。必须看到真正 failed 才算数。
    if "error" in summary and "failed" not in summary:
        raise AssertionError(f"变异导致收集期错误，不是断言捕获：{summary}")
    return proc.returncode == 0, summary


@pytest.mark.skipif(
    not os.environ.get(MUTATION_FLAG),
    reason=f"变异测试要复制 3 个包并跑 5 轮子进程（约 3-4 分钟），用 {MUTATION_FLAG}=1 触发",
)
class TestMutation:
    """变异若仍全绿，说明断言是空转的。这里逐个钉住关键判据。"""

    def test_sort_removed_is_caught(self, tmp_path):
        green, summary = _run_variant(tmp_path, "    lines.sort()\n", "")
        assert not green, f"删掉排序后仍全绿，行序判据没有被测住：{summary}"

    def test_null_marker_flattened_is_caught(self, tmp_path):
        green, summary = _run_variant(
            tmp_path, 'marker = "\\x00" if truth is None else str(truth).strip()',
            'marker = str(truth).strip() if truth is not None else ""',
        )
        # 后者的空串与"空真值"同码，null≠缺行这条应被检出
        assert not green, f"null 与空串混码后仍全绿：{summary}"

    def test_kind_isolation_broken_is_caught(self, tmp_path):
        green, summary = _run_variant(
            tmp_path, "    data[kind] = entry", "    data = {kind: entry}"
        )
        assert not green, f"写一类标定清掉另一类后仍全绿：{summary}"

    def test_probe_check_disabled_is_caught(self, tmp_path):
        green, summary = _run_variant(
            tmp_path, "if probe is not None and recorded_probe and recorded_probe != probe:",
            "if False:",
        )
        assert not green, f"关掉探针集比对后仍全绿：{summary}"

    def test_corrupt_becomes_missing_is_caught(self, tmp_path):
        green, summary = _run_variant(
            tmp_path,
            "    except ValueError as exc:\n        return STATUS_CORRUPT, str(exc)",
            "    except ValueError as exc:\n        return STATUS_MISSING, str(exc)",
        )
        assert not green, f"损坏退化成缺失后仍全绿：{summary}"
