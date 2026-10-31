# -*- coding: utf-8 -*-
"""黄忠事故台账工具单测（tmp 夹具，零网络，不触碰真实 work/ 台账）。

覆盖：add/list 往返 + 倒序 + limit + since；去重；commit 空/非法；ts 校验；
verify 合法/坏行/坏 ts/坏 commit/缺字段；stats；env 覆盖与优先级；缺失=empty ok；
不可读=fail-visible；演练纪律（正规台账拒绝、临时目录允许）；CLI JSON 出口。
"""
import datetime as dt
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import huangzhong_incidents as hi  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 10, 31, 12, 0, 0, tzinfo=TZ)


def _write_lines(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _good(ts="2026-10-30T10:00:00+08:00", commit="abcdef1234567890",
          kind="incident", desc="服务短暂不可用"):
    return {"ts": ts, "commit": commit, "kind": kind, "desc": desc,
            "source": "test", "added_at": "2026-10-30T10:05:00+08:00"}


# ---------------------------------------------------------------- add / 去重

def test_add_then_list_roundtrip(tmp_path):
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "abcdef1234567890",
                        "incident", "服务短暂不可用", source="docs/audit/x.md",
                        work_dir=tmp_path, now=NOW)
    assert rep["ok"] is True and rep["action"] == "appended"
    path = tmp_path / "incidents.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["ts"] == "2026-10-30T10:00:00+08:00"
    assert rec["commit"] == "abcdef1234567890"
    assert rec["kind"] == "incident"
    assert rec["source"] == "docs/audit/x.md"
    assert rec["added_at"].endswith("+08:00")

    out = hi.list_records(limit=20, work_dir=tmp_path)
    assert out["ok"] is True and out["total"] == 1 and out["matched"] == 1
    assert out["bad_lines"] == 0
    assert out["rows"][0]["desc"] == "服务短暂不可用"


def test_add_dedup_same_ts_commit_kind(tmp_path):
    kw = dict(ts="2026-10-30T10:00:00+08:00", commit="abcdef1234567890",
              kind="incident", desc="同一事故重复登记", work_dir=tmp_path, now=NOW)
    first = hi.add_record(**kw)
    second = hi.add_record(**kw)
    assert first["action"] == "appended"
    assert second["ok"] is True and second["action"] == "skipped"
    assert second["reason"] == "duplicate"
    assert len((tmp_path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_add_different_kind_not_dedup(tmp_path):
    hi.add_record("2026-10-30T10:00:00+08:00", "abcdef1234567890",
                  "incident", "a", work_dir=tmp_path, now=NOW)
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "abcdef1234567890",
                        "revert", "b", work_dir=tmp_path, now=NOW)
    assert rep["action"] == "appended"
    assert len((tmp_path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_add_empty_commit_allowed(tmp_path):
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "", "incident",
                        "未绑定 commit 的事故", work_dir=tmp_path, now=NOW)
    assert rep["ok"] is True and rep["record"]["commit"] == ""
    assert hi.verify_ledger(work_dir=tmp_path)["ok"] is True


def test_add_normalizes_ts_to_iso(tmp_path):
    rep = hi.add_record("2026-10-30 10:00:00", "", "incident", "空格格式时间",
                        work_dir=tmp_path, now=NOW)
    assert rep["ok"] is True
    assert rep["record"]["ts"].startswith("2026-10-30T10:00:00")


def test_add_rejects_invalid_ts(tmp_path):
    rep = hi.add_record("not-a-time", "", "incident", "x", work_dir=tmp_path, now=NOW)
    assert rep["ok"] is False and rep["error"] == "invalid_ts"
    assert not (tmp_path / "incidents.jsonl").exists()


def test_add_rejects_invalid_commit(tmp_path):
    for bad in ("xyz1234", "abc12", "a" * 41):
        rep = hi.add_record("2026-10-30T10:00:00+08:00", bad, "incident", "x",
                            work_dir=tmp_path, now=NOW)
        assert rep["ok"] is False and rep["error"] == "invalid_commit", bad
    assert not (tmp_path / "incidents.jsonl").exists()


def test_add_rejects_empty_kind_or_desc(tmp_path):
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "", "", "x", work_dir=tmp_path)
    assert rep["ok"] is False and rep["error"] == "invalid_kind"
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "", "incident", "  ",
                        work_dir=tmp_path)
    assert rep["ok"] is False and rep["error"] == "invalid_desc"


def test_add_only_writes_ledger_file(tmp_path):
    work = tmp_path / "work"
    hi.add_record("2026-10-30T10:00:00+08:00", "", "incident", "x",
                  work_dir=work, now=NOW)
    files = sorted(p.name for p in work.iterdir())
    assert files == ["incidents.jsonl"]


# ------------------------------------------------------------- 演练纪律

def test_drill_refused_in_production_ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("HUANGZHONG_WORK_DIR", raising=False)
    ledger = hi.DEFAULT_WORK_DIR / "incidents.jsonl"
    before = ledger.read_bytes() if ledger.exists() else None
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "", "drill", "演练：模拟事故",
                        work_dir=None, now=NOW)
    assert rep["ok"] is False
    assert rep["error"] == "drill_not_allowed_in_production_ledger"
    # 生产台账可能已建立（空台账正常存在）：只断言「未被本次演练写入」
    after = ledger.read_bytes() if ledger.exists() else None
    assert after == before


def test_drill_allowed_in_temp_workdir(tmp_path):
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "", "drill", "演练：临时目录",
                        work_dir=tmp_path, now=NOW)
    assert rep["ok"] is True and rep["action"] == "appended"
    assert hi.verify_ledger(work_dir=tmp_path)["rows"] == 1


# ------------------------------------------------------------------ list

def test_list_missing_file_is_empty_ok(tmp_path):
    out = hi.list_records(limit=5, work_dir=tmp_path)
    assert out["ok"] is True and out["exists"] is False and out["empty"] is True
    assert out["rows"] == [] and out["total"] == 0 and out["bad_lines"] == 0


def test_list_reverse_order_and_limit(tmp_path):
    for day in (29, 30, 31):
        hi.add_record("2026-10-%02dT10:00:00+08:00" % day, "", "incident",
                      "day %d" % day, work_dir=tmp_path, now=NOW)
    out = hi.list_records(limit=2, work_dir=tmp_path)
    assert out["total"] == 3 and out["matched"] == 3
    assert len(out["rows"]) == 2
    assert out["rows"][0]["ts"].startswith("2026-10-31")
    assert out["rows"][1]["ts"].startswith("2026-10-30")


def test_list_since_filter(tmp_path):
    hi.add_record("2026-10-01T10:00:00+08:00", "", "incident", "old",
                  work_dir=tmp_path, now=NOW)
    hi.add_record("2026-10-30T10:00:00+08:00", "", "incident", "new",
                  work_dir=tmp_path, now=NOW)
    out = hi.list_records(limit=20, since="2026-10-15", work_dir=tmp_path)
    assert out["ok"] is True and out["matched"] == 1
    assert out["rows"][0]["desc"] == "new"


def test_list_rejects_invalid_since_and_limit(tmp_path):
    assert hi.list_records(limit=5, since="不存在的日期", work_dir=tmp_path)["ok"] is False
    assert hi.list_records(limit=0, work_dir=tmp_path)["error"] == "invalid_limit"
    assert hi.list_records(limit=-3, work_dir=tmp_path)["error"] == "invalid_limit"


def test_list_counts_bad_lines(tmp_path):
    _write_lines(tmp_path / "incidents.jsonl", [
        json.dumps(_good()),
        "this is not json",
        json.dumps([1, 2, 3]),
        json.dumps(_good(ts="2026-10-31T10:00:00+08:00")),
    ])
    out = hi.list_records(limit=20, work_dir=tmp_path)
    assert out["ok"] is True and out["total"] == 2 and out["bad_lines"] == 2
    assert len(out["rows"]) == 2


# ------------------------------------------------------------------ verify

def test_verify_missing_is_empty_ok(tmp_path):
    rep = hi.verify_ledger(work_dir=tmp_path)
    assert rep["ok"] is True and rep["exists"] is False
    assert rep["rows"] == 0 and rep["issues"] == []


def test_verify_valid_ledger(tmp_path):
    _write_lines(tmp_path / "incidents.jsonl", [
        json.dumps(_good()),
        json.dumps(_good(commit="", kind="revert", desc="回滚一次配置变更")),
    ])
    rep = hi.verify_ledger(work_dir=tmp_path)
    assert rep["ok"] is True
    assert rep["rows"] == 2 and rep["bad_lines"] == 0
    assert rep["issues"] == [] and rep["issue_count"] == 0


def test_verify_flags_bad_ts_commit_fields_and_bad_line(tmp_path):
    _write_lines(tmp_path / "incidents.jsonl", [
        json.dumps(_good()),
        json.dumps({"ts": "bad-time", "commit": "", "kind": "revert", "desc": "坏 ts"}),
        json.dumps({"ts": "2026-10-30T10:00:00+08:00", "commit": "zzz",
                    "kind": "incident", "desc": "坏 commit"}),
        json.dumps({"ts": "2026-10-30T10:00:00+08:00", "commit": "",
                    "desc": "缺 kind"}),
        json.dumps({"ts": "2026-10-30T10:00:00+08:00", "commit": "",
                    "kind": "incident", "desc": "坏 added_at",
                    "added_at": "???"}),
        "broken json line",
    ])
    rep = hi.verify_ledger(work_dir=tmp_path)
    assert rep["ok"] is False
    assert rep["rows"] == 5 and rep["bad_lines"] == 1
    codes = [i["code"] for i in rep["issues"]]
    assert codes.count("bad_ts") == 1
    assert codes.count("bad_commit") == 1
    assert codes.count("missing_kind") == 1
    assert codes.count("bad_added_at") == 1


def test_verify_empty_existing_file_ok(tmp_path):
    (tmp_path / "incidents.jsonl").write_text("", encoding="utf-8")
    rep = hi.verify_ledger(work_dir=tmp_path)
    assert rep["ok"] is True and rep["exists"] is True and rep["rows"] == 0


# ------------------------------------------------------------------ stats

def test_stats_missing_all_zero(tmp_path):
    rep = hi.stats_ledger(work_dir=tmp_path, now=NOW)
    assert rep["ok"] is True and rep["exists"] is False
    assert rep["total"] == 0 and rep["by_kind"] == {}
    assert rep["commit_unique"] == 0 and rep["recent_30d"] == 0


def test_stats_counts(tmp_path):
    _write_lines(tmp_path / "incidents.jsonl", [
        json.dumps(_good(ts="2026-10-30T10:00:00+08:00", commit="abcdef123456",
                         kind="incident")),
        json.dumps(_good(ts="2026-10-29T10:00:00+08:00", commit="abcdef12ffff",
                         kind="incident")),          # 与上一条同 8 位前缀
        json.dumps(_good(ts="2026-10-20T10:00:00+08:00", commit="1234567abcde",
                         kind="revert")),
        json.dumps(_good(ts="2026-09-01T10:00:00+08:00", commit="",
                         kind="other")),             # 超 30 天 + 未绑定
        json.dumps(_good(ts="2026-10-30T11:00:00+08:00", commit="deadbeef00",
                         kind="outage")),
    ])
    rep = hi.stats_ledger(work_dir=tmp_path, now=NOW)
    assert rep["ok"] is True and rep["total"] == 5
    assert rep["bad_lines"] == 0 and rep["bad_ts"] == 0
    assert rep["by_kind"]["incident"] == 2
    assert rep["by_kind"]["revert"] == 1
    assert rep["by_kind"]["outage"] == 1
    assert rep["by_kind"]["other"] == 1
    assert rep["commit_unique"] == 3          # abcdef12 / 1234567a / deadbeef
    assert rep["unattributed"] == 1
    assert rep["recent_30d"] == 4             # 9-01 那条超出 30 天（now=10-31）


# ----------------------------------------------------- env 覆盖 / fail-visible

def test_env_override_work_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HUANGZHONG_WORK_DIR", str(tmp_path))
    rep = hi.add_record("2026-10-30T10:00:00+08:00", "", "incident", "env 覆盖",
                        work_dir=None, now=NOW)
    assert rep["ok"] is True
    assert (tmp_path / "incidents.jsonl").exists()
    assert hi.list_records(limit=5, work_dir=None)["total"] == 1


def test_explicit_work_dir_beats_env(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("HUANGZHONG_WORK_DIR", str(env_dir))
    hi.add_record("2026-10-30T10:00:00+08:00", "", "incident", "显式优先",
                  work_dir=explicit, now=NOW)
    assert (explicit / "incidents.jsonl").exists()
    assert not (env_dir / "incidents.jsonl").exists()


def test_unreadable_ledger_is_error(tmp_path):
    # 用同名目录占位模拟不可读：读字节会抛 OSError（Windows 为 PermissionError）
    (tmp_path / "incidents.jsonl").mkdir()
    rep = hi.list_records(limit=5, work_dir=tmp_path)
    assert rep["ok"] is False and rep["error"] == "ledger_unreadable"
    ver = hi.verify_ledger(work_dir=tmp_path)
    assert ver["ok"] is False and ver["error"] == "ledger_unreadable"
    add = hi.add_record("2026-10-30T10:00:00+08:00", "", "incident", "x",
                        work_dir=tmp_path, now=NOW)
    assert add["ok"] is False and add["error"] == "ledger_unreadable"


# -------------------------------------------------------------------- CLI

def test_cli_list_missing_json(tmp_path, capsys):
    code = hi.main(["list", "--limit", "5", "--json", "--work-dir", str(tmp_path)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["exists"] is False
    assert payload["rows"] == []


def test_cli_add_then_verify_stats(tmp_path, capsys):
    work = str(tmp_path)
    code = hi.main(["add", "--ts", "2026-10-30T10:00:00+08:00", "--commit",
                    "abcdef123456", "--kind", "incident", "--desc", "CLI 写入",
                    "--source", "cli-test", "--work-dir", work])
    assert code == 0
    capsys.readouterr()
    code = hi.main(["verify", "--json", "--work-dir", work])
    assert code == 0
    ver = json.loads(capsys.readouterr().out)
    assert ver["ok"] is True and ver["rows"] == 1
    code = hi.main(["stats", "--json", "--work-dir", work])
    assert code == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["total"] == 1 and stats["commit_unique"] == 1


def test_cli_verify_failed_exit_code_1(tmp_path, capsys):
    _write_lines(tmp_path / "incidents.jsonl", ["bad line", json.dumps(_good())])
    code = hi.main(["verify", "--json", "--work-dir", str(tmp_path)])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["bad_lines"] == 1


def test_cli_add_invalid_commit_exit_code_2(tmp_path, capsys):
    code = hi.main(["add", "--ts", "2026-10-30T10:00:00+08:00", "--commit", "zz",
                    "--kind", "incident", "--desc", "bad", "--json",
                    "--work-dir", str(tmp_path)])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["error"] == "invalid_commit"
