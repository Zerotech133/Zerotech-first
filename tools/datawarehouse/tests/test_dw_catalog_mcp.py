# -*- coding: utf-8 -*-
"""dw-catalog MCP 测试：stdio JSON-RPC 真调 + 真实值对账 + 只读性。

参考 tools/datawarehouse/tests/test_dw_wp_i3.py 的 stdio 用例风格：
  每个用例起一个 MCP server 子进程，喂 JSON-RPC 行，解析 content[0].text。
覆盖（≥2）：
  - initialize / tools/list（5 工具名）
  - registry_stats / qualification 与仓库真实文件对账
  - catalog_snapshot / lifecycle 子进程真调（catalog 真值）
  - table_info 命中 + 未知表 isError
  - 未知工具 isError
  - 只读性：调用前后 catalog/registry 的 sha256/大小/mtime 不变
  - --selftest 退出码与工具数
"""
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "agent_platform" / "tools" / "dw_catalog_mcp.py"
DATA = REPO / "quant_backtest" / "data"
REGISTRY = DATA / "warehouse_registry.json"
CATALOG = DATA / "catalog.json"

TOOL_NAMES = ["catalog_snapshot", "registry_stats", "lifecycle", "table_info", "qualification"]
STATES = ("evidence_ok", "unknown", "evidence_partial", "planned", "deprecated")


# ---------------------------------------------------------------- 测试辅助
def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _call(msgs: list) -> list:
    """起一次 server 子进程，返回逐行 JSON-RPC 响应。"""
    inp = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT)], input=inp,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    return [json.loads(x) for x in r.stdout.splitlines() if x.strip()]


def _init_msg(mid=1):
    return {"jsonrpc": "2.0", "id": mid, "method": "initialize", "params": {}}


def _tool_msg(name, arguments=None, mid=2):
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


def _tool_body(resp: dict) -> dict:
    """取 tools/call 的 content 文本并反序列化；断言非 isError。"""
    assert resp["result"]["isError"] is False, resp["result"]["content"][0]["text"]
    return json.loads(resp["result"]["content"][0]["text"])


def _fingerprint(path: Path) -> dict:
    st = path.stat()
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": st.st_size, "mtime_ns": st.st_mtime_ns}


# ---------------------------------------------------------------- 协议层
def test_initialize_and_tools_list():
    lines = _call([_init_msg(), {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}])
    init = lines[0]["result"]
    assert init["serverInfo"]["name"] == "dw-catalog"
    assert init["protocolVersion"] == "2024-11-05"
    names = [t["name"] for t in lines[1]["result"]["tools"]]
    assert names == TOOL_NAMES, names
    for tool in lines[1]["result"]["tools"]:
        assert tool["description"] and isinstance(tool["inputSchema"], dict)


def test_unknown_tool_is_error():
    lines = _call([_init_msg(), _tool_msg("no_such_tool")])
    assert lines[1]["result"]["isError"] is True
    assert "unknown tool" in lines[1]["result"]["content"][0]["text"]


# ---------------------------------------------------------------- 真实值对账
def test_registry_stats_matches_file():
    reg = _load(REGISTRY)
    lines = _call([_init_msg(), _tool_msg("registry_stats")])
    body = _tool_body(lines[1])
    assert body["read_only"] is True and body["dry_run"] is True
    assert body["physical_count"] == len(reg["physical_tables"]) == reg["physical_count"]
    assert body["display_count"] == len(reg["display_assets"]) == reg["display_count"]
    real = Counter(str(e.get("qualification_status") or "unknown")
                   for e in reg["physical_tables"])
    assert body["qualification_status"] == {k: real.get(k, 0) for k in STATES}
    sha = hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    assert body["catalog"]["sha256"] == sha
    assert body["catalog_sha256_match"] == (reg.get("catalog_sha256") == sha)
    assert body["catalog"]["tables"] == len(_load(CATALOG)["tables"])


def test_qualification_distribution():
    reg = _load(REGISTRY)
    lines = _call([_init_msg(), _tool_msg("qualification")])
    body = _tool_body(lines[1])
    assert set(STATES).issubset(body["counts"].keys())
    assert body["physical_total"] == len(reg["physical_tables"])
    assert sum(body["counts"][k] for k in STATES) == body["physical_total"]
    assert abs(sum(body["pct"][k] for k in STATES) - 100.0) <= 0.5
    assert body["display_total"] == len(reg["display_assets"])


def test_catalog_snapshot_real_values():
    cat = _load(CATALOG)
    lines = _call([_init_msg(), _tool_msg("catalog_snapshot", {"limit": 5})])
    body = _tool_body(lines[1])
    assert body["read_only"] is True
    assert body["catalog"]["tables"] == len(cat["tables"]) >= 200
    assert body["catalog"]["version"] == cat["version"]
    assert body["catalog"]["sha256"] == hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    assert body["drift_sample"]["tables"] == 5
    assert isinstance(body.get("rc"), int)  # 底层工具约定退出码随载荷返回


def test_lifecycle_coverage():
    cat = _load(CATALOG)
    lines = _call([_init_msg(), _tool_msg("lifecycle", {"limit": 3})])
    body = _tool_body(lines[1])
    assert body["read_only"] is True
    assert body["total"] == len(cat["tables"])
    assert body["missing_count"] == body["implicit_active"]
    assert len(body["missing"]) == min(3, body["missing_count"])
    assert body["invalid_count"] == 0
    assert isinstance(body["distribution"], dict) and isinstance(body["by_domain"], dict)
    assert 0.0 <= body["coverage_pct"] <= body["effective_coverage_pct"] <= 100.0


def test_table_info_known_and_alias():
    reg = _load(REGISTRY)
    table = reg["physical_tables"][0]["physical_tables"][0]
    lines = _call([_init_msg(), _tool_msg("table_info", {"table": table})])
    body = _tool_body(lines[1])
    assert body["registered"] is True
    assert body["registry"]["asset_id"] == reg["physical_tables"][0]["asset_id"]
    assert body["catalog"]["store"]  # 路径来自 catalog.store
    # catalog 逻辑别名（未登记为物理表的别名键）应返回 resolves 明细，而不是 isError
    cat = _load(CATALOG)
    reg_names = set(cat["tables"])
    for entry in reg["physical_tables"]:
        reg_names.add(str(entry.get("asset_id")))
        reg_names.update(str(t) for t in (entry.get("physical_tables") or []))
        reg_names.update(str(a) for a in (entry.get("aliases") or []))
    alias_only = sorted(k for k in (cat.get("aliases") or {}) if k not in reg_names)
    assert alias_only, "catalog 应存在未登记为物理表的逻辑别名"
    lines2 = _call([_init_msg(), _tool_msg("table_info", {"table": alias_only[0]})])
    body2 = _tool_body(lines2[1])
    assert body2["registered"] is False
    assert body2["alias"]["resolves"]
    assert isinstance(body2["resolved"], list)


def test_table_info_unknown_is_error():
    lines = _call([_init_msg(), _tool_msg("table_info", {"table": "definitely_missing_table"})])
    assert lines[1]["result"]["isError"] is True
    assert "未登记" in lines[1]["result"]["content"][0]["text"]


# ---------------------------------------------------------------- 只读性 / 自检
def test_calls_are_readonly():
    before = {p.name: _fingerprint(p) for p in (CATALOG, REGISTRY)}
    msgs = [_init_msg(), _tool_msg("registry_stats", mid=2), _tool_msg("qualification", mid=3),
            _tool_msg("table_info", {"table": "adj_factor"}, mid=4),
            _tool_msg("catalog_snapshot", {"limit": 2}, mid=5),
            _tool_msg("lifecycle", {"limit": 2}, mid=6)]
    lines = _call(msgs)
    assert all(line["result"].get("isError") is False for line in lines[1:])
    after = {p.name: _fingerprint(p) for p in (CATALOG, REGISTRY)}
    assert before == after, "只读工具不得写入 catalog/registry"
    # 数据根不得出现本工具产生的临时文件
    assert not list(DATA.glob("*.tmp*")), "不应留下临时文件"


def test_selftest():
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "--selftest"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout)
    assert out["server"] == "dw-catalog"
    assert out["tools"] == len(TOOL_NAMES) == 5
    assert out["tool_names"] == TOOL_NAMES
    assert out["read_only"] is True
    assert out["missing"] == []
