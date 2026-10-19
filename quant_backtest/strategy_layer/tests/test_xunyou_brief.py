# -*- coding: utf-8 -*-
"""荀攸参谋简报器单测（读池 + 读证据链 + 读后验 → MD/JSON 留痕）。

全部 fixture 注入 tmp_path：禁读真实 reports/ 生产档、禁碰真实
data/evidence_chain.db、禁连 PG。核心断言：三节齐全 / 逐节降级不中断 /
MD-JSON 双产物一致 / research_only 纪律标注 / 历史日文件名正确。
"""
import json
import os

import pytest

from strategy_layer import xunyou_brief

DAY = "20261016"
SYMBOL = "002633.SZ"
ENTRY = "chan_monitor:%s:%s" % (DAY, SYMBOL)


# ── fixtures（全部 tmp_path 注入，不触生产路径）─────────────────────────
@pytest.fixture()
def pool_file(tmp_path):
    """xunyou.build_pool 同构池档夹具（含 tier 与分歧票）。"""
    doc = {
        "agent": "荀攸（公达）",
        "trade_date": DAY,
        "generated_at": "2026-10-16 17:45:00",
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION",
        "cutover_verdict": "BLOCK_CUTOVER",
        "pool": [
            {"symbol": SYMBOL, "action": "buy", "score": 99.5,
             "confidence": 0.9, "divergence": False,
             "rationale": "缠论1买", "contributions": {}, "tier": "T1"},
            {"symbol": "600519.SH", "action": "buy", "score": 96.0,
             "confidence": 0.8, "divergence": False,
             "rationale": "量价共振", "contributions": {}, "tier": "T2"},
            {"symbol": "300750.SZ", "action": "buy", "score": 90.0,
             "confidence": 0.6, "divergence": True,
             "rationale": "双线分歧", "contributions": {},
             "tier": "", "review_required": True},
        ],
        "stats": {"combined_total": 5, "buy_total": 3,
                  "divergence_total": 1, "superseded_total": 2,
                  "tier1_total": 1, "tier2_total": 1, "review_total": 1},
        "notes": [],
    }
    p = tmp_path / ("xunyou_pool_%s.json" % DAY)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


@pytest.fixture()
def ev_db(tmp_path):
    """tmp 证据链库：一条 pool_in 记账（写完关闭连接，WAL 落盘）。"""
    from evidence_chain import EvidenceChainStore

    db = str(tmp_path / "ev.db")
    store = EvidenceChainStore(db)
    store.transition(ENTRY, "pool_in",
                     {"symbol": SYMBOL, "trade_date": DAY,
                      "strategy": "chan_monitor", "action": "buy",
                      "score": 99.5, "rationale": "缠论1买",
                      "_meta": {"symbol": SYMBOL, "trade_date": DAY,
                                "strategy": "chan_monitor"}},
                     actor="test")
    store.close()
    return db


@pytest.fixture()
def posterior_file(tmp_path):
    """combined_verdicts.py write_out 同构的统计输出夹具。"""
    doc = {"generated_at": "2026-10-16 18:00:00",
           "summary": {"asof": DAY, "events": 2,
                       "windows": {"1": {"n": 2, "hit": 50.0,
                                         "avg": 0.8, "med": 0.8}},
                       "by_strategy": {"chan_monitor":
                                       {"1": {"n": 1, "hit": 100.0,
                                              "avg": 2.0, "med": 2.0}}},
                       "by_weight_group": {"chan_monitor":
                                           {"ge075":
                                            {"1": {"n": 1, "hit": 100.0,
                                                   "avg": 2.0, "med": 2.0}}}},
                       "by_shadow": {}},
           "per_code": {}}
    p = tmp_path / "combined_verdicts.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ── 1. 三节齐全的正常简报 ─────────────────────────────────────────────
def test_brief_full_three_sections(tmp_path, pool_file, ev_db, posterior_file):
    doc = xunyou_brief.build_brief(
        DAY, pool_path=pool_file, evidence_db_path=ev_db,
        posterior_path=posterior_file, out_dir=str(tmp_path / "out"))
    secs = doc["sections"]
    assert secs["pool"]["status"] == "OK"
    assert secs["pool"]["buy_total"] == 3
    assert secs["pool"]["tier_dist"]["T1"] == 1
    assert secs["pool"]["divergence_symbols"] == ["300750.SZ"]
    assert secs["evidence"]["status"] == "OK"
    assert secs["evidence"]["state_counts"] == {"pool_in": 1}
    assert secs["posterior"]["status"] == "OK"
    assert secs["posterior"]["by_strategy"]["chan_monitor"]["n"] == 1
    assert secs["posterior"]["by_weight_group"]["chan_monitor"]["ge075"][
        "hit"] == 100.0


# ── 2. 池档缺失降级 ───────────────────────────────────────────────────
def test_brief_pool_missing_degrades(tmp_path, ev_db, posterior_file):
    doc = xunyou_brief.build_brief(
        DAY, evidence_db_path=ev_db, posterior_path=posterior_file,
        out_dir=str(tmp_path / "out"))
    sec = doc["sections"]["pool"]
    assert sec["status"] == "DEGRADED"
    assert xunyou_brief.NO_POOL_TEXT in sec["reason"]
    md = open(doc["brief_md_path"], encoding="utf-8").read()
    assert "池档未产出" in md


# ── 3. 证据链库不存在 → 优雅降级文案（非崩溃）─────────────────────────
def test_brief_evidence_db_missing_degrades_gracefully(
        tmp_path, pool_file, posterior_file):
    doc = xunyou_brief.build_brief(
        DAY, pool_path=pool_file,
        evidence_db_path=str(tmp_path / "no_such.db"),
        posterior_path=posterior_file, out_dir=str(tmp_path / "out"))
    sec = doc["sections"]["evidence"]
    assert sec["status"] == "DEGRADED"
    assert "证据链未启用" in sec["reason"]
    md = open(doc["brief_md_path"], encoding="utf-8").read()
    assert "证据链未启用" in md
    assert "Traceback" not in md  # 优雅呈现，非堆栈裸奔


# ── 4. 后验档缺失降级 ─────────────────────────────────────────────────
def test_brief_posterior_missing_degrades(tmp_path, pool_file, ev_db):
    doc = xunyou_brief.build_brief(
        DAY, pool_path=pool_file, evidence_db_path=ev_db,
        posterior_path=str(tmp_path / "nope.json"),
        out_dir=str(tmp_path / "out"))
    sec = doc["sections"]["posterior"]
    assert sec["status"] == "DEGRADED"
    assert xunyou_brief.NO_POSTERIOR_TEXT in sec["reason"]
    md = open(doc["brief_md_path"], encoding="utf-8").read()
    assert "后验档未产出" in md


# ── 5. 单节源抛异常 → 整份简报继续（逐节隔离）─────────────────────────
def test_brief_section_exception_continues(tmp_path, pool_file, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom: 证据链读取炸了")

    monkeypatch.setattr(xunyou_brief, "read_evidence_state", broken)
    doc = xunyou_brief.build_brief(DAY, pool_path=pool_file,
                                   out_dir=str(tmp_path / "out"))
    sec = doc["sections"]["evidence"]
    assert sec["status"] == "DEGRADED"
    assert "RuntimeError" in sec.get("detail", "")
    md = open(doc["brief_md_path"], encoding="utf-8").read()
    assert "已降级占位" in md
    # 其余节不受牵连：池摘要仍 OK
    assert doc["sections"]["pool"]["status"] == "OK"


# ── 6. MD 与 JSON 双产物一致（JSON 可解析且字段齐）────────────────────
def test_brief_md_json_consistency(tmp_path, pool_file, ev_db,
                                   posterior_file):
    doc = xunyou_brief.build_brief(
        DAY, pool_path=pool_file, evidence_db_path=ev_db,
        posterior_path=posterior_file, out_dir=str(tmp_path / "out"))
    json_path = doc["brief_json_path"]
    md_path = doc["brief_md_path"]
    assert os.path.isfile(json_path) and os.path.isfile(md_path)
    loaded = json.loads(open(json_path, encoding="utf-8").read())
    for key in ("agent", "trade_date", "scope", "cutover_verdict",
                "sections"):
        assert key in loaded
    assert loaded["trade_date"] == DAY
    assert loaded["scope"] == "RESEARCH_ONLY_NOT_PRODUCTION"
    for name in ("pool", "evidence", "posterior"):
        assert name in loaded["sections"]
        assert loaded["sections"][name]["status"] == "OK"
    # JSON 数字与 MD 文本一致：3 只 buy / pool_in=1
    md = open(md_path, encoding="utf-8").read()
    assert "**3**" in md
    assert "pool_in：1" in md


# ── 7. 历史日回看：文件名正确 ─────────────────────────────────────────
def test_brief_history_date_filename(tmp_path, pool_file):
    old = "20260101"
    pool_old = str(tmp_path / ("xunyou_pool_%s.json" % old))
    os.replace(pool_file, pool_old)
    doc = xunyou_brief.build_brief(old, pool_path=pool_old,
                                   out_dir=str(tmp_path / "out"))
    assert doc["trade_date"] == old
    assert doc["brief_md_path"].endswith("xunyou_brief_%s.md" % old)
    assert doc["brief_json_path"].endswith("xunyou_brief_%s.json" % old)
    assert os.path.isfile(doc["brief_md_path"])
    assert "荀攸参谋简报（%s）" % old in open(doc["brief_md_path"],
                                             encoding="utf-8").read()


# ── 8. research_only 纪律标注存在（MD 与 JSON 双端）───────────────────
def test_brief_research_only_annotation(tmp_path):
    doc = xunyou_brief.build_brief(DAY, out_dir=str(tmp_path / "out"))
    md = open(doc["brief_md_path"], encoding="utf-8").read()
    assert "RESEARCH_ONLY_NOT_PRODUCTION" in md
    assert "BLOCK_CUTOVER" in md
    loaded = json.loads(open(doc["brief_json_path"],
                             encoding="utf-8").read())
    assert loaded["scope"] == "RESEARCH_ONLY_NOT_PRODUCTION"
    assert loaded["cutover_verdict"] == "BLOCK_CUTOVER"


# ── 9. CLI main：--date/--out-dir/--posterior 显式覆盖可用 ────────────
def test_cli_main_writes_to_out_dir(tmp_path, posterior_file):
    rc = xunyou_brief.main([
        "--date", DAY,
        "--out-dir", str(tmp_path / "cli_out"),
        "--posterior", posterior_file,
    ])
    assert rc == 0
    out = tmp_path / "cli_out"
    assert (out / ("xunyou_brief_%s.md" % DAY)).is_file()
    assert (out / ("xunyou_brief_%s.json" % DAY)).is_file()
    loaded = json.loads((out / ("xunyou_brief_%s.json" % DAY))
                        .read_text(encoding="utf-8"))
    assert loaded["trade_date"] == DAY
    # CLI 未给池/证据链 → 对应节降级但不崩
    assert loaded["sections"]["pool"]["status"] == "DEGRADED"
    assert loaded["sections"]["evidence"]["status"] == "DEGRADED"
    assert loaded["sections"]["posterior"]["status"] == "OK"


# ── 10. 只读纪律：调用后源文件字节不变（池/后验档）────────────────────
def test_brief_leaves_source_files_unchanged(tmp_path, pool_file, ev_db,
                                             posterior_file):
    def snapshot(root):
        # 只读 ro 连接可能产生 -shm/-wal 瞬态文件（既有判例），排除后比对；
        # wal 瞬态必须为空（无真实写入）
        files = {}
        for n in sorted(os.listdir(root)):
            p = os.path.join(root, n)
            if not os.path.isfile(p) or n.endswith(("-shm", "-wal")):
                continue
            files[n] = open(p, "rb").read()
        return files

    roots = [os.path.dirname(pool_file), os.path.dirname(posterior_file),
             os.path.dirname(ev_db)]
    before = {r: snapshot(r) for r in roots}
    xunyou_brief.build_brief(DAY, pool_path=pool_file,
                             evidence_db_path=ev_db,
                             posterior_path=posterior_file,
                             out_dir=str(tmp_path / "out"))
    after = {r: snapshot(r) for r in roots}
    for r in roots:
        assert after[r] == before[r]
    wal = os.path.join(os.path.dirname(ev_db), "ev.db-wal")
    assert not os.path.exists(wal) or open(wal, "rb").read() == b""
