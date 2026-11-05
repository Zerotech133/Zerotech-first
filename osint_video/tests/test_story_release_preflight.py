"""上线前自检：钉住"什么时候该红、红了之后给出什么"。

这个脚本最容易被写成"永远绿"的样子——每项检查都调一次、都记一笔，最后打印
一堆 OK。所以测试的重心不是"能跑通"，而是**失败路径**：

1. 标定戳缺失/过期/损坏必须 FAIL，且给出可执行的修复命令；
2. 备份摘要不符必须 FAIL（"备份存在"与"备份可用"是两件事）；
3. 从未备份只是 WARN（第一次跑必然如此，一上来满屏红就没人看了）；
4. 队列积压是 WARN 而不是 FAIL（积压要人处理，不是系统停机）；
5. 待裁定项（``STORY_BORDERLINE_JUDGE=off``）显示为绿 + 标注，不替人拍板。

DB 一律不打真库：``corpus_fingerprint`` 与 ``queue_stats`` 的输入都用
monkeypatch 注入，使这些判据能在离线环境重复验证。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import story_release_preflight as pf  # noqa: E402
from knowledge import calibration_stamp as stamp  # noqa: E402


# --------------------------------------------------------------- 夹具 --


@pytest.fixture
def sandbox(tmp_path):
    """三件状态文件 + 独立备份根目录，全部落在 tmp 里。"""
    data = tmp_path / "data"
    data.mkdir()
    files = [
        data / "story_borderline_overrides.json",
        data / "story_borderline_pending.jsonl",
        data / "story_borderline_pending.jsonl.decided.jsonl",
    ]
    files[0].write_text('{"a||b": {"decision": true}}\n', encoding="utf-8")
    files[1].write_text(
        '{"left": "a", "right": "b", "queued_at": "2026-11-05T00:00:00Z"}\n',
        encoding="utf-8",
    )
    return {"root": tmp_path / "backups", "files": files, "data": data}


def _by_name(report: pf.Report) -> dict[str, pf.Check]:
    return {c.name: c for c in report.checks}


def _stamp_file(tmp_path: Path, *, margin=None, prompt=None, probe="sha256:probe") -> Path:
    path = tmp_path / "stamp.json"
    if margin is not None:
        stamp.write_stamp(stamp.KIND_MARGIN, fingerprint=margin, units=50, path=path)
    if prompt is not None:
        stamp.write_stamp(
            stamp.KIND_PROMPT,
            fingerprint=prompt,
            units=50,
            detail={"probe_fingerprint": probe},
            path=path,
        )
    return path


# ---------------------------------------------------------- 标定检查 --


class TestCalibration:
    """标定检查。语料/探针指纹一律注入，不连真库——这几条判据必须能离线复现。"""

    @pytest.fixture(autouse=True)
    def _fake_inputs(self, monkeypatch):
        monkeypatch.setattr(stamp, "corpus_fingerprint", lambda: ("sha256:corpus", 50))
        monkeypatch.setattr(stamp, "probe_fingerprint", lambda path=None: "sha256:probe")

    def test_missing_stamp_is_fail_with_command(self, tmp_path, monkeypatch):
        monkeypatch.setattr(stamp, "stamp_path", lambda p=None: tmp_path / "none.json")
        report = pf.Report()
        pf.check_calibration(report)
        checks = _by_name(report)
        assert checks["calibration.borderline_margin"].level == pf.LEVEL_FAIL
        assert "calibrate_borderline_margin.py" in checks["calibration.borderline_margin"].action
        assert checks["calibration.judge_prompt"].level == pf.LEVEL_FAIL
        assert "calibrate_judge_prompt.py" in checks["calibration.judge_prompt"].action

    def test_stale_stamp_is_fail(self, tmp_path, monkeypatch):
        path = _stamp_file(tmp_path, margin="sha256:old", prompt="sha256:old")
        monkeypatch.setattr(stamp, "stamp_path", lambda p=None: path)
        report = pf.Report()
        pf.check_calibration(report)
        checks = _by_name(report)
        assert checks["calibration.borderline_margin"].level == pf.LEVEL_FAIL
        assert "过期" in checks["calibration.borderline_margin"].title

    def test_fresh_stamp_is_ok(self, tmp_path, monkeypatch):
        path = _stamp_file(tmp_path, margin="sha256:corpus", prompt="sha256:corpus")
        monkeypatch.setattr(stamp, "stamp_path", lambda p=None: path)
        report = pf.Report()
        pf.check_calibration(report)
        checks = _by_name(report)
        assert checks["calibration.borderline_margin"].level == pf.LEVEL_OK
        assert checks["calibration.judge_prompt"].level == pf.LEVEL_OK
        assert report.failed == []

    def test_probe_change_is_stale(self, tmp_path, monkeypatch):
        """探针集换了也算依据变更——提示词标定的输入含探针集。"""
        path = _stamp_file(
            tmp_path, margin="sha256:corpus", prompt="sha256:corpus", probe="sha256:old-probe"
        )
        monkeypatch.setattr(stamp, "stamp_path", lambda p=None: path)
        report = pf.Report()
        pf.check_calibration(report)
        assert _by_name(report)["calibration.judge_prompt"].level == pf.LEVEL_FAIL

    def test_corrupt_stamp_reports_corrupt(self, tmp_path, monkeypatch):
        path = tmp_path / "stamp.json"
        path.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(stamp, "stamp_path", lambda p=None: path)
        report = pf.Report()
        pf.check_calibration(report)
        check = _by_name(report)["calibration.borderline_margin"]
        assert check.level == pf.LEVEL_FAIL
        assert "损坏" in check.title

    def test_db_down_is_skip_not_fail(self, tmp_path, monkeypatch):
        """库不可用只是"无法核对"，不能断言标定失效——否则离线跑必红。"""
        from common.db import DatabaseUnavailableError

        def boom():
            raise DatabaseUnavailableError("no db")

        monkeypatch.setattr(stamp, "corpus_fingerprint", boom)
        report = pf.Report()
        pf.check_calibration(report)
        assert report.failed == []
        assert _by_name(report)["calibration"].level == pf.LEVEL_SKIP


# ------------------------------------------------------------ 备份检查 --


class TestBackup:
    def test_never_backed_up_is_warn_with_command(self, tmp_path, sandbox):
        report = pf.Report()
        pf.check_backup(report, sandbox["files"], sandbox["root"], 7.0)
        checks = _by_name(report)
        assert checks["backup.age"].level == pf.LEVEL_WARN
        assert "--backup" in checks["backup.age"].action
        # 没备份可校验 ≠ 备份坏了
        assert checks["backup.integrity"].level == pf.LEVEL_SKIP
        assert report.failed == []

    def test_fresh_backup_is_ok(self, tmp_path, sandbox):
        created = pf.do_backup(sandbox["root"], sandbox["files"])
        assert created is not None
        report = pf.Report()
        pf.check_backup(report, sandbox["files"], sandbox["root"], 7.0)
        checks = _by_name(report)
        assert checks["backup.integrity"].level == pf.LEVEL_OK
        assert checks["backup.age"].level == pf.LEVEL_OK

    def test_tampered_backup_is_fail(self, tmp_path, sandbox):
        """摘要不符必须红：这是"备份存在"与"备份可用"的分界。"""
        created = pf.do_backup(sandbox["root"], sandbox["files"])
        victim = created / "story_borderline_overrides.json"
        victim.write_text('{"x": 1}\n', encoding="utf-8")

        report = pf.Report()
        pf.check_backup(report, sandbox["files"], sandbox["root"], 7.0)
        check = _by_name(report)["backup.integrity"]
        assert check.level == pf.LEVEL_FAIL
        assert "摘要不符" in check.detail

    def test_missing_file_inside_backup_is_fail(self, tmp_path, sandbox):
        created = pf.do_backup(sandbox["root"], sandbox["files"])
        (created / "story_borderline_pending.jsonl").unlink()
        report = pf.Report()
        pf.check_backup(report, sandbox["files"], sandbox["root"], 7.0)
        assert _by_name(report)["backup.integrity"].level == pf.LEVEL_FAIL

    def test_old_backup_is_warn(self, tmp_path, sandbox):
        created = pf.do_backup(sandbox["root"], sandbox["files"])
        # 把目录名改成 30 天前：新鲜度用目录名判，不用 mtime
        old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y%m%dT%H%M%SZ")
        created.rename(created.parent / old)
        report = pf.Report()
        pf.check_backup(report, sandbox["files"], sandbox["root"], 7.0)
        check = _by_name(report)["backup.age"]
        assert check.level == pf.LEVEL_WARN
        assert "30.0 天" in check.title

    def test_backup_only_copies_existing_files(self, tmp_path):
        """不造空文件：台账文件的"存在"本身就是"启用"的语义。"""
        data = tmp_path / "data"
        data.mkdir()
        only = data / "pending.jsonl"
        only.write_text("", encoding="utf-8")
        absent = data / "overrides.json"
        created = pf.do_backup(tmp_path / "bk", [only, absent])
        assert created is not None
        assert (created / "pending.jsonl").exists()
        assert not (created / "overrides.json").exists()

    def test_backup_returns_none_when_nothing_to_copy(self, tmp_path):
        assert pf.do_backup(tmp_path / "bk", [tmp_path / "nope.json"]) is None

    def test_two_backups_in_same_second_do_not_overwrite(self, tmp_path):
        """同一秒跑两次：第二次必须另起目录，否则静默吃掉第一次。"""
        source = tmp_path / "s.json"
        source.write_text("{}", encoding="utf-8")
        first = pf.do_backup(tmp_path / "bk", [source])
        second = pf.do_backup(tmp_path / "bk", [source])
        assert first != second
        assert second.exists()

    def test_manifest_records_sha256_and_size(self, tmp_path):
        source = tmp_path / "s.json"
        source.write_text('{"k": 1}\n', encoding="utf-8")
        created = pf.do_backup(tmp_path / "bk", [source])
        manifest = json.loads((created / "MANIFEST.json").read_text(encoding="utf-8"))
        entry = manifest["files"][0]
        assert entry["name"] == "s.json"
        assert entry["bytes"] == source.stat().st_size
        assert len(entry["sha256"]) == 64


# ------------------------------------------------------------ 配置检查 --


class TestConfig:
    def test_docked_values_are_ok(self, monkeypatch):
        class S:
            story_borderline_margin = 0.05
            judge_prompt_variant = "v2"
            story_borderline_judge = "off"
            story_cross_window = False

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_config(report)
        checks = _by_name(report)
        assert checks["config.docked"].level == pf.LEVEL_OK
        assert checks["config.borderline_judge"].level == pf.LEVEL_OK

    def test_margin_drift_is_warn(self, monkeypatch):
        class S:
            story_borderline_margin = 0.10
            judge_prompt_variant = "v2"
            story_borderline_judge = "off"
            story_cross_window = False

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_config(report)
        check = _by_name(report)["config.docked"]
        assert check.level == pf.LEVEL_WARN
        assert "0.1" in check.detail

    def test_variant_drift_is_warn(self, monkeypatch):
        class S:
            story_borderline_margin = 0.05
            judge_prompt_variant = "v1"
            story_borderline_judge = "off"
            story_cross_window = False

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_config(report)
        assert _by_name(report)["config.docked"].level == pf.LEVEL_WARN

    def test_judge_off_is_green_but_flagged_pending_decision(self, monkeypatch):
        """待裁定项不该报红：自检不替人拍板，但必须让人看见当前形态。"""

        class S:
            story_borderline_margin = 0.05
            judge_prompt_variant = "v2"
            story_borderline_judge = "off"
            story_cross_window = False

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_config(report)
        check = _by_name(report)["config.borderline_judge"]
        assert check.level == pf.LEVEL_OK
        assert "待裁定" in check.title

    def test_cross_window_on_is_stated_in_report(self, monkeypatch):
        """跨窗会改变事件数，必须出现在自检里，否则线上事件数变了无从解释。"""

        class S:
            story_borderline_margin = 0.05
            judge_prompt_variant = "v2"
            story_borderline_judge = "llm"
            story_cross_window = True

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_config(report)
        check = _by_name(report)["config.cross_window"]
        assert check.level == pf.LEVEL_OK
        assert "已开启" in check.title

    def test_cross_window_off_is_green_but_flagged_pending_decision(self, monkeypatch):
        """关着不是缺陷，但要让看报告的人知道"跨月事件会被切成两个"。"""

        class S:
            story_borderline_margin = 0.05
            judge_prompt_variant = "v2"
            story_borderline_judge = "off"
            story_cross_window = False

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_config(report)
        check = _by_name(report)["config.cross_window"]
        assert check.level == pf.LEVEL_OK
        assert "待裁定" in check.title


# ---------------------------------------------------------- 积压检查 --


class TestBacklog:
    def test_no_queue_file_is_ok(self, tmp_path, monkeypatch):
        class S:
            story_borderline_pending = str(tmp_path / "none.jsonl")
            story_borderline_overrides = str(tmp_path / "o.json")
            story_review_backlog_max = 20
            story_review_max_age_days = 14.0

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_backlog(report)
        assert _by_name(report)["backlog"].level == pf.LEVEL_OK

    def test_over_threshold_is_warn(self, tmp_path, monkeypatch):
        queue = tmp_path / "q.jsonl"
        old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        queue.write_text(
            json.dumps({"left": "a", "right": "b", "queued_at": old}) + "\n",
            encoding="utf-8",
        )

        class S:
            story_borderline_pending = str(queue)
            story_borderline_overrides = str(tmp_path / "o.json")
            story_review_backlog_max = 20
            story_review_max_age_days = 14.0

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_backlog(report)
        check = _by_name(report)["backlog"]
        assert check.level == pf.LEVEL_WARN
        assert "compact" in check.action

    def test_single_bad_record_is_warn_not_fail(self, tmp_path, monkeypatch):
        """单条坏记录被加载器跳过且不抛错——自检必须靠"条数不对"把它抓出来。

        这是本轮发现的真缺口：``load_overrides`` 对单条坏记录只记日志，调用方
        看到的只是一个少了一条的 dict。若不自检条数，人拍过的板被无声推翻。
        """
        queue = tmp_path / "q.jsonl"
        queue.write_text('{"left": "a", "right": "b"}\n', encoding="utf-8")
        bad = tmp_path / "o.json"
        bad.write_text('{"a||b": "maybe"}', encoding="utf-8")

        class S:
            story_borderline_pending = str(queue)
            story_borderline_overrides = str(bad)
            story_review_backlog_max = 20
            story_review_max_age_days = 14.0

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_backlog(report)
        check = _by_name(report)["overrides.coverage"]
        assert check.level == pf.LEVEL_WARN
        assert "1 条裁决未生效" in check.title

    def test_whole_file_corruption_is_fail(self, tmp_path, monkeypatch):
        """整份 JSON 坏了才抛异常：那种情况连积压都没法统计。"""
        queue = tmp_path / "q.jsonl"
        queue.write_text('{"left": "a", "right": "b"}\n', encoding="utf-8")
        bad = tmp_path / "o.json"
        bad.write_text("{ not json", encoding="utf-8")

        class S:
            story_borderline_pending = str(queue)
            story_borderline_overrides = str(bad)
            story_review_backlog_max = 20
            story_review_max_age_days = 14.0

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_backlog(report)
        assert _by_name(report)["backlog"].level == pf.LEVEL_FAIL

    def test_clean_overrides_reports_ok(self, tmp_path, monkeypatch):
        queue = tmp_path / "q.jsonl"
        queue.write_text('{"left": "a", "right": "b"}\n', encoding="utf-8")
        good = tmp_path / "o.json"
        good.write_text('{"a||b": {"decision": true}}', encoding="utf-8")

        class S:
            story_borderline_pending = str(queue)
            story_borderline_overrides = str(good)
            story_review_backlog_max = 20
            story_review_max_age_days = 14.0

        monkeypatch.setattr(pf, "get_settings", lambda: S())
        report = pf.Report()
        pf.check_backlog(report)
        assert _by_name(report)["overrides.coverage"].level == pf.LEVEL_OK


# -------------------------------------------------------------- 退出码 --


class TestExitCode:
    def _report(self, levels) -> pf.Report:
        report = pf.Report()
        for i, level in enumerate(levels):
            report.add(pf.Check(f"c{i}", level, f"t{i}"))
        return report

    def test_fail_returns_1(self, monkeypatch):
        monkeypatch.setattr(
            pf, "build_report", lambda **kw: self._report([pf.LEVEL_OK, pf.LEVEL_FAIL])
        )
        assert pf.main([]) == 1

    def test_all_ok_returns_0(self, monkeypatch):
        monkeypatch.setattr(pf, "build_report", lambda **kw: self._report([pf.LEVEL_OK]))
        assert pf.main([]) == 0

    def test_warn_only_returns_0(self, monkeypatch):
        monkeypatch.setattr(
            pf, "build_report", lambda **kw: self._report([pf.LEVEL_WARN, pf.LEVEL_OK])
        )
        assert pf.main([]) == 0

    def test_db_skip_returns_2(self, monkeypatch):
        report = self._report([pf.LEVEL_OK])
        report.add(pf.Check("calibration", pf.LEVEL_SKIP, "库不可用"))
        monkeypatch.setattr(pf, "build_report", lambda **kw: report)
        assert pf.main([]) == 2

    def test_json_output_is_parseable(self, monkeypatch, capsys):
        monkeypatch.setattr(
            pf, "build_report", lambda **kw: self._report([pf.LEVEL_OK])
        )
        assert pf.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["ok"] == 1
