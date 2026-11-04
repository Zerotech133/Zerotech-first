# -*- coding: utf-8 -*-
"""tools/intel_evidence_targets.py 定向测试（IL-13 工具层 v1）。

覆盖：exact/candidate 严格分离、candidate 不升级、stocks/JEV 图层、去重、
since/until 过滤、坏行 fail-visible、空结果、部分失败、只读断言（URI mode=ro /
文件字节不变）、JSON schema 稳定、limit 分页稳定、回源抽样可定位、staging 对拍。
全部用 temp sqlite fixture，不碰仓库真实 news.db / 生产库。
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

import intel_evidence_targets as m  # noqa: E402

TOOL = ROOT / "tools" / "intel_evidence_targets.py"
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

EXPECTED_JSON_KEYS = {
    "tool", "version", "generated_at", "readonly", "db", "sources", "mode",
    "query", "stats", "exact", "candidate", "sample", "failures", "warnings",
    "partial_failure",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_db(path: Path, rows: list[tuple]) -> Path:
    """rows: (id, source, ts, title, content, stocks, jev)"""
    con = sqlite3.connect(str(path))
    try:
        con.execute(SCHEMA)
        con.executemany(
            "INSERT INTO news(id, source, ts, title, content, created_at, stocks, jev) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(r[0], r[1], r[2], r[3], r[4], "2026-11-01 10:00", r[5], r[6]) for r in rows],
        )
        con.commit()
    finally:
        con.close()
    return path


def row(i, *, title="", content="", stocks=None, jev=None, source="sina",
        ts="2026-11-01 10:00"):
    return (str(i), source, ts, title, content, stocks, jev)


def write_name_map(path: Path, name2code: dict[str, str]) -> Path:
    path.write_text(
        json.dumps({"generated_at": "test", "name2code": name2code}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def dummy_map(tmp_path: Path) -> Path:
    """加载成功但不命中 fixture 文本的名称表（让 stats 维持 partial_failure=false）。"""
    return write_name_map(tmp_path / "dummy_names.json", {"不存在的股票名": "600001.SH"})


def write_staging(path: Path, items: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n", encoding="utf-8"
    )
    return path


def staging_item(nid, *, exact=None, candidates=None, level="none", source="sina",
                 ts="2026-11-01 10:00"):
    return {
        "id": nid, "ts": ts, "source": source, "current_stocks": [],
        "extracted": [], "exact": exact or [], "candidates": candidates or [],
        "weak": [], "level": level,
    }


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [PY, "-X", "utf8", str(TOOL), *args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, env=env,
    )


def cli(db: Path, *args: str, map_path: Path | None = None,
        staging_path: Path | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    base = [PY, "-X", "utf8", str(TOOL), "--db", str(db)]
    base += ["--staging", str(staging_path)] if staging_path else ["--no-staging"]
    base += ["--name-map", str(map_path)] if map_path else ["--no-name-map"]
    base += list(args)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        base, cwd=str(cwd or ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, env=env,
    )


def jout(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode in (0, 3), proc.stderr
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise AssertionError("stdout 不是合法 JSON:\n%s\n--- stderr ---\n%s" % (
            proc.stdout[:2000], proc.stderr[:2000]))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. exact / candidate 严格分离
# ---------------------------------------------------------------------------
def test_exact_and_candidate_strict_separation(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="贵州茅台 600519 发布三季报"),
        row(2, title="五粮液发布公告"),
    ])
    nm = write_name_map(tmp_path / "names.json", {
        "贵州茅台": "600519.SH", "五粮液": "000858.SZ",
    })
    proc = cli(db, "--code", "600519", "--json", map_path=nm)
    d = jout(proc)
    assert proc.returncode == 0
    exact = d["exact"]
    candidate = d["candidate"]
    assert exact["total"] == 1, exact
    assert candidate["total"] == 0, candidate
    assert exact["items"][0]["target"] == "SH600519"
    assert exact["items"][0]["target_class"] == "exact"
    assert exact["items"][0]["origins"][0]["layer"] == "text_extract"
    # 500ms: 候选与精确从不相混
    assert set(d["exact"]["items"][0]) >= {"target", "target_class", "evidence", "origins"}

    proc2 = cli(db, "--code", "000858", "--json", map_path=nm)
    d2 = jout(proc2)
    assert d2["exact"]["total"] == 0
    assert d2["candidate"]["total"] == 1
    cand = d2["candidate"]["items"][0]
    assert cand["target"] == "SZ000858"
    assert cand["target_class"] == "candidate"
    assert cand["origins"][0]["layer"] == "text_name_map"
    assert cand["origins"][0]["derived"] == "name_map"
    assert cand["evidence"]["news_id"] == "2"


# ---------------------------------------------------------------------------
# 2. candidate 不得升级为 exact
# ---------------------------------------------------------------------------
def test_candidate_never_upgraded_to_exact(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="贵州茅台发布三季报")])
    nm = write_name_map(tmp_path / "names.json", {"贵州茅台": "600519.SH"})
    d = jout(cli(db, "--code", "600519", "--json", map_path=nm))
    assert d["exact"]["total"] == 0
    assert d["candidate"]["total"] == 1
    # 覆盖口径：candidate 不计入 exact_rows
    st = jout(cli(db, "--stats", "--json", map_path=nm))["stats"]
    assert st["exact_rows"] == 0
    assert st["candidate_rows"] == 1
    assert st["after_rows"] == 0


# ---------------------------------------------------------------------------
# 3. stocks 字段：代码字面 → exact；名称 → candidate
# ---------------------------------------------------------------------------
def test_stocks_layer_code_exact_and_name_candidate(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="公告", stocks='["600519.SH"]'),
        row(2, title="公告", stocks='["贵州茅台"]'),
    ])
    nm = write_name_map(tmp_path / "names.json", {"贵州茅台": "600519.SH"})
    d = jout(cli(db, "--code", "600519", "--json", map_path=nm))
    assert d["exact"]["total"] == 1
    assert d["exact"]["items"][0]["evidence"]["news_id"] == "1"
    assert d["exact"]["items"][0]["origins"][0]["layer"] == "stocks_code"
    assert d["candidate"]["total"] == 1
    assert d["candidate"]["items"][0]["evidence"]["news_id"] == "2"
    assert d["candidate"]["items"][0]["origins"][0]["layer"] == "stocks_name_map"


# ---------------------------------------------------------------------------
# 4. JEV（模型层）即使给代码也只作 candidate
# ---------------------------------------------------------------------------
def test_jev_model_code_is_candidate_not_exact(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="AI 芯片新闻", jev=json.dumps({"companies": ["688256"]}, ensure_ascii=False)),
        row(2, title="688256 公司公告",
            jev=json.dumps({"companies": ["688256"]}, ensure_ascii=False)),
    ])
    nm = dummy_map(tmp_path)
    d = jout(cli(db, "--code", "688256", "--json", map_path=nm))
    assert d["exact"]["total"] == 1           # 仅 text_extract 行
    assert d["exact"]["items"][0]["evidence"]["news_id"] == "2"
    assert d["candidate"]["total"] == 1       # 模型行是候选
    c = d["candidate"]["items"][0]
    assert c["evidence"]["news_id"] == "1"
    assert c["origins"][0]["layer"] == "jev_model_code"
    assert c["origins"][0]["derived"] == "model_code"
    assert c["origins"][0]["model"] is True


# ---------------------------------------------------------------------------
# 5. 去重：同一证据同一标的只留一条 exact（多来源合并 origins）
# ---------------------------------------------------------------------------
def test_dedupe_same_target_merges_origins(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="600519 600519.SH SH600519 三连",
            stocks='["600519.SH"]',
            jev=json.dumps({"companies": ["600519"]})),
    ])
    nm = dummy_map(tmp_path)
    d = jout(cli(db, "--code", "600519", "--json", map_path=nm))
    assert d["exact"]["total"] == 1
    rel = d["exact"]["items"][0]
    layers = {o["layer"] for o in rel["origins"]}
    assert layers == {"text_extract", "stocks_code"}
    assert d["candidate"]["total"] == 0       # 模型同标的被 exact 吸收，不重复不升级


# ---------------------------------------------------------------------------
# 6. since / until 过滤
# ---------------------------------------------------------------------------
def test_since_until_filter(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="600519 九月", ts="2026-09-15 10:00"),
        row(2, title="600519 十月", ts="2026-10-15 10:00"),
        row(3, title="600519 十一月", ts="2026-11-01 10:00"),
    ])
    nm = dummy_map(tmp_path)
    d = jout(cli(db, "--code", "600519", "--since", "2026-10-01",
                 "--until", "2026-10-31", "--json", map_path=nm))
    assert d["exact"]["total"] == 1
    assert d["exact"]["items"][0]["evidence"]["ts"] == "2026-10-15 10:00"
    assert d["query"]["since"] == "2026-10-01 00:00:00"
    assert d["query"]["until"] == "2026-10-31 23:59:59"


# ---------------------------------------------------------------------------
# 7. 代码归一（多种写法同结果）与非法代码 rc=2
# ---------------------------------------------------------------------------
def test_code_normalization_equivalence_and_invalid(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="600519 公告")])
    nm = dummy_map(tmp_path)
    outs = []
    for code in ("600519", "SH600519", "600519.SH", "sh600519"):
        d = jout(cli(db, "--code", code, "--json", map_path=nm))
        outs.append(d["exact"]["total"])
    assert outs == [1, 1, 1, 1]
    for bad in ("123456", "920964", "not-a-code"):
        proc = cli(db, "--code", bad, "--json", map_path=nm)
        assert proc.returncode == 2, proc.stdout
        assert "非法" in proc.stderr or "不支持" in proc.stderr


# ---------------------------------------------------------------------------
# 8. 坏行 fail-visible（不中断）+ --strict rc=3
# ---------------------------------------------------------------------------
def test_bad_rows_fail_visible(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="正常 600519", stocks='["英伟达"]'),
        row(2, title="坏 stocks", stocks="not-json"),
        row(3, title="坏 jev", jev="not-json"),
        row(4, title="jev 非 dict", jev="[1,2,3]"),
    ])
    nm = dummy_map(tmp_path)
    proc = cli(db, "--stats", "--json", map_path=nm)
    assert proc.returncode == 0
    d = jout(proc)
    fc = d["stats"]["failure_counts"]
    assert fc.get("bad_stocks_json") == 1
    assert fc.get("bad_jev_json") == 1
    assert fc.get("bad_jev_type") == 1
    assert d["partial_failure"] is True
    assert any(f["kind"] == "bad_stocks_json" for f in d["failures"])

    proc_strict = cli(db, "--stats", "--json", "--strict", map_path=nm)
    assert proc_strict.returncode == 3

    # 人类可读输出同样 fail-visible
    human = cli(db, "--stats", map_path=nm)
    assert human.returncode == 0
    assert "partial_failure=true" in human.stdout
    assert "failure kind=bad_stocks_json" in human.stdout


# ---------------------------------------------------------------------------
# 9. 空结果（合法代码无数据）正常返回空列表
# ---------------------------------------------------------------------------
def test_empty_result_ok(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="宏观新闻")])
    nm = dummy_map(tmp_path)
    proc = cli(db, "--code", "600000", "--json", map_path=nm)
    assert proc.returncode == 0
    d = jout(proc)
    assert d["exact"]["total"] == 0
    assert d["candidate"]["total"] == 0
    assert d["exact"]["has_more"] is False
    assert d["partial_failure"] is False


# ---------------------------------------------------------------------------
# 10. 部分失败 fail-visible：名称表缺失 → candidate 层不可用
# ---------------------------------------------------------------------------
def test_partial_failure_missing_name_map(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="贵州茅台 600519")])
    proc = cli(db, "--stats", "--json")  # 默认 --no-name-map
    assert proc.returncode == 0
    d = jout(proc)
    assert d["partial_failure"] is True
    assert any("candidate" in w for w in d["warnings"])
    assert cli(db, "--stats", "--json", "--strict").returncode == 3


# ---------------------------------------------------------------------------
# 11. staging 一致性对拍：一致 → 不 partial；不一致/坏行 → fail-visible
# ---------------------------------------------------------------------------
def test_staging_consistency_and_mismatch(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, source="sina", ts="2026-11-01 10:00", title="贵州茅台 600519 公告"),
        row(2, source="sina", ts="2026-11-01 10:05", title="五粮液发布公告"),
    ])
    nm = write_name_map(tmp_path / "names.json", {
        "贵州茅台": "600519.SH", "五粮液": "000858.SZ",
    })
    good = write_staging(tmp_path / "good.jsonl", [
        staging_item("1", exact=["SH600519"], level="exact"),
        staging_item("2", candidates=[{"symbol": "SZ000858", "name": "五粮液"}], level="candidate"),
    ])
    d = jout(cli(db, "--stats", "--json", map_path=nm, staging_path=good))
    sc = d["stats"]["staging_check"]
    assert sc["staging_rows"] == 2
    assert sc["exact_match"] == 2
    assert sc["exact_mismatch"] == 0
    assert sc["candidate_mismatch"] == 0
    assert sc["missing_rows"] == 0
    assert d["partial_failure"] is False

    bad = write_staging(tmp_path / "bad.jsonl", [
        staging_item("1", exact=[], level="none"),      # 与 live 不符
        {"broken": True},                               # 缺字段坏行
    ])
    dirty = jout(cli(db, "--stats", "--json", map_path=nm, staging_path=bad))
    sc2 = dirty["stats"]["staging_check"]
    assert sc2["exact_mismatch"] == 1
    assert sc2["staging_bad_lines"] == 1
    assert dirty["partial_failure"] is True
    kinds = {f["kind"] for f in dirty["failures"]}
    assert "staging_mismatch" in kinds
    assert "staging_bad_line" in kinds


# ---------------------------------------------------------------------------
# 12. staging_confirmed 标注（回源确认）
# ---------------------------------------------------------------------------
def test_staging_confirmed_annotation(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="贵州茅台 600519 公告"),
        row(2, title="600519 另一条"),
    ])
    nm = write_name_map(tmp_path / "names.json", {"贵州茅台": "600519.SH"})
    st = write_staging(tmp_path / "s.jsonl", [
        staging_item("1", exact=["SH600519"], level="exact"),
    ])
    d = jout(cli(db, "--code", "600519", "--json", map_path=nm, staging_path=st))
    by_id = {it["evidence"]["news_id"]: it for it in d["exact"]["items"]}
    assert by_id["1"]["staging_confirmed"] is True
    assert by_id["2"]["staging_confirmed"] is False


# ---------------------------------------------------------------------------
# 12b. staging 对拍口径：stocks_code 层压制候选不算 mismatch（回归）
# ---------------------------------------------------------------------------
def test_staging_candidate_compare_ignores_stocks_code_suppression(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="贵州茅台发布公告", stocks='["600519.SH"]'),
    ])
    nm = write_name_map(tmp_path / "names.json", {"贵州茅台": "600519.SH"})
    # 前序 staging v1 不感知 stocks_code 层：exact=[] 且候选含 SH600519
    st = write_staging(tmp_path / "s.jsonl", [
        staging_item("1", exact=[], candidates=[{"symbol": "SH600519", "name": "贵州茅台"}],
                     level="candidate"),
    ])
    d = jout(cli(db, "--stats", "--json", map_path=nm, staging_path=st))
    sc = d["stats"]["staging_check"]
    assert sc["exact_mismatch"] == 0
    assert sc["candidate_mismatch"] == 0, sc["mismatch_samples"]
    assert d["partial_failure"] is False
    # 契约侧：stocks 代码是 exact，text 名称候选被压制（不重复、不升级）
    q = jout(cli(db, "--code", "600519", "--json", map_path=nm, staging_path=st))
    assert q["exact"]["total"] == 1
    assert q["candidate"]["total"] == 0
    assert q["exact"]["items"][0]["origins"][0]["layer"] == "stocks_code"


# ---------------------------------------------------------------------------
# 13. 只读断言：mode=ro URI / DB 字节不变 / 写被拒绝
# ---------------------------------------------------------------------------
def test_readonly_uri_and_no_db_write(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="600519 公告")])
    nm = dummy_map(tmp_path)
    before = sha256(db)
    proc = cli(db, "--stats", "--json", map_path=nm)
    assert proc.returncode == 0
    d = jout(proc)
    assert d["readonly"] is True
    assert "mode=ro" in d["db"]["uri"]
    assert sha256(db) == before, "工具运行后 news.db 字节发生变化（违反只读）"

    con = m.open_readonly(db)
    try:
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO news(id, title) VALUES ('x','y')")
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE news SET stocks='[]'")
    finally:
        con.close()
    assert sha256(db) == before
    assert "mode=ro" in m.readonly_uri(db)


# ---------------------------------------------------------------------------
# 14. JSON schema 稳定（stats / code / sample 三种模式键集合一致）
# ---------------------------------------------------------------------------
def test_json_schema_stable(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="贵州茅台 600519 公告"),
        row(2, title="五粮液公告"),
    ])
    nm = write_name_map(tmp_path / "names.json", {
        "贵州茅台": "600519.SH", "五粮液": "000858.SZ",
    })
    modes = [
        ["--stats"],
        ["--code", "600519"],
        ["--sample", "2"],
    ]
    for extra in modes:
        d = jout(cli(db, *extra, "--json", map_path=nm))
        assert set(d) == EXPECTED_JSON_KEYS, (extra, set(d) ^ EXPECTED_JSON_KEYS)
        assert d["tool"] == "intel_evidence_targets"
        assert d["version"] == "v1"
        assert isinstance(d["readonly"], bool)
        assert isinstance(d["failures"], list)
        assert isinstance(d["warnings"], list)
        assert isinstance(d["partial_failure"], bool)


# ---------------------------------------------------------------------------
# 15. limit/offset 分页稳定、无重叠、可复跑
# ---------------------------------------------------------------------------
def test_limit_pagination_stable(tmp_path):
    rows = [
        row(i, title="600519 新闻 %d" % i, ts="2026-11-0%d 10:00" % i)
        for i in range(1, 6)
    ]
    db = make_db(tmp_path / "news.db", rows)
    nm = dummy_map(tmp_path)
    seen: list[str] = []
    for offset in (0, 2, 4):
        d = jout(cli(db, "--code", "600519", "--limit", "2", "--offset", str(offset),
                     "--json", map_path=nm))
        page = d["exact"]
        assert page["total"] == 5
        assert page["offset"] == offset
        seen.extend(it["evidence"]["news_id"] for it in page["items"])
        if offset == 0:
            assert page["has_more"] is True
        if offset == 4:
            assert page["has_more"] is False
            assert len(page["items"]) == 1
    assert len(seen) == 5 and len(set(seen)) == 5, seen
    # 可复跑：同一页两次结果一致（忽略 generated_at）
    a = jout(cli(db, "--code", "600519", "--limit", "2", "--offset", "2", "--json",
                 map_path=nm))
    b = jout(cli(db, "--code", "600519", "--limit", "2", "--offset", "2", "--json",
                 map_path=nm))
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


# ---------------------------------------------------------------------------
# 16. 回源抽样：可定位率 100%，锚点可解释；不足 95% 时 fail-visible
# ---------------------------------------------------------------------------
def test_sample_locatable_and_contract(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="600519 公告甲"),
        row(2, title="600519 公告乙", stocks='["600519.SH"]'),
        row(3, title="贵州茅台发布公告"),
        row(4, title="五粮液发布公告"),
    ])
    nm = write_name_map(tmp_path / "names.json", {
        "贵州茅台": "600519.SH", "五粮液": "000858.SZ",
    })
    proc = cli(db, "--sample", "4", "--json", map_path=nm)
    assert proc.returncode == 0
    d = jout(proc)
    smp = d["sample"]
    assert smp["requested"] == 4
    assert smp["returned"] >= 2
    assert smp["locatable"] == smp["returned"]
    assert smp["locatable_pct"] == 100.0
    assert smp["threshold_pct"] == 95.0
    classes = {it["target_class"] for it in smp["items"]}
    assert classes == {"exact", "candidate"}
    for it in smp["items"]:
        assert it["verify"]["locatable"] is True
        assert it["verify"]["anchor"]
        assert it["evidence"]["news_id"]


# ---------------------------------------------------------------------------
# 17. 参数/日期校验 rc=2；--stats 与 --code 互斥
# ---------------------------------------------------------------------------
def test_arg_validation_rc2(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="600519 公告")])
    nm = dummy_map(tmp_path)
    cases = [
        (["--since", "2026-11-02", "--until", "2026-11-01"], "since 晚于"),
        (["--since", "not-a-date"], "日期格式非法"),
        (["--stats", "--code", "600519"], "互斥"),
        (["--code", "600519", "--limit", "-1"], "不能为负"),
        (["--sample", "0"], "必须为正整数"),
    ]
    for extra, expect in cases:
        proc = cli(db, *extra, "--json", map_path=nm)
        assert proc.returncode == 2, (extra, proc.stdout, proc.stderr)
        assert expect in proc.stderr, (extra, proc.stderr)

    missing = run_cli("--db", str(tmp_path / "nope.db"), "--stats", "--no-staging",
                      "--no-name-map")
    assert missing.returncode == 2

    empty_sqlite = tmp_path / "empty.db"
    sqlite3.connect(str(empty_sqlite)).close()
    no_table = cli(empty_sqlite, "--stats", "--json", map_path=nm)
    assert no_table.returncode == 2
    assert "news 表不存在" in no_table.stderr


# ---------------------------------------------------------------------------
# 18. 统计口径：before / exact / after / candidate 分离
# ---------------------------------------------------------------------------
def test_stats_coverage_contract(tmp_path):
    db = make_db(tmp_path / "news.db", [
        row(1, title="已有标的", stocks='["英伟达"]'),        # before
        row(2, title="空数组", stocks="[]"),                  # 非 before
        row(3, title="600519 显式代码"),                      # exact
        row(4, title="宏观 1"),
        row(5, title="宏观 2"),
    ])
    nm = dummy_map(tmp_path)
    d = jout(cli(db, "--stats", "--json", map_path=nm))
    st = d["stats"]
    assert st["news_total"] == 5
    assert st["news_scanned"] == 5
    assert st["before_rows"] == 1
    assert st["before_pct"] == 20.0
    assert st["exact_rows"] == 1
    assert st["after_rows"] == 2
    assert st["after_pct"] == 40.0
    assert st["delta_pp"] == 20.0
    assert st["relation_total"] == 1
    assert st["exact_relations"] == 1
    assert st["candidate_relations"] == 0
    assert st["exact_relation_layers"] == {"text_extract": 1}
    assert d["readonly"] is True


# ---------------------------------------------------------------------------
# 19. 默认模式 = stats；人类可读输出含关键段
# ---------------------------------------------------------------------------
def test_default_mode_stats_human_output(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="600519 公告")])
    nm = dummy_map(tmp_path)
    proc = cli(db, map_path=nm)
    assert proc.returncode == 0
    assert "[metadata]" in proc.stdout
    assert "[stats]" in proc.stdout
    assert "mode=stats" in proc.stdout
    assert "[done]" in proc.stdout
    assert "rc=0" in proc.stdout


# ---------------------------------------------------------------------------
# 20. 多候选 ambiguous 标注
# ---------------------------------------------------------------------------
def test_ambiguous_candidate_marking(tmp_path):
    db = make_db(tmp_path / "news.db", [row(1, title="贵州茅台与五粮液齐涨")])
    nm = write_name_map(tmp_path / "names.json", {
        "贵州茅台": "600519.SH", "五粮液": "000858.SZ",
    })
    d = jout(cli(db, "--code", "600519", "--json", map_path=nm))
    rel = d["candidate"]["items"][0]
    assert rel["ambiguous"] is True
    st = jout(cli(db, "--stats", "--json", map_path=nm))["stats"]
    assert st["ambiguous_candidate_rows"] == 1


# ---------------------------------------------------------------------------
# 21. 参数/日期归一工具函数
# ---------------------------------------------------------------------------
def test_parse_date_bound_unit():
    assert m.parse_date_bound("2026-11-04", upper=False) == "2026-11-04 00:00:00"
    assert m.parse_date_bound("2026-11-04", upper=True) == "2026-11-04 23:59:59"
    assert m.parse_date_bound("2026-11-04 12:30", upper=True) == "2026-11-04 12:30:59"
    assert m.parse_date_bound(None, upper=False) is None
    with pytest.raises(ValueError):
        m.parse_date_bound("2026/11/04", upper=False)
