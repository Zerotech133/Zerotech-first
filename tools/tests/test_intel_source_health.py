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
