# -*- coding: utf-8 -*-
"""tools/intel_source_health.py + ops/run_intel_pre_review.ps1 测试
（WO-INTEL-HEALTH-20261031 / I1 来源健康 + 机器预审调度壳）。

覆盖（不依赖生产服务；全部 tmp 夹具）：
- JSON 契约 `intel_source_health/v1`：3 登记源 + 9 news 源 = 12 源，字段齐全、summary 计数；
- 0 字节计数 / sample_ok 判定 / 目录与单文件（tdx sqlite）口径；
- stale（采样 mtime 超阈值、status 整体采集超阈值）与 --strict 拦截；
- 缺样本路径（启用源）非 strict 也 fail-visible（exit 1）；registry/status 缺文件/坏 JSON 显式失败；
- 只读边界：模块不 import 网络/写盘/sqlite 依赖，运行前后 tmp 文件树零变化（不写文件）；
- 调度壳：默认 DryRun、显式 -Write、日志落盘、旧产物归档（不删除）、无计划任务/系统配置注册；
  纯 ASCII（PS 5.1 无 BOM 安全）；可用 PowerShell 解析 + `-Limit 0` 参数错误路径实跑。

真实登记表/status 只做只读契约抽样，不写真实数据根。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import intel_source_health as h  # noqa: E402

TOOL = ROOT / "tools" / "intel_source_health.py"
WRAPPER = ROOT / "ops" / "run_intel_pre_review.ps1"
REAL_REGISTRY = ROOT / "agent_platform" / "data" / "intel" / "sources.json"
PYTHON = sys.executable
NOW = "2026-10-31T13:00:00"

NEWS_IDS = ("sina", "eastmoney", "wallstreetcn", "announcement", "survey",
            "report", "world", "govpolicy", "chinanews")


# ── 夹具 ──────────────────────────────────────────────────────────────
def _src(source_id, sample_path, enabled=True, mode="sampling", kind="k",
         fmt="jsonl"):
    return {"source_id": source_id, "name": source_id, "kind": kind,
            "mode": mode, "enabled": enabled, "trust": "medium",
            "authorization": "test", "sample_path": str(sample_path),
            "sample_format": fmt, "last_sample": None, "owner": "test"}


def _write_registry(tmp_path, sources, name="sources.json"):
    data = {
        "schema_version": "1.0", "registry_id": "intel-sources-test",
        "phase": "Phase 2 test", "updated_at": "2026-10-31", "owner": "test",
        "policy": {
            "registration_is_not_ingestion": "注册≠接入（测试口径）",
            "sampling_is_not_publication": "采样≠发布（测试口径）",
        },
        "sources": sources,
    }
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _write_status(tmp_path, sources, updated="2026-10-31 12:55",
                  last_ok=True, name="status.json"):
    data = {"last_collect": updated, "last_ok": last_ok,
            "new_last_run": 12, "push_last_run": 1, "news_24h": 100,
            "news_total": 1000, "sources": sources, "updated": updated}
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _news_sources(ok=True):
    return {sid: {"ok": ok, "got": 30, "new": 1} for sid in NEWS_IDS}


def _set_mtime(path: Path, epoch: float):
    os.utime(path, (epoch, epoch))


def _fixture(tmp_path, *, guba_files=2, guba_zero=0, guba_age_h=1,
             osint_enabled=False, tdx_missing=False, tdx_enabled=False,
             status_updated="2026-10-31 12:55", news_ok=True,
             status_last_ok=True):
    """构造 3 登记源 + 9 news 源的 tmp 夹具，mtime 相对注入时钟。"""
    epoch = datetime.fromisoformat(NOW).timestamp()
    guba = tmp_path / "guba"
    guba.mkdir()
    for i in range(guba_files):
        f = guba / ("f%d.jsonl" % i)
        f.write_text('{"id": %d}' % i, encoding="utf-8")
        _set_mtime(f, epoch - guba_age_h * 3600 - i)
    for i in range(guba_zero):
        f = guba / ("zero%d.jsonl" % i)
        f.write_text("", encoding="utf-8")
        _set_mtime(f, epoch - guba_age_h * 3600 - guba_files - i)
    osint = tmp_path / "osint"
    osint.mkdir()
    empty = osint / "empty.json"
    empty.write_text("", encoding="utf-8")
    _set_mtime(empty, epoch - 2 * 3600)
    tdx = tmp_path / "tdx.db"
    if not tdx_missing:
        tdx.write_bytes(b"SQLite format 3\x00" + b"x" * 128)
        _set_mtime(tdx, epoch - 3 * 3600)
    registry = _write_registry(tmp_path, [
        _src("guba", guba, enabled=True),
        _src("osint_video", osint, enabled=osint_enabled),
        _src("tdx_market", tdx, enabled=tdx_enabled, mode="registered",
             kind="market_data", fmt="sqlite"),
    ])
    status = _write_status(tmp_path, _news_sources(ok=news_ok),
                           updated=status_updated, last_ok=status_last_ok)
    return registry, status, {"guba": guba, "osint": osint, "tdx": tdx}


def _run_json(capsys, *extra):
    args = list(extra) + ["--json"]
    rc = h.main(args)
    out = capsys.readouterr().out
    return rc, json.loads(out)


def _report(tmp_path, capsys, **fixture_kw):
    reg, st, paths = _fixture(tmp_path, **fixture_kw)
    rc, data = _run_json(capsys, "--registry", str(reg), "--status", str(st),
                         "--now", NOW)
    return rc, data, paths


# ── JSON 契约 / 覆盖 / 计数 ──────────────────────────────────────────
def test_json_contract_and_source_coverage(tmp_path, capsys):
    rc, data, _ = _report(tmp_path, capsys)
    assert rc == 0
    assert data["schema"] == "intel_source_health/v1"
    assert data["readonly"] is True and data["network"] is False
    assert data["registry"]["ok"] is True and data["status"]["ok"] is True
    assert data["registry"]["source_count"] == 3
    assert data["status"]["source_count"] == 9
    assert len(data["sources"]) == 12
    ids = {s["source_id"] for s in data["sources"]}
    assert {"guba", "osint_video", "tdx_market"} <= ids
    assert set(NEWS_IDS) <= ids
    required = {"source_id", "name", "category", "kind", "mode", "enabled",
                "status", "last_ok", "age_s", "file_count", "0byte_count",
                "sample_ok", "sample_path", "stale_after_s", "error"}
    for src in data["sources"]:
        assert required <= set(src), src["source_id"]
    sm = data["summary"]
    assert sm["total"] == 12 and sm["news_total"] == 9 and sm["news_ok"] == 9
    assert sm["disabled"] == 2 and sm["enabled_total"] == 10
    assert data["ok"] is True
    # 目录源口径：2 个非空文件、最新 mtime ≈ 1 小时前
    guba = next(s for s in data["sources"] if s["source_id"] == "guba")
    assert guba["status"] == "ok" and guba["category"] == "registry"
    assert guba["file_count"] == 2 and guba["0byte_count"] == 0
    assert guba["sample_ok"] is True and guba["last_ok"] is True
    assert isinstance(guba["age_s"], int) and 3500 <= guba["age_s"] <= 3700
    # 单文件源（tdx sqlite，仅 stat 不打开库）disabled 但样本状态可见
    tdx = next(s for s in data["sources"] if s["source_id"] == "tdx_market")
    assert tdx["file_count"] == 1 and tdx["0byte_count"] == 0
    assert tdx["sample_ok"] is True and tdx["status"] == "disabled"
    # news 源不适用字段显式 null（不伪造）
    for src in data["sources"]:
        if src["category"] == "news":
            assert src["file_count"] is None
            assert src["0byte_count"] is None
            assert src["sample_ok"] is None
            assert src["status"] == "ok" and src["last_ok"] is True


def test_zero_byte_counting_and_empty_sample(tmp_path, capsys):
    rc, data, _ = _report(tmp_path, capsys, guba_files=2, guba_zero=1)
    assert rc == 0
    guba = next(s for s in data["sources"] if s["source_id"] == "guba")
    assert guba["file_count"] == 3 and guba["0byte_count"] == 1
    assert guba["sample_ok"] is True  # 仍有非空样本 → 可用
    osint = next(s for s in data["sources"] if s["source_id"] == "osint_video")
    assert osint["file_count"] == 1 and osint["0byte_count"] == 1
    assert osint["sample_ok"] is False
    assert osint["status"] == "disabled"       # 未启用
    assert osint["error"] == "sample_all_zero_bytes"  # fail-visible 不静默


def test_text_mode_contract(tmp_path, capsys):
    reg, st, _ = _fixture(tmp_path)
    rc = h.main(["--registry", str(reg), "--status", str(st), "--now", NOW])
    out = capsys.readouterr().out
    assert rc == 0
    assert "intel_source_health/v1" in out
    assert "汇总：" in out and "整体：OK" in out


# ── stale / 缺文件 fail-visible ──────────────────────────────────────
def test_stale_sample_strict_only(tmp_path, capsys):
    reg, st, _ = _fixture(tmp_path, guba_age_h=40)  # > 30h 采样阈值
    rc, data = _run_json(capsys, "--registry", str(reg), "--status", str(st),
                         "--now", NOW)
    assert rc == 0, "非 strict 只报不拦"
    guba = next(s for s in data["sources"] if s["source_id"] == "guba")
    assert guba["status"] == "stale" and guba["error"] == "sample_stale"
    assert guba["sample_ok"] is True and guba["last_ok"] is True
    assert data["ok"] is False and data["summary"]["stale"] >= 1
    rc2, data2 = _run_json(capsys, "--registry", str(reg), "--status", str(st),
                           "--now", NOW, "--strict")
    assert rc2 == 1 and data2["ok"] is False


def test_stale_status_marks_news_sources(tmp_path, capsys):
    reg, st, _ = _fixture(tmp_path, status_updated="2026-10-31 06:00")  # 7h 前
    rc, data = _run_json(capsys, "--registry", str(reg), "--status", str(st),
                         "--now", NOW)
    assert rc == 0
    assert data["status"]["stale"] is True
    news = [s for s in data["sources"] if s["category"] == "news"]
    assert len(news) == 9 and all(s["status"] == "stale" for s in news)
    assert data["summary"]["news_ok"] == 0
    rc2, _ = _run_json(capsys, "--registry", str(reg), "--status", str(st),
                       "--now", NOW, "--strict")
    assert rc2 == 1


def test_missing_sample_path_fail_visible_even_non_strict(tmp_path, capsys):
    reg, st, _ = _fixture(tmp_path, tdx_missing=True, tdx_enabled=True)
    rc, data = _run_json(capsys, "--registry", str(reg), "--status", str(st),
                         "--now", NOW)
    assert rc == 1, "启用源缺样本属硬故障：非 strict 也 fail-visible"
    tdx = next(s for s in data["sources"] if s["source_id"] == "tdx_market")
    assert tdx["status"] == "missing" and tdx["error"] == "sample_path_missing"
    assert tdx["sample_ok"] is False and tdx["file_count"] == 0
    assert data["ok"] is False
    assert any("tdx_market" in e for e in data["errors"])


def test_missing_registry_file_fail_visible(tmp_path, capsys):
    st = _write_status(tmp_path, _news_sources())
    rc, data = _run_json(capsys, "--registry", str(tmp_path / "nope.json"),
                         "--status", str(st), "--now", NOW)
    assert rc == 1
    assert data["registry"]["ok"] is False
    assert data["registry"]["error"], "缺登记表须显式 error（不静默空）"
    assert data["ok"] is False and data["errors"]


def test_missing_status_file_fail_visible(tmp_path, capsys):
    reg = _write_registry(tmp_path, [_src("guba", tmp_path, enabled=True)])
    rc, data = _run_json(capsys, "--registry", str(reg),
                         "--status", str(tmp_path / "nope.json"), "--now", NOW)
    assert rc == 1
    assert data["status"]["ok"] is False and data["status"]["error"]
    assert data["ok"] is False and any("status" in e for e in data["errors"])


def test_bad_json_registry_fail_visible(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    st = _write_status(tmp_path, _news_sources())
    rc, data = _run_json(capsys, "--registry", str(bad), "--status", str(st),
                         "--now", NOW)
    assert rc == 1 and data["registry"]["ok"] is False
    assert "JSON" in data["registry"]["error"] or "json" in data["registry"]["error"]


# ── 只读边界 ─────────────────────────────────────────────────────────
def test_module_static_readonly_boundary():
    src = TOOL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"urllib", "requests", "http", "socket", "sqlite3", "shutil"}
    assert not (imported & banned), "只读工具不得 import 网络/DB/写盘依赖: %s" % (imported & banned)
    for token in ("write_text", "write_bytes", "unlink", "rmtree", "mkdir(",
                  "os.remove", "open("):
        assert token not in src, "只读工具不得含写盘调用: %s" % token


def test_run_does_not_touch_fixture_files(tmp_path, capsys):
    reg, st, _ = _fixture(tmp_path)
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns)
              for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    rc = h.main(["--registry", str(reg), "--status", str(st), "--now", NOW])
    assert rc == 0
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns)
             for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    assert before == after, "健康检查不得写/改任何样本或输入文件"


def test_real_registry_readonly_contract(tmp_path, capsys):
    """真实登记表只读抽样：3 源必在、契约字段可解析（status 用 tmp 夹具，不碰真实数据根）。"""
    assert REAL_REGISTRY.is_file()
    st = _write_status(tmp_path, _news_sources())
    rc, data = _run_json(capsys, "--registry", str(REAL_REGISTRY),
                         "--status", str(st), "--now", NOW)
    assert rc in (0, 1)
    ids = {s["source_id"] for s in data["sources"]}
    assert {"guba", "osint_video", "tdx_market"} <= ids
    assert data["registry"]["ok"] is True


# ── 调度壳 ops/run_intel_pre_review.ps1 ──────────────────────────────
def _wrapper_text() -> str:
    return WRAPPER.read_text(encoding="utf-8-sig")


def _ps():
    return shutil.which("powershell") or shutil.which("pwsh")


def test_wrapper_exists_and_ascii_safe():
    assert WRAPPER.is_file()
    raw = WRAPPER.read_bytes()
    assert all(b < 128 for b in raw), "PS 5.1 无 BOM 场景：脚本须纯 ASCII"


def test_wrapper_default_dry_run_and_write_switch():
    text = _wrapper_text()
    assert "[switch]$Write" in text and "[switch]$DryRun" in text
    assert "intel_pre_review.py" in text
    assert "'--write'" in text and "'--out'" in text
    assert "if ($Write)" in text, "仅 -Write 才调用写模式"
    assert "dry-run" in text.lower()


def test_wrapper_no_task_registration_or_system_config():
    text = _wrapper_text()
    banned = ("Register-ScheduledTask", "New-ScheduledTask", "Set-ScheduledTask",
              "schtasks", "New-Service", "Set-Service", "Set-ItemProperty",
              "New-ItemProperty", "reg add", "HKCU:", "HKLM:", "Start-Service")
    for token in banned:
        assert token not in text, "调度壳不得注册任务/写系统配置: %s" % token
    assert "L2" in text, "文档/脚本须注明调度注册需老田批 L2"


def test_wrapper_logs_and_archives_not_deletes():
    text = _wrapper_text()
    assert "Add-Content" in text and "LogDir" in text, "长跑输出须落日志文件"
    assert "KeepDays" in text and "Move-Item" in text and "archive" in text
    assert "Remove-Item" not in text, "红线：不得删除旧产物（归档/回收站人工处理）"
    assert "truncated" in text, "大 JSON 输出须截断落日志（防日志膨胀）"


def test_wrapper_uses_staging_then_final():
    text = _wrapper_text()
    assert ".tmp" in text and "Move-Item" in text
    assert "intel_pre_review_" in text
    assert "Mutex" in text, "幂等：并发保护"


@pytest.mark.skipif(not _ps(), reason="PowerShell not available")
def test_wrapper_powershell_parses():
    env = dict(os.environ, PS_SCRIPT_PATH=str(WRAPPER))
    script = ("[void][scriptblock]::Create((Get-Content -Raw -LiteralPath "
              "$env:PS_SCRIPT_PATH)); 'PS_PARSE_OK'")
    r = subprocess.run([_ps(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, env=env)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "PS_PARSE_OK" in (r.stdout or "")


@pytest.mark.skipif(not _ps(), reason="PowerShell not available")
def test_wrapper_config_error_exit2_without_calling_tool(tmp_path):
    out = tmp_path / "out"
    logs = tmp_path / "logs"
    r = subprocess.run([_ps(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(WRAPPER), "-Limit", "0",
                        "-OutDir", str(out), "-LogDir", str(logs)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 2, (r.stdout or "") + (r.stderr or "")
    assert logs.is_dir(), "参数错误前应已建目录并落日志（长跑输出落盘）"
    assert any(logs.iterdir()), "应有 run_<date>.log"
    # 参数错误不应产生任何预审产物
    assert not list(out.glob("intel_pre_review_*.jsonl"))


# ══════════════════════════════════════════════════════════════════════
# IL-08 三源健康盘点（增量能力；不改动以上 I1 用例与 legacy 契约）
# schema: intel_source_health_il08/v1；--md 输出到达 stdout，不落盘
# ══════════════════════════════════════════════════════════════════════
IL08_SCHEMA = "intel_source_health_il08/v1"
SOURCE_KEYS = {"source_id", "name", "in_registry", "owner", "license",
                    "watermark", "watermark_at", "age_days", "stale_days", "stale",
                    "probe_available", "notes", "gaps", "mode", "enabled",
                    "registry_source_id", "registry_note"}
IL08_TOP_KEYS = {"schema", "kind", "readonly", "network", "online_health_claimed",
                 "generated_at", "now", "no_authoritative_registry", "registry",
                 "thresholds", "sources", "summary", "gaps", "errors", "policy", "ok"}


def _mk_sqlite_db(path, specs):
    """specs: [(ddl, insert_sql, rows)]；创建只读探针用的临时 sqlite。"""
    import sqlite3
    con = sqlite3.connect(str(path))
    try:
        for ddl, insert_sql, rows in specs:
            con.execute(ddl)
            if rows:
                con.executemany(insert_sql, rows)
        con.commit()
    finally:
        con.close()


def _il08_fixture(tmp_path, *, xq_created_h=1.0, xq_labeled_h=1.5,
                  guba_ts_h=2.0, tdx_days=3, guba_nonempty=2, guba_zero=1,
                  xq_empty=False, tdx_corrupt=False):
    """构造 IL-08 夹具：三源本地仓 + registry(guba/tdx, 无 xueqiu) + status。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    clk = datetime.fromisoformat(NOW)
    epoch = clk.timestamp()

    # 雪球 sqlite
    xq = tmp_path / "xueqiu_info.db"
    created = (clk.timestamp() - xq_created_h * 3600)
    labeled = (clk.timestamp() - xq_labeled_h * 3600)
    _mk_sqlite_db(xq, [
        ("CREATE TABLE xq_status (status_id TEXT PRIMARY KEY, created_at TEXT)",
         "INSERT INTO xq_status VALUES (?,?)",
         [] if xq_empty else [("s1", datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S")),
                              ("s2", datetime.fromtimestamp(created - 3600).strftime("%Y-%m-%d %H:%M:%S"))]),
        ("CREATE TABLE xq_status_classified (status_id TEXT PRIMARY KEY, labeled_at TEXT)",
         "INSERT INTO xq_status_classified VALUES (?,?)",
         [] if xq_empty else [("s1", datetime.fromtimestamp(labeled).strftime("%Y-%m-%d %H:%M:%S"))]),
    ])

    # 股吧 目录
    gdir = tmp_path / "guba"
    gdir.mkdir()
    ts = datetime.fromtimestamp(clk.timestamp() - guba_ts_h * 3600)
    for i in range(guba_nonempty):
        f = gdir / ("f%d.jsonl" % i)
        f.write_text(json.dumps({"post_id": i, "title": "t", "ts": ts.strftime("%Y-%m-%d %H:%M:%S")},
                                ensure_ascii=False), encoding="utf-8")
        _set_mtime(f, epoch - guba_ts_h * 3600 - i)
    for i in range(guba_zero):
        f = gdir / ("zero%d.jsonl" % i)
        f.write_text("", encoding="utf-8")
        _set_mtime(f, epoch - guba_ts_h * 3600 - guba_nonempty - i)

    # TDX sqlite
    tdx = tmp_path / "tdx_info.db"
    if tdx_corrupt:
        tdx.write_bytes(b"not-a-sqlite-file")
    else:
        d = datetime.fromtimestamp(clk.timestamp() - tdx_days * 86400).strftime("%Y-%m-%d")
        _mk_sqlite_db(tdx, [
            ("CREATE TABLE stock_notice (ts_code TEXT, notice_date TEXT)",
             "INSERT INTO stock_notice VALUES (?,?)", [("600519.SH", d)]),
            ("CREATE TABLE stock_news (ts_code TEXT, pub_date TEXT)",
             "INSERT INTO stock_news VALUES (?,?)", [("600519.SH", d)]),
            ("CREATE TABLE stock_report (ts_code TEXT, pub_date TEXT)",
             "INSERT INTO stock_report VALUES (?,?)", [("600519.SH", d)]),
        ])

    reg = _write_registry(tmp_path, [
        _src("guba", gdir, enabled=True),
        _src("osint_video", tmp_path / "osint", enabled=False),
        _src("tdx_market", tdx, enabled=False, mode="registered",
             kind="market_data", fmt="sqlite"),
    ])
    st = _write_status(tmp_path, _news_sources())
    args = ["--registry", str(reg), "--status", str(st), "--now", NOW,
            "--xueqiu-db", str(xq), "--guba-dir", str(gdir), "--tdx-db", str(tdx)]
    return {"registry": reg, "status": st, "xueqiu": xq, "guba": gdir,
            "tdx": tdx, "args": args, "clk": clk}


def _il08_json(tmp_path, capsys, **fixture_kw):
    fx = _il08_fixture(tmp_path, **fixture_kw)
    rc, data = _run_json(capsys, *fx["args"])
    return rc, data, fx


def test_il08_json_additive_block_and_three_sources(tmp_path, capsys):
    rc, data, fx = _il08_json(tmp_path, capsys)
    assert rc == 0, "legacy 健康输入下 rc 应保持 0"
    # legacy 契约零破坏
    assert data["schema"] == "intel_source_health/v1"
    assert len(data["sources"]) == 12 and data["summary"]["total"] == 12
    # 新增 il08 块
    inv = data["il08"]
    assert inv["schema"] == IL08_SCHEMA and inv["kind"] == "intel_source_health_il08"
    assert inv["readonly"] is True and inv["network"] is False
    assert inv["online_health_claimed"] is False
    assert inv["no_authoritative_registry"] is True
    assert inv["registry"]["authoritative"] is False
    assert inv["registry"]["covers_three_sources"] is False
    assert inv["registry"]["source_count"] == 3
    ids = [s["source_id"] for s in inv["sources"]]
    assert ids == ["xueqiu", "guba", "tdx_market"]
    by = {s["source_id"]: s for s in inv["sources"]}
    assert by["xueqiu"]["in_registry"] is False
    assert by["guba"]["in_registry"] is True
    assert by["tdx_market"]["in_registry"] is True
    # 每源一行必需字段
    for src in inv["sources"]:
        assert SOURCE_KEYS <= set(src), src["source_id"]
        assert src["stale_days"] == 2.0
    # 水位事实（注入时钟 NOW 附近）
    assert by["xueqiu"]["watermark"]["kind"] == "sqlite"
    assert by["xueqiu"]["watermark"]["rows_total"] == 3
    assert by["xueqiu"]["watermark_at"].startswith("2026-10-31 1")
    assert by["xueqiu"]["probe_available"] is True
    assert by["guba"]["watermark"]["kind"] == "directory"
    assert by["guba"]["watermark"]["files"] == 3
    assert by["guba"]["watermark"]["files_nonempty"] == 2
    assert by["guba"]["watermark"]["files_zero_bytes"] == 1
    assert by["guba"]["watermark_at"] == "2026-10-31 11:00:00"
    assert by["tdx_market"]["watermark"]["kind"] == "sqlite"
    assert by["tdx_market"]["watermark"]["rows_total"] == 3
    # owner / 许可：在册源有，未在册源 null
    assert by["guba"]["owner"] == "test"
    assert by["guba"]["license"] == "test"
    assert by["xueqiu"]["owner"] is None and by["xueqiu"]["license"] is None
    assert "not_in_registry" in by["xueqiu"]["gaps"]
    assert "owner_unknown" in by["xueqiu"]["gaps"]
    assert "license_unknown" in by["xueqiu"]["gaps"]
    sm = inv["summary"]
    assert sm["total"] == 3 and sm["in_registry"] == 2
    assert sm["not_in_registry"] == ["xueqiu"]
    assert sm["probe_available"] == 3 and sm["errors"] == 0
    assert inv["policy"]["writes_db"] is False
    assert inv["policy"]["starts_stops_services"] is False


def test_il08_md_output_only(tmp_path, capsys):
    fx = _il08_fixture(tmp_path)
    rc = h.main(fx["args"] + ["--md"])
    out = capsys.readouterr().out
    assert rc == 0
    assert IL08_SCHEMA in out
    assert "no_authoritative_registry=true" in out
    assert "| 源 | in_registry | 水位 |" in out
    for token in ("雪球", "股吧", "TDX"):
        assert token in out
    assert "## 缺口（fail-visible）" in out
    assert "## 未做 / 边界" in out
    # --md 只输出 IL-08，不混 legacy 文本
    assert "情报来源健康（intel_source_health/v1）" not in out


def test_il08_stale_days_and_threshold(tmp_path, capsys):
    rc, data, _ = _il08_json(tmp_path, capsys, xq_created_h=1.0, tdx_days=5)
    by = {s["source_id"]: s for s in data["il08"]["sources"]}
    assert by["xueqiu"]["stale"] is False
    assert by["tdx_market"]["stale"] is True
    assert by["tdx_market"]["age_days"] >= 5
    # 提高阈值后 TDX 不再 stale
    fx = _il08_fixture(tmp_path / "t2", xq_created_h=1.0, tdx_days=5)
    (tmp_path / "t2").mkdir(exist_ok=True)
    rc2, data2 = _run_json(capsys, *fx["args"], "--stale-days", "10")
    by2 = {s["source_id"]: s for s in data2["il08"]["sources"]}
    assert rc2 == 0
    assert by2["tdx_market"]["stale"] is False
    assert by2["tdx_market"]["stale_days"] == 10.0


def test_il08_future_watermark_conservative(tmp_path, capsys):
    fx = _il08_fixture(tmp_path)
    # 把雪球水位改成未来（+2 天）
    import sqlite3
    con = sqlite3.connect(str(fx["xueqiu"]))
    try:
        future = (fx["clk"].timestamp() + 2 * 86400)
        con.execute("UPDATE xq_status SET created_at = ?",
                    (datetime.fromtimestamp(future).strftime("%Y-%m-%d %H:%M:%S"),))
        con.commit()
    finally:
        con.close()
    rc, data = _run_json(capsys, *fx["args"])
    xq = {s["source_id"]: s for s in data["il08"]["sources"]}["xueqiu"]
    assert rc == 0
    assert xq["age_days"] == 0.0 and xq["stale"] is False
    assert "watermark_in_future" in xq["notes"]


def test_il08_empty_and_bad_sqlite_fail_visible(tmp_path, capsys):
    rc, data, _ = _il08_json(tmp_path, capsys, xq_empty=True, tdx_corrupt=True)
    inv = data["il08"]
    by = {s["source_id"]: s for s in inv["sources"]}
    assert rc == 0, "IL-08 失败不改 legacy rc（fail-visible 在 il08.errors）"
    assert by["xueqiu"]["watermark_at"] is None
    assert "空水位" in by["xueqiu"]["notes"]
    assert by["tdx_market"]["probe_available"] is False
    assert by["tdx_market"]["watermark"]["error"].startswith("sqlite_open_failed")
    assert inv["ok"] is False
    assert inv["summary"]["errors"] == 1
    assert any(e["source_id"] == "tdx_market" for e in inv["errors"])
    assert any(g["source_id"] == "tdx_market" and g["gap"] == "probe_error"
               for g in inv["gaps"])
    # --md 也 fail-visible 且 rc=0（事实盘点模式）
    rc_md = h.main(_il08_fixture(tmp_path / "t2", xq_empty=True, tdx_corrupt=True)["args"] + ["--md"])
    md = capsys.readouterr().out
    assert rc_md == 0
    assert "sqlite_open_failed" in md


def test_il08_bad_registry_degrade_and_gaps(tmp_path, capsys):
    fx = _il08_fixture(tmp_path)
    # 缺注册表 → legacy rc=1（旧语义），IL-08 仍给事实 + no_authoritative_registry
    rc, data = _run_json(capsys, "--registry", str(tmp_path / "nope.json"),
                         "--status", str(fx["status"]), "--now", NOW,
                         "--xueqiu-db", str(fx["xueqiu"]),
                         "--guba-dir", str(fx["guba"]), "--tdx-db", str(fx["tdx"]))
    inv = data["il08"]
    assert rc == 1, "旧契约：缺注册表 fail-visible rc=1（不得被 IL-08 改写）"
    assert inv["no_authoritative_registry"] is True
    assert inv["registry"]["ok"] is False
    assert inv["summary"]["in_registry"] == 0
    assert all(not s["in_registry"] for s in inv["sources"])
    assert {g["gap"] for g in inv["gaps"]} >= {"not_in_registry", "owner_unknown",
                                               "license_unknown"}
    assert inv["summary"]["probe_available"] == 3  # 事实仍可查


def test_il08_guba_zero_byte_only_fail_visible(tmp_path, capsys):
    rc, data, _ = _il08_json(tmp_path, capsys, guba_nonempty=0, guba_zero=2)
    guba = {s["source_id"]: s for s in data["il08"]["sources"]}["guba"]
    assert guba["probe_available"] is False
    assert guba["watermark"]["error"] == "all_files_zero_bytes"
    assert "all_files_zero_bytes" in guba["notes"]
    assert data["il08"]["ok"] is False


def test_il08_readonly_no_write_and_uri(tmp_path, capsys):
    import hashlib
    fx = _il08_fixture(tmp_path)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (fx["xueqiu"], fx["tdx"])}
    before_tree = {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                   for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    rc = h.main(fx["args"] + ["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mode=ro" in out  # il08.watermark.path 所在只读 URI 由 probe 记录
    rc_md = h.main(fx["args"] + ["--md"])
    assert rc_md == 0
    capsys.readouterr()
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (fx["xueqiu"], fx["tdx"])}
    assert before == after, "IL-08 探针不得写/改 source 数据仓"
    after_tree = {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                  for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    assert before_tree == after_tree, "IL-08 盘点不得新增/改任何文件"


def test_il08_json_schema_stable_and_legacy_equal(tmp_path, capsys):
    fx = _il08_fixture(tmp_path)
    rc, data = _run_json(capsys, *fx["args"])
    assert rc == 0
    inv = data["il08"]
    assert IL08_TOP_KEYS <= set(inv), set(inv) ^ IL08_TOP_KEYS
    assert {"total", "in_registry", "not_in_registry", "probe_available",
            "probe_unavailable", "stale", "errors", "gaps"} <= set(inv["summary"])
    assert {"path", "ok", "error", "registry_id", "schema_version", "updated_at",
            "phase", "source_count", "authoritative", "covers_three_sources",
            "reason"} <= set(inv["registry"])
    # legacy 无 il08 版本与带 il08 版本的 legacy 键完全一致（零破坏）
    legacy_only = h.build_report(str(fx["registry"]), str(fx["status"]), NOW,
                                 include_il08=False)
    got = {k: v for k, v in data.items() if k != "il08"}
    assert got == legacy_only, "legacy JSON 语义被改动"


def test_il08_no_credentials_in_output(tmp_path, capsys):
    fx = _il08_fixture(tmp_path)
    h.main(fx["args"] + ["--json"])
    js = capsys.readouterr().out
    h.main(fx["args"] + ["--md"])
    md = capsys.readouterr().out
    for text in (js, md):
        low = text.lower()
        for bad in ("token=", "cookie", "bearer ", "password", "passwd", "api_key"):
            assert bad not in low, "IL-08 输出不得含凭据线索: %s" % bad


def test_il08_helper_static_readonly_boundary():
    helper = ROOT / "tools" / "intel_source_probe.py"
    src = helper.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"urllib", "requests", "http", "socket", "shutil"}  # sqlite3 仅只读必需
    assert not (imported & banned), imported & banned
    assert "mode=ro" in src and "query_only" in src, "helper 必须以只读 URI + query_only 打开"
    for token in ("write_text", "write_bytes", "unlink", "rmtree", "mkdir(",
                  "os.remove", ".execute(\"INSERT", ".execute(\"UPDATE"):
        assert token not in src, "只读 helper 不得含写盘/写库调用: %s" % token
