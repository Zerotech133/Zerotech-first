# -*- coding: utf-8 -*-
"""WO-OS-27 契约测试：真实证据供数器（先红后绿）。

运行：python -m pytest portal/test_gen_result_evidence.py -q
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

BEIJING = timezone(timedelta(hours=8))
DAY = "2026-10-21"


def _gm(day=DAY, n=2):
    """假 git 输出：n 条目标日提交 + 1 条前一日（应被过滤）。"""
    rows = []
    for i in range(n):
        ts = "%sT%02d:1%d:00+08:00" % (day, 9 + i, i)
        rows.append("abc1234%x\x1f%s\x1fWO 工单 %d" % (i, ts, i))
    rows.append("deadbeef\x1f2026-10-20T23:00:00+08:00\x1f前一日不应计入")
    return "\n".join(rows)


def _runner_factory(out=None, rc=0):
    calls = []

    def runner(argv):
        calls.append(argv)
        return rc, (out if out is not None else "")

    runner.calls = calls
    return runner


# ① 提交映射：六字段齐全 + receipt 必为 None（不冒充已验收）
def test_commit_maps_to_unverified_evidence():
    import gen_result_evidence as g
    runner = _runner_factory(_gm(n=2))
    rows = g.collect_commits(DAY, limit=20, runner=runner)
    assert len(rows) == 2, "只应保留目标日的 2 条"
    one = rows[0]
    for key in ("id", "domain", "business_day", "producer_time",
                "outcome", "evidence_uri"):
        assert key in one and one[key], "缺关键字段 %s" % key
    assert one["outcome"] == "done"
    assert one["business_day"] == DAY
    assert one["receipt_uri"] is None, "自证不是复核回执，必须留空"
    assert one["evidence_uri"].startswith("commit:")


# ② 跨日过滤：北京日边界外的提交不进
def test_commit_filters_other_days():
    import gen_result_evidence as g
    out = ("aaa\x1f2026-10-20T23:59:59+08:00\x1f昨日\n"
           "bbb\x1f2026-10-21T00:00:01+08:00\x1f今日凌晨\n"
           "ccc\x1f2026-10-22T00:00:01+08:00\x1f明日\n")
    rows = g.collect_commits(DAY, runner=_runner_factory(out))
    assert [r["id"] for r in rows] == ["commit:bbb"], "只留北京日内的提交"


# ③ git 失败 → 空列表（不抛、不造数）
def test_commit_git_failure_returns_empty():
    import gen_result_evidence as g
    assert g.collect_commits(DAY, runner=_runner_factory("", rc=128)) == []


# ④ 交付物按 mtime 归日
def test_deliverables_by_mtime(tmp_path):
    import gen_result_evidence as g
    import os
    today_file = tmp_path / "今日交付.md"
    today_file.write_text("x", encoding="utf-8")
    old_file = tmp_path / "昨日交付.md"
    old_file.write_text("x", encoding="utf-8")
    # 把旧文件 mtime 设为前一日
    old_ts = datetime(2026, 10, 20, 12, 0, tzinfo=BEIJING).timestamp()
    os.utime(old_file, (old_ts, old_ts))
    rows = g.collect_deliverables(DAY, root=tmp_path)
    names = [r["evidence_uri"] for r in rows]
    assert "deliverables/今日交付.md" in names
    assert "deliverables/昨日交付.md" not in names
    assert rows[0]["outcome"] == "delivered"
    assert rows[0]["receipt_uri"] is None


# ⑤ 合并载荷：结构 + 诚实 note + 排序无关但条数对
def test_build_payload_shape(tmp_path):
    import gen_result_evidence as g
    payload = g.build_payload(DAY, limit=5, runner=_runner_factory(_gm(n=2)),
                              deliverables_root=tmp_path)
    assert payload["ok"] is True
    assert payload["business_day"] == DAY
    assert payload["source"] == "git-commits+deliverables"
    assert "unverified" in payload["note"] or "未走独立复核" in payload["note"]
    assert isinstance(payload["items"], list) and len(payload["items"]) == 2
    assert all(x["receipt_uri"] is None for x in payload["items"])


# ⑤b. WO-OS-28 截断必须可见：total_available 是真实总量，limit 是产物上限
def test_payload_reports_truncation_honestly(tmp_path):
    """实测风险：真实规模 179 项而产物只 40 条，页面若不提示截断，
    读者会把 40 条当成全天结果——与"不造数"基调自相矛盾。"""
    import gen_result_evidence as g
    payload = g.build_payload(DAY, limit=1, runner=_runner_factory(_gm(n=3)),
                              deliverables_root=tmp_path)
    assert payload["limit"] == 1
    assert payload["total_available"] == 3, "总量须为未截断的真实条数"
    assert len(payload["items"]) == 1, "items 受 limit 约束"
    assert payload["total_available"] >= len(payload["items"])
    assert "截断" in payload["note"], "note 必须显式说明这是截断视图"
    assert "非当日全部工作" in payload["note"]


# ⑤c. 无截断时总量与条数相等
def test_payload_no_truncation_when_under_limit(tmp_path):
    import gen_result_evidence as g
    payload = g.build_payload(DAY, limit=50, runner=_runner_factory(_gm(n=2)),
                              deliverables_root=tmp_path)
    assert payload["total_available"] == len(payload["items"]) == 2


# ⑤d. WO-OS-29 两源必须交错：忙碌日不能全被提交占满、交付物永不出现
def test_payload_interleaves_commits_and_docs(tmp_path):
    """实测风险：items = commits[:limit] + docs[:limit] 时，忙碌日（145 提交）
    前 8 条全是提交，交付物证据在页面上永不出现，读者会以为交付物为零。
    修法：合并后按 producer_time 倒序，两源按真实时间交错。
    """
    import gen_result_evidence as g
    import os
    # 造 5 份交付物（时点分散在 08:00~12:00）
    for i in range(5):
        p = tmp_path / ("交付%d.md" % i)
        p.write_text("x", encoding="utf-8")
        ts = datetime(2026, 10, 21, 8 + i, 0, tzinfo=BEIJING).timestamp()
        os.utime(p, (ts, ts))
    # 造 5 条提交（时点 09:10~13:10，与交付物交错）
    lines = []
    for i in range(5):
        lines.append("abc%02d\x1f2026-10-21T%02d:10:00+08:00\x1f提交 %d" % (i, 9 + i, i))
    payload = g.build_payload(DAY, limit=4, runner=_runner_factory("\n".join(lines)),
                              deliverables_root=tmp_path)
    kinds = ["commit" if x["id"].startswith("commit:") else "doc"
             for x in payload["items"]]
    assert "doc" in kinds, "交付物必须有代表进入前 limit 条，实得：" + str(kinds)
    assert "commit" in kinds, "提交也必须有代表"
    # 时间必须严格倒序
    times = [x["producer_time"] for x in payload["items"]]
    assert times == sorted(times, reverse=True), "合并后应按生产者时点倒序"
    assert len(payload["items"]) == 4


# ⑥ dry-run 不写盘；正常写入 today.json
def test_dry_run_and_write(tmp_path):
    import gen_result_evidence
    rc = gen_result_evidence.main(["--dry-run", "--day", DAY, "--out-dir", str(tmp_path)])
    assert rc == 0
    assert not (tmp_path / "today.json").exists(), "dry-run 不得写盘"

    rc = gen_result_evidence.main(["--day", DAY, "--out-dir", str(tmp_path), "--limit", "3"])
    assert rc == 0
    target = tmp_path / "today.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["business_day"] == DAY


# ⑦ 坏参数拒绝
def test_bad_args_rejected(tmp_path):
    import gen_result_evidence
    assert gen_result_evidence.main(["--day", "2026-13-40", "--dry-run"]) == 2
    assert gen_result_evidence.main(["--day", DAY, "--limit", "0", "--dry-run"]) == 2


# ⑧ 源码禁项：不写仓库、不碰业务账本、不发通知
def test_source_has_no_write_side_effects():
    src = (HERE / "gen_result_evidence.py").read_text(encoding="utf-8")
    for banned in ("requests.", "urllib.request", "smtplib", "schedules.json",
                   "push_flow.jsonl", "git commit", "git push", "os.remove",
                   "shutil.rmtree"):
        assert banned not in src, "生产代码不得出现 %s" % banned
    # 唯一写盘点应是产物 JSON
    assert src.count("write_text(") == 1
