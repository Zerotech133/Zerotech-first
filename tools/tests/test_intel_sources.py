# -*- coding: utf-8 -*-
"""tools/intel_sources.py 测试（Phase 2，WO-INTEL-SOURCES-20261031）。

覆盖（对应任务要求）：
- 登记表 JSON 契约合法：真实 sources.json 的必填字段/模式/口径短语/三来源；
- 采样不越界：--limit 边界（1..100）、返回条数恒 ≤ limit、按落盘新旧取文件；
- 缺文件 fail-visible：路径缺失/空目录/来源未启用/仅登记 → 显式非零退出，不静默空样本；
- 不写生产：采样与展示前后，样本目录与登记表字节/mtime/文件集合零变化；
- 只读工具边界：模块不 import 网络/写盘依赖、无写文件调用（AST 静态断言）。

全部用例使用 tmp_path 假数据；真实登记表只做契约断言，不写真实数据根。
"""
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import intel_sources as m  # noqa: E402

REAL_REGISTRY = ROOT / "agent_platform" / "data" / "intel" / "sources.json"
PYTHON = sys.executable


# ── 测试数据构造 ─────────────────────────────────────────
def _write_registry(tmp_path, sources, policy=None, filename="sources.json"):
    registry = {
        "schema_version": "1.0",
        "registry_id": "intel-sources-test",
        "phase": "Phase 2 · 测试",
        "updated_at": "2026-10-31",
        "owner": "测试",
        "policy": policy or {
            "registration_is_not_ingestion": "登记≠接入：测试口径",
            "sampling_is_not_publication": "采样≠发布：测试口径",
        },
        "sources": sources,
    }
    path = tmp_path / filename
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _source(**overrides):
    base = {
        "source_id": "demo",
        "name": "演示来源",
        "kind": "forum_posts",
        "mode": "sampling",
        "enabled": True,
        "full_ingest": "deferred",
        "trust": "medium",
        "authorization": "仅内部只读采样，不联网、不再分发",
        "sample_path": "unused-sample-path-for-registry-tests",
        "sample_format": "jsonl",
        "last_sample": None,
        "owner": "测试",
    }
    base.update(overrides)
    return base


def _jsonl(path, records, bad_line=False):
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    if bad_line:
        lines.append("{not-json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _snapshot(paths):
    """→ {相对路径: (size, mtime_ns, sha256)}，供"零写入"断言。"""
    snap = {}
    files = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(saved for saved in p.rglob("*") if saved.is_file())
    for f in files:
        stat = f.stat()
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        snap[str(f)] = (stat.st_size, stat.st_mtime_ns, digest)
    return snap


def _run_cli(args, registry=None):
    cmd = [PYTHON, str(ROOT / "tools" / "intel_sources.py")] + list(args)
    if registry is not None:
        cmd += ["--registry", str(registry)]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=120)


# ── 1) 真实登记表 JSON 合法 ──────────────────────────────
def test_real_registry_exists_and_is_valid_json():
    assert REAL_REGISTRY.is_file(), "缺 agent_platform/data/intel/sources.json"
    data = json.loads(REAL_REGISTRY.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0"
    assert data["registry_id"] == "intel-sources"


def test_real_registry_contract_three_sources():
    registry, path = m.load_registry()
    assert path == REAL_REGISTRY
    index = m.source_index(registry)
    assert {"guba", "osint_video", "tdx_market"} <= set(index)

    guba = index["guba"]
    assert guba["mode"] == "sampling" and guba["enabled"] is True
    assert guba["full_ingest"] == "deferred"
    assert "仅" in guba["authorization"], "授权边界必须写清仅内部使用"
    assert guba["sample_path"].replace("\\", "/").endswith(
        "/quant_backtest/data/guba"), "股吧采样落点必须是既有落盘数据根"

    osint = index["osint_video"]
    assert osint["mode"] == "sampling" and osint["enabled"] is False
    assert osint["full_ingest"] == "deferred", "视频后置：完整接入须 deferred"

    tdx = index["tdx_market"]
    assert tdx["mode"] == "registered"
    assert tdx["sample_format"] == "sqlite", "TDX 仅登记 sqlite 真源，不走进位采样"


def test_real_registry_policy_states_registration_not_ingestion():
    registry, _ = m.load_registry()
    policy = registry["policy"]
    assert "登记≠接入" in policy["registration_is_not_ingestion"]
    assert "采样≠发布" in policy["sampling_is_not_publication"]
    assert "不联网" in policy["sampling_is_not_publication"] or \
        "不写" in policy["sampling_is_not_publication"]


# ── 2) 登记表校验（缺字段/重复/非法模式 → 显式拒绝）────────
def test_registry_missing_required_field_rejected(tmp_path):
    bad = _source()
    bad.pop("owner")
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(m.IntelSourcesError, match="缺字段"):
        m.load_registry(path)


def test_registry_duplicate_source_id_rejected(tmp_path):
    path = _write_registry(tmp_path, [_source(), _source(name="重复")])
    with pytest.raises(m.IntelSourcesError, match="重复"):
        m.load_registry(path)


def test_registry_invalid_mode_rejected(tmp_path):
    path = _write_registry(tmp_path, [_source(mode="auto-ingest")])
    with pytest.raises(m.IntelSourcesError, match="mode 非法"):
        m.load_registry(path)


def test_registry_missing_policy_phrase_rejected(tmp_path):
    path = _write_registry(tmp_path, [_source()], policy={"sampling_is_not_publication": "采样≠发布"})
    with pytest.raises(m.IntelSourcesError, match="policy"):
        m.load_registry(path)


def test_registry_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(m.IntelSourcesError, match="不可读"):
        m.load_registry(tmp_path / "nope.json")


def test_unknown_source_rejected(tmp_path):
    path = _write_registry(tmp_path, [_source()])
    registry, _ = m.load_registry(path)
    with pytest.raises(m.IntelSourcesError, match="未知来源"):
        m.get_source(registry, "no_such_source")


# ── 3) 采样：limit 边界与不越界 ──────────────────────────
def test_sample_returns_at_most_limit_and_newest_files_first(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    old = _jsonl(data / "old.jsonl", [{"id": i} for i in range(3)])
    new = _jsonl(data / "new.jsonl", [{"id": 100 + i} for i in range(5)])
    import os
    import time
    now = time.time()
    os.utime(old, (now - 300, now - 300))
    os.utime(new, (now, now))
    src = _source(sample_path=str(data))
    registry, _ = m.load_registry(_write_registry(tmp_path, [src]))

    result = m.sample_source(registry, "demo", limit=2)
    assert result["returned"] == 2
    assert result["returned"] <= result["limit"] == 2
    assert [r["record"]["id"] for r in result["records"]] == [100, 101], "应先采最新落盘文件"
    assert result["records"][0]["file"] == "new.jsonl"


def test_sample_skips_empty_files_and_invalid_lines(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "empty.jsonl").write_text("", encoding="utf-8")
    _jsonl(data / "good.jsonl", [{"id": 1}, {"id": 2}, {"id": 3}], bad_line=True)
    src = _source(sample_path=str(data))
    registry, _ = m.load_registry(_write_registry(tmp_path, [src]))

    result = m.sample_source(registry, "demo", limit=10)
    assert result["returned"] == 3
    assert result["files_skipped_empty"] >= 1
    assert result["lines_skipped_invalid"] == 1


@pytest.mark.parametrize("bad_limit", [0, -1, 101, True, "3"])
def test_sample_limit_out_of_range_rejected(tmp_path, bad_limit):
    src = _source(sample_path=str(tmp_path))
    registry, _ = m.load_registry(_write_registry(tmp_path, [src]))
    with pytest.raises(m.IntelSourcesError, match="limit"):
        m.sample_source(registry, "demo", limit=bad_limit)


def test_cli_limit_over_max_exit_2(tmp_path):
    path = _write_registry(tmp_path, [_source(sample_path=str(tmp_path))])
    proc = _run_cli(["--sample", "demo", "--limit", str(m.MAX_LIMIT + 1)], registry=path)
    assert proc.returncode == 2
    assert "limit" in (proc.stderr or "").lower()


# ── 4) 缺样本 fail-visible ───────────────────────────────
def test_missing_sample_path_fail_visible(tmp_path):
    # 真实落盘路径是否存在取决于本机；此处只验证不存在路径的显式报错（不联网补采）。
    src = _source(sample_path=str(tmp_path / "definitely-missing-intel-sample"))
    registry, _ = m.load_registry(_write_registry(tmp_path, [src]))
    with pytest.raises(m.NoSampleError, match="样本路径不存在"):
        m.sample_source(registry, "demo", limit=3)


def test_empty_dir_fail_visible(tmp_path):
    data = tmp_path / "empty"
    data.mkdir()
    src = _source(sample_path=str(data))
    registry, _ = m.load_registry(_write_registry(tmp_path, [src]))
    with pytest.raises(m.NoSampleError, match="无可用落盘记录"):
        m.sample_source(registry, "demo", limit=3)


def test_cli_missing_sample_path_exit_3(tmp_path):
    src = _source(sample_path=str(tmp_path / "not-there"))
    path = _write_registry(tmp_path, [src])
    proc = _run_cli(["--sample", "demo"], registry=path)
    assert proc.returncode == 3
    assert "FAIL-VISIBLE" in (proc.stderr or "")


# ── 5) 仅登记/未启用来源拒绝采样 ─────────────────────────
def test_registered_source_refused_exit_4(tmp_path):
    src = _source(source_id="tdx", mode="registered", enabled=False,
                  sample_format="sqlite", sample_path=str(tmp_path / "tdx.db"))
    path = _write_registry(tmp_path, [src])
    registry, _ = m.load_registry(path)
    with pytest.raises(m.NotSamplingError, match="registered"):
        m.sample_source(registry, "tdx")
    proc = _run_cli(["--sample", "tdx"], registry=path)
    assert proc.returncode == 4


def test_disabled_sampling_source_refused(tmp_path):
    src = _source(source_id="osint_video", enabled=False, full_ingest="deferred",
                  sample_path=str(tmp_path))
    registry, _ = m.load_registry(_write_registry(tmp_path, [src]))
    with pytest.raises(m.NotSamplingError, match="未启用采样"):
        m.sample_source(registry, "osint_video")


# ── 6) 不写生产：采样前后零变化 ──────────────────────────
def test_sampling_and_listing_never_write(tmp_path):
    data = tmp_path / "prod_data"
    data.mkdir()
    _jsonl(data / "a.jsonl", [{"id": 1}, {"id": 2}])
    registry_path = _write_registry(tmp_path, [_source(sample_path=str(data))])
    registry, _ = m.load_registry(registry_path)

    before = _snapshot([data, tmp_path])
    listed = m.list_sources(registry, registry_path)
    sampled = m.sample_source(registry, "demo", limit=2)
    _run_cli(["--list", "--json"], registry=registry_path)
    _run_cli(["--sample", "demo", "--limit", "1", "--json"], registry=registry_path)
    after = _snapshot([data, tmp_path])

    assert sampled["returned"] == 2 and listed["source_count"] == 1
    assert after == before, "只读工具不得新增/修改样本目录与登记表任何文件"


# ── 7) 静态只读边界：无网络/写盘依赖与调用 ───────────────
def test_module_has_no_network_or_write_side_effects():
    source = (ROOT / "tools" / "intel_sources.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "argparse", "json", "sys", "time", "pathlib"}, \
        "只读工具不得引入网络/DB/写盘依赖：%s" % sorted(imported)
    for forbidden in ("urlopen", "requests", "socket", "httpx", "sqlite3",
                      "write_text", "write_bytes", "mkdir", "makedirs", "shutil",
                      "unlink", "rename", "replace("):
        assert forbidden not in source, "只读工具出现写/网调用：%s" % forbidden


# ── 8) CLI 契约 + 真实股吧采样冒烟 ───────────────────────
def test_cli_list_json_contract():
    proc = _run_cli(["--list", "--json"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == m.SCHEMA_LIST
    assert payload["ok"] is True and payload["readonly"] is True
    assert payload["network"] is False
    assert payload["source_count"] >= 3
    assert {s["source_id"] for s in payload["sources"]} >= {"guba", "osint_video", "tdx_market"}


def test_cli_list_text_marks_missing_sample_path(tmp_path):
    src = _source(sample_path=str(tmp_path / "missing-dir"))
    path = _write_registry(tmp_path, [src])
    proc = _run_cli(["--list"], registry=path)
    assert proc.returncode == 0, proc.stderr
    assert "MISSING（fail-visible）" in proc.stdout


@pytest.mark.skipif(
    not Path("C:/项目/老田的AI助手/quant_backtest/data/guba").is_dir(),
    reason="本机无股吧落盘数据根，跳过真实采样冒烟（不联网补采）")
def test_cli_sample_real_guba_readonly_smoke():
    proc = _run_cli(["--sample", "guba", "--limit", "2", "--json"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == m.SCHEMA_SAMPLE
    assert payload["source_id"] == "guba"
    assert payload["returned"] <= 2
    assert payload["network"] is False and payload["readonly"] is True
    for item in payload["records"]:
        assert isinstance(item["record"], dict)
        assert item["file"]
