# -*- coding: utf-8 -*-
"""tools/intel_entity_extract.py 定向测试（WO-INTEL-ENTITY-20261103）。

覆盖：多板归一、名称候选不升级、去重、坏行 fail-visible、空文本、staging
幂等、只读断言、覆盖率口径、抽样确定性、CLI rc 契约。
全部用 temp sqlite fixture，不碰仓库真实 news.db / 主区。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import intel_entity_extract as m  # noqa: E402

TOOL = ROOT / "tools" / "intel_entity_extract.py"
PY = sys.executable

SCHEMA = """
CREATE TABLE news (
    id TEXT PRIMARY KEY,
    source TEXT,
    ts TEXT,
    title TEXT,
    content TEXT,
    created_at TEXT,
    stocks TEXT,
    jev TEXT
)
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_db(path: Path, rows: list[tuple]) -> Path:
    """rows: (id, source, ts, title, content, created_at, stocks, jev)"""
    con = sqlite3.connect(str(path))
    try:
        con.execute(SCHEMA)
        con.executemany(
            "INSERT INTO news(id, source, ts, title, content, created_at, stocks, jev) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        con.commit()
    finally:
        con.close()
    return path


def row(i, *, title="", content=None, stocks=None, source="sina", ts="2026-11-01 10:00"):
    return (str(i), source, ts, title, content, "2026-11-01 10:05", stocks, None)


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [PY, "-X", "utf8", str(TOOL), *args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, env=env,
    )


def kv(out: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line and not line.startswith("["):
            key, _, val = line.partition("=")
            result.setdefault(key.strip(), val.strip())
    return result


def write_name_map(path: Path, name2code: dict[str, str]) -> Path:
    path.write_text(
        json.dumps({"generated_at": "test", "name2code": name2code}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. 归一化：多板
# ---------------------------------------------------------------------------
def test_normalize_symbol_multi_board():
    assert m.normalize_symbol("600519") == "SH600519"
    assert m.normalize_symbol("688981") == "SH688981"
    assert m.normalize_symbol("900901") == "SH900901"   # 沪 B
    assert m.normalize_symbol("000001") == "SZ000001"
    assert m.normalize_symbol("300750") == "SZ300750"
    assert m.normalize_symbol("830799") == "BJ830799"
    assert m.normalize_symbol("430047") == "BJ430047"
    # 前缀/后缀/大小写形态统一归一
    assert m.normalize_symbol("SH600519") == "SH600519"
    assert m.normalize_symbol("sh600519") == "SH600519"
    assert m.normalize_symbol("600519.SH") == "SH600519"
    assert m.normalize_symbol("600519SH") == "SH600519"
    assert m.normalize_symbol("  sz000001 ") == "SZ000001"
    # 非 A 股 / 无法判定
    assert m.normalize_symbol(None) is None
    assert m.normalize_symbol("") is None
    assert m.normalize_symbol("123456") is None       # 非任何板前缀
    assert m.normalize_symbol("920964") is None       # 92 开头北交所新码不在 canonical 口径内
    assert m.normalize_symbol("abcxyz") is None
    assert m.normalize_symbol("60051") is None


# ---------------------------------------------------------------------------
# 2. 与 intel_collector/core/canonical.py 口径 parity
# ---------------------------------------------------------------------------
def test_normalize_symbol_parity_with_intel_collector_canonical():
    """与生产归一化口径对拍（intel_collector 未入库时跳过，仅本地参考实现生效）。"""
    canonical_norm = None
    for root in m._candidate_roots(ROOT):  # worktree 未跟踪 intel_collector，回落主工作树
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from intel_collector.core.canonical import normalize_symbol as canonical_norm
            break
        except Exception:
            continue
    if canonical_norm is None:  # pragma: no cover - 生产包不可得时
        pytest.skip("intel_collector.core.canonical 不可导入（未入库/未安装）")
    samples = [
        "600519", "SH600519", "sh600519", "600519.SH", "000001", "300750",
        "830799", "430047", "900901", "688981", "123456", "", "abc",
        "920964", "83079", "  002594  ",
    ]
    for raw in samples:
        assert m.normalize_symbol(raw) == canonical_norm(raw), "parity diff: %r" % raw


# ---------------------------------------------------------------------------
# 3. exact 抽取：多形态 + 去重
# ---------------------------------------------------------------------------
def test_extract_entities_code_forms_and_dedupe():
    text = (
        "SH600519、600519.SH、600519SH、[600853 龙建股份]、830799 路演，"
        "贵州茅台600519 再次提及"
    )
    ent = m.extract_entities(text)
    assert ent["exact"] == ["SH600519", "SH600853", "BJ830799"]
    assert ent["rules"]["SH600519"] == "prefix"
    assert ent["rules"]["SH600853"] == "code"
    assert ent["rules"]["BJ830799"] == "code"
    # "贵州茅台600519" 汉字紧贴，按任务书 \b 口径不计 exact（进 weak）
    assert "SH600519" in ent["exact"]
    assert ent["rejected"] == 0


def test_extract_entities_cjk_adjacent_goes_weak():
    ent = m.extract_entities("证券代码002475 与 中文紧贴300750，另有 2026 年数据")
    assert ent["exact"] == []                       # \b 口径下汉字紧贴不匹配
    assert ent["weak"] == ["SZ002475", "SZ300750"]  # 补充层可见，但不升级 exact


# ---------------------------------------------------------------------------
# 4. CLI：多板归一 + 覆盖率口径端到端
# ---------------------------------------------------------------------------
def test_cli_multi_board_end_to_end(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="沪市龙头 600519 涨停", stocks=None),
        row(2, title="创业板 SZ300750 宁德时代", content="正文", stocks='["英伟达"]'),
        row(3, title="北交所 830799 路演"),
        row(4, title="无标的宏观新闻：政策基调延续"),
    ])
    proc = run_cli("--db", str(db), "--dry-run", "--no-name-map")
    assert proc.returncode == 0, proc.stderr
    d = kv(proc.stdout)
    assert d["total_rows"] == "4"
    assert d["scanned_rows"] == "4"
    assert d["before_rows"] == "1"
    assert d["exact_rows"] == "3"
    assert d["after_rows"] == "3"          # before ∪ exact
    assert d["before_pct"] == "25.00%"
    assert d["after_pct"] == "75.00%"
    assert d["delta_pp"] == "50.00"
    assert d["distinct_exact_symbols"] == "3"
    assert d["dry_run"] == "true"
    assert d["staging_out"] == ""


# ---------------------------------------------------------------------------
# 5. 名称候选：只作 candidate，绝不升级 exact
# ---------------------------------------------------------------------------
def test_name_candidate_not_upgraded_to_exact(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="贵州茅台发布三季报"),
        row(2, title="贵州茅台 600519 发布三季报"),
    ])
    nm = write_name_map(tmp_path / "names.json", {"贵州茅台": "600519.SH"})
    proc = run_cli("--db", str(db), "--dry-run", "--name-map", str(nm))
    assert proc.returncode == 0, proc.stderr
    d = kv(proc.stdout)
    assert d["name_map_loaded"] == "true"
    assert d["name_map_entries"] == "1"
    assert d["candidate_rows"] == "1"       # 仅第 1 行是纯候选
    assert d["exact_rows"] == "1"           # 第 2 行 exact
    assert d["after_rows"] == "1"           # 候选不计入 after 覆盖率
    assert d["after_pct"] == "50.00%"
    assert d["before_rows"] == "0"

    out = tmp_path / "staging.jsonl"
    proc2 = run_cli("--db", str(db), "--staging-out", str(out), "--name-map", str(nm))
    assert proc2.returncode == 0, proc2.stderr
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    by_id = {r["id"]: r for r in rows}
    plain = by_id["1"]
    assert plain["exact"] == []
    assert plain["level"] == "candidate"
    assert plain["current_stocks"] == []
    assert plain["candidates"] == [{"symbol": "SH600519", "name": "贵州茅台"}]
    extracted = by_id["2"]
    assert extracted["exact"] == ["SH600519"]
    assert extracted["level"] == "exact"
    assert extracted["candidates"] == []


def test_ambiguous_multiple_name_candidates(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="贵州茅台与五粮液齐涨")])
    nm = write_name_map(tmp_path / "names.json", {
        "贵州茅台": "600519.SH", "五粮液": "000858.SZ",
    })
    proc = run_cli("--db", str(db), "--dry-run", "--name-map", str(nm))
    assert proc.returncode == 0, proc.stderr
    d = kv(proc.stdout)
    assert d["candidate_rows"] == "1"
    assert d["ambiguous_rows"] == "1"
    assert d["distinct_candidate_symbols"] == "2"
    assert d["after_rows"] == "0"


def test_name_candidate_alias_dedup_same_symbol(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="平安银行深发展A公告")])
    nm = write_name_map(tmp_path / "names.json", {
        "平安银行": "000001.SZ", "深发展A": "000001.SZ",
    })
    out = tmp_path / "staging.jsonl"
    proc = run_cli("--db", str(db), "--staging-out", str(out), "--name-map", str(nm))
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["candidates"] == [{"symbol": "SZ000001", "name": "平安银行"}]
    assert lines[0]["level"] == "candidate"


def test_name_map_unsupported_codes_are_reported(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="润农节水发布公告")])
    nm = write_name_map(tmp_path / "names.json", {"润农节水": "920964.BJ"})
    proc = run_cli("--db", str(db), "--dry-run", "--name-map", str(nm))
    assert proc.returncode == 0, proc.stderr
    d = kv(proc.stdout)
    assert d["name_map_entries"] == "0"
    assert d["name_map_skipped_unsupported"] == "1"
    assert d["candidate_rows"] == "0"


# ---------------------------------------------------------------------------
# 6. 坏行 fail-visible（不中断、打印样本）
# ---------------------------------------------------------------------------
def test_bad_stocks_rows_fail_visible(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="坏 JSON", stocks="not-json"),
        row(2, title="非列表", stocks='{"a": 1}'),
        row(3, title="字符串标量", stocks='"英伟达"'),
        row(4, title="空数组", stocks="[]"),
        row(5, title="正常", stocks='["英伟达"]'),
    ])
    proc = run_cli("--db", str(db), "--dry-run", "--no-name-map")
    assert proc.returncode == 0, proc.stderr
    d = kv(proc.stdout)
    assert d["bad_rows"] == "3"
    assert d["before_rows"] == "1"
    bad_ids = [line for line in proc.stdout.splitlines() if line.startswith("BAD_ROW")]
    assert len(bad_ids) >= 3
    assert "invalid_stocks_json" in proc.stdout
    assert "stocks_not_list" in proc.stdout


# ---------------------------------------------------------------------------
# 7. 空文本
# ---------------------------------------------------------------------------
def test_empty_text_rows_counted(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="", content=None),
        row(2, title=None, content=""),
        row(3, title="正常标题", content=None),
    ])
    proc = run_cli("--db", str(db), "--dry-run", "--no-name-map")
    assert proc.returncode == 0, proc.stderr
    d = kv(proc.stdout)
    assert d["empty_text_rows"] == "2"
    assert d["exact_rows"] == "0"
    assert d["after_rows"] == "0"


# ---------------------------------------------------------------------------
# 8. 覆盖率口径定义
# ---------------------------------------------------------------------------
def test_coverage_metric_definition(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="已有标的 A", stocks='["英伟达"]'),
        row(2, title="已有标的 B", stocks='["特斯拉"]'),
        row(3, title="实锤代码 600519 公告"),
        row(4, title="宏观 1"),
        row(5, title="宏观 2"),
    ])
    proc = run_cli("--db", str(db), "--dry-run", "--no-name-map")
    assert proc.returncode == 0, proc.stderr
    d = kv(proc.stdout)
    assert d["total_rows"] == "5"
    assert d["before_rows"] == "2"
    assert d["before_pct"] == "40.00%"
    assert d["exact_rows"] == "1"
    assert d["after_rows"] == "3"
    assert d["after_pct"] == "60.00%"
    assert d["delta_pp"] == "20.00"


# ---------------------------------------------------------------------------
# 9. staging 幂等 + 必需字段
# ---------------------------------------------------------------------------
def test_staging_idempotent_and_required_fields(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="贵州茅台 600519 公告", stocks='["英伟达"]'),
        row(2, title="五粮液发布公告", stocks="[]"),
        row(3, title="宏观新闻", stocks=None),
        row(4, title="中文紧贴证券代码002475"),
    ])
    nm = write_name_map(tmp_path / "names.json", {
        "贵州茅台": "600519.SH", "五粮液": "000858.SZ",
    })
    p1 = tmp_path / "s1.jsonl"
    p2 = tmp_path / "s2.jsonl"
    for path in (p1, p2, p1):  # 同一路径重写 + 不同路径复跑
        proc = run_cli("--db", str(db), "--staging-out", str(path),
                       "--name-map", str(nm), "--no-cjk-adjacent")
        assert proc.returncode == 0, proc.stderr
    assert p1.read_bytes() == p2.read_bytes()           # 幂等：字节一致
    lines = [json.loads(x) for x in p1.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2                              # 第 3/4 行无实体且 before 空 → 不入 staging
    for item in lines:
        for key in ("id", "ts", "source", "current_stocks",
                    "extracted", "exact", "candidates"):
            assert key in item, "staging 缺少字段 %s" % key
    by_id = {x["id"]: x for x in lines}
    assert by_id["1"]["exact"] == ["SH600519"]
    assert by_id["2"]["level"] == "candidate"
    assert by_id["2"]["candidates"] == [{"symbol": "SZ000858", "name": "五粮液"}]
    assert "weak" in by_id["1"]


# ---------------------------------------------------------------------------
# 10. 只读断言：DB 不被写 + 写操作被 sqlite 拒绝
# ---------------------------------------------------------------------------
def test_readonly_no_db_write_and_write_blocked(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="600519 公告", stocks=None)])
    before = sha256(db)
    proc = run_cli("--db", str(db), "--staging-out", str(tmp_path / "s.jsonl"),
                   "--no-name-map")
    assert proc.returncode == 0, proc.stderr
    assert sha256(db) == before, "工具运行后 news.db 字节发生变化（违反只读）"

    con = m.open_readonly(db)
    try:
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO news(id, title) VALUES ('x', 'y')")
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE news SET stocks='[]'")
    finally:
        con.close()
    assert sha256(db) == before


# ---------------------------------------------------------------------------
# 11. --limit 抽样确定性
# ---------------------------------------------------------------------------
def test_limit_sample_is_deterministic(tmp_path):
    rows = [
        row(i, title="新闻 %d 600519" % i, ts="2026-11-0%d 10:00" % (i % 5 + 1))
        for i in range(1, 7)
    ]
    db = make_db(tmp_path / "news.db", rows)
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    for path in (p1, p2):
        proc = run_cli("--db", str(db), "--limit", "2", "--staging-out", str(path),
                       "--no-name-map")
        assert proc.returncode == 0, proc.stderr
    assert p1.read_bytes() == p2.read_bytes()
    d = kv(run_cli("--db", str(db), "--limit", "2", "--dry-run", "--no-name-map").stdout)
    assert d["total_rows"] == "6"
    assert d["scanned_rows"] == "2"
    assert d["limit"] == "2"
    ids = [json.loads(x)["id"] for x in p1.read_text(encoding="utf-8").splitlines()]
    assert len(ids) == 2


# ---------------------------------------------------------------------------
# 12. dry-run 默认不落盘 / 参数冲突 / 缺 DB 的 rc 契约
# ---------------------------------------------------------------------------
def test_dry_run_default_writes_nothing(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="600519 公告")])
    workdir = tmp_path / "wd"
    workdir.mkdir()
    before = sorted(p.name for p in tmp_path.iterdir())
    proc = run_cli("--db", str(db), "--no-name-map", cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    assert kv(proc.stdout)["dry_run"] == "true"
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert sorted(p.name for p in workdir.iterdir()) == []


def test_cli_arg_errors_return_rc2(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="x")])
    p = run_cli("--db", str(db), "--dry-run",
                "--staging-out", str(tmp_path / "s.jsonl"))
    assert p.returncode == 2
    assert "--dry-run 与 --staging-out 不能同时使用" in p.stderr

    p = run_cli("--db", str(tmp_path / "missing.db"), "--dry-run")
    assert p.returncode == 2
    assert "不存在" in p.stderr

    p = run_cli("--db", str(db), "--dry-run", "--name-map", str(tmp_path / "nope.json"))
    assert p.returncode == 2


# ---------------------------------------------------------------------------
# 13. 默认 DB / 名称表定位（worktree 回落）
# ---------------------------------------------------------------------------
def test_default_location_resolution(tmp_path):
    assert m.resolve_default_db(tmp_path) is None
    fake_repo = tmp_path / "repo"
    (fake_repo / "news_collector").mkdir(parents=True)
    db = fake_repo / "news_collector" / "news.db"
    db.write_bytes(b"")
    assert m.resolve_default_db(fake_repo) == db

    nm_dir = fake_repo / "quant_backtest" / "data"
    nm_dir.mkdir(parents=True)
    nm = nm_dir / "stock_names.json"
    write_name_map(nm, {"贵州茅台": "600519.SH"})
    assert m.resolve_default_name_map(fake_repo) == nm


# ---------------------------------------------------------------------------
# 14. Aho-Corasick 名称匹配器（多命中/去重）
# ---------------------------------------------------------------------------
def test_name_matcher_multi_hit_and_dedupe():
    matcher = m.NameMatcher({
        "贵州茅台": "SH600519",
        "茅台": "SH600519",
        "五粮液": "SZ000858",
        "润农节水": "",
    })
    assert bool(matcher) is True
    matched, unmapped = matcher.find_symbols("贵州茅台、五粮液、贵州茅台再提，茅台")
    names = [name for name, _ in matched]
    assert names.count("贵州茅台") == 1
    assert names.count("茅台") == 1
    symbols = {sym for _, sym in matched}
    assert symbols == {"SH600519", "SZ000858"}
    assert m.NameMatcher({}).find_symbols("任意") == ([], [])


# ---------------------------------------------------------------------------
# 15. --json-out 统计落盘（可复跑证据）
# ---------------------------------------------------------------------------
def test_json_out_written(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="600519 公告")])
    jp = tmp_path / "stats.json"
    proc = run_cli("--db", str(db), "--dry-run", "--no-name-map", "--json-out", str(jp))
    assert proc.returncode == 0, proc.stderr
    stats = json.loads(jp.read_text(encoding="utf-8"))
    assert stats["db_mode"] == "ro"
    assert stats["before_rows"] == 0
    assert stats["exact_rows"] == 1
    assert stats["dry_run"] is True
