# -*- coding: utf-8 -*-
"""MCP 工具描述 U+FFFD 乱码 · 字节级只读定位探针 v2（task-44）。

结论性证明链：
  server 子进程（Hermes 白名单 env，无 PYTHONIOENCODING/PYTHONUTF8）→ sys.stdout 编码=gbk
  → handle 里 json.dumps(ensure_ascii=False) 的中文按 GBK 字节写 stdout
  → Hermes mcp SDK TextReceiveStream(encoding="utf-8", errors="replace") 解码
  → 非法 UTF-8 字节变 U+FFFD/杂字 → WebUI /api/mcp/tools 原样透传（合法 UTF-8 JSON）

本探针：
  A. 用复刻 env 直接拉起每个 MCP server，抓 stdout 原始字节，判定 utf-8 strict / gbk 解码；
  B. 对同一 server 加 PYTHONIOENCODING=utf-8 / PYTHONUTF8=1 对照（证明修法有效）；
  C. 取 8787 GET /api/mcp/tools（登录态，只读）同一工具的描述，与
     server 原始字节的 utf-8(replace)/gbk 解码逐字比对（证明 WebUI 只是透传）；
  D. tools/call（只读 catalog_snapshot）看返回文本是否同样受累。

只读：不写配置、不启停 8787/8796/8091、不改 server 源码；一次性子进程抓完即 kill。
"""
import http.client
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent

SDK_WIN_KEYS = [
    "APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT",
    "PROCESSOR_ARCHITECTURE", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "USERNAME", "USERPROFILE",
]
HERMES_SAFE_KEYS = [
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR",
    "ALLUSERSPROFILE", "APPDATA", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432", "COMPUTERNAME", "COMSPEC", "HOMEDRIVE", "HOMEPATH",
    "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "OS", "PATHEXT", "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PUBLIC",
    "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERDOMAIN", "USERNAME",
    "USERPROFILE", "WINDIR",
]


def hermes_like_env(extra=None):
    keys = set(SDK_WIN_KEYS) | set(HERMES_SAFE_KEYS)
    env = {k: os.environ[k] for k in keys if k in os.environ}
    for k in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        env.pop(k, None)
    if extra:
        env.update(extra)
    return env


def run_server(label, command, args, extra_env=None, call=None, timeout=180):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "mcp-encoding-probe", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    if call:
        requests.append({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": call, "arguments": {}}})
    payload = ("\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n").encode("utf-8")
    env = hermes_like_env(extra_env)
    t0 = time.perf_counter()
    rec = {"label": label, "command": command, "args": args, "env_extra": bool(extra_env),
           "lines": [], "error": None, "duration_s": None, "stderr_tail": "",
           "exit_code": None}
    proc = None
    try:
        proc = subprocess.Popen([command] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, cwd=None)
        out, err = proc.communicate(payload, timeout=timeout)
        rec["exit_code"] = proc.returncode
        rec["stderr_tail"] = err.decode("utf-8", "replace").strip()[-300:]
        for line_no, raw in enumerate(out.splitlines(), 1):
            if not raw.strip():
                continue
            entry = {"line": line_no, "raw_len": len(raw)}
            try:
                msg = json.loads(raw.decode("utf-8"))
                entry["valid_utf8"] = True
                entry["decoded_by"] = "utf-8"
            except UnicodeDecodeError:
                entry["valid_utf8"] = False
                entry["decoded_by"] = "gbk"
                entry["utf8_replace"] = raw.decode("utf-8", "replace")
                try:
                    msg = json.loads(raw.decode("gbk"))
                except (UnicodeDecodeError, ValueError):
                    try:
                        msg = json.loads(raw.decode("gbk", "replace"))
                    except ValueError:
                        msg = None
            except ValueError:
                entry["valid_utf8"] = True
                entry["decoded_by"] = "utf-8(invalid-json)"
                msg = None
            entry["message"] = msg
            rec["lines"].append(entry)
    except Exception as exc:  # noqa: BLE001
        rec["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        rec["duration_s"] = round(time.perf_counter() - t0, 2)
    return rec


def tools_of(rec):
    for entry in rec["lines"]:
        msg = entry.get("message") or {}
        got = (msg.get("result") or {}).get("tools")
        if isinstance(got, list) and got:
            return entry, got
    return None, []


def first_cjk_desc(tools):
    for tool in tools:
        desc = str(tool.get("description") or "")
        if any(ord(ch) > 127 for ch in desc):
            return tool.get("name"), desc
    return None, None


def webui_tools():
    """8787 登录态只读拉 inventory（nginx 同源 trusted-header；不落 cookie/token）。"""
    conn = http.client.HTTPConnection("127.0.0.1", 8787, timeout=20)
    try:
        conn.request("GET", "/api/mcp/tools", headers={"X-Portal-User": "laotian"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return {"status": resp.status, "tools": {}}
        data = json.loads(body.decode("utf-8"))
    finally:
        conn.close()
    return {"status": 200,
            "total": data.get("total"),
            "tools": {t.get("name"): t.get("description")
                      for t in data.get("tools") or []}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    py312 = r"C:\项目\py312\python.exe"
    py311 = r"C:\Program Files\Python311\python.exe"
    T = lambda name: str(REPO / "agent_platform" / "tools" / name)  # noqa: E731
    specs = [
        # 6 个乱码 server（均无 sys.stdout.reconfigure）——按 config.yaml 的 command 原样
        ("d1-research", py311, T("d1_research_mcp.py"), "mcp__d1_research__d1_research"),
        ("dw-governance", py311, T("dw_governance_mcp.py"), "mcp__dw_governance__dw_stats"),
        ("dw-quality", py312, T("dw_quality_mcp.py"), "mcp__dw_quality__quality_snapshot"),
        ("dw-catalog", py312, T("dw_catalog_mcp.py"), "mcp__dw_catalog__catalog_snapshot"),
        ("huangzhong", py311, T("huangzhong_mcp.py"), "mcp__huangzhong__huangzhong_status"),
        ("kb-corpus", py312, T("kb_corpus_mcp.py"), "mcp__kb_corpus__kb_status"),
        # 干净对照（有 reconfigure / Node 默认 UTF-8）
        ("factor-tools[clean]", py312, T("factor_tools_mcp.py"), "mcp__factor_tools__factor_reports"),
        ("stock-data[clean]", py311, str(Path(r"C:\项目\zerobound-kg\stock_data_mcp.py")),
         "mcp__stock_data__data_overview"),
    ]

    report = {
        "tool": "mcp_stdio_encoding_probe_v2",
        "task": "task-44 / WO-MCP-ENCODING-AUDIT-20261104",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True,
        "causal_chain": [
            "Hermes tools/mcp_tool_config.py::_build_safe_env 白名单不含 PYTHONIOENCODING/PYTHONUTF8；"
            "mcp SDK get_default_environment() 同样不含",
            "6 个 MCP server 用 sys.stdout.write(json.dumps(ensure_ascii=False)) 且未 reconfigure(utf-8)",
            "Windows 子进程 stdout=pipe 且无 UTF-8 强制时 sys.stdout.encoding=gbk（cp936）→ 中文按 GBK 字节写出",
            "Hermes mcp 2.0.0 TextReceiveStream(encoding='utf-8', errors='replace')（mcp_tool_transport.py L371-374）"
            "→ 非法 UTF-8 变 U+FFFD",
            "WebUI /api/mcp/tools 把已损坏 str 原样 json.dumps → 合法 UTF-8 JSON 里携带乱码（渲染层无罪）",
        ],
        "child_stdout_encoding": {},
        "servers": [],
        "webui": {},
        "compare": {},
        "tools_call_impact": {},
    }

    # A0. 子进程 stdout 编码自证
    for extra, tag in ((None, "hermes_like_env"),
                       ({"PYTHONIOENCODING": "utf-8"}, "+PYTHONIOENCODING=utf-8"),
                       ({"PYTHONUTF8": "1"}, "+PYTHONUTF8=1")):
        code = ("import sys, locale;"
                "print('stdout=%s preferred=%s utf8_mode=%s' % "
                "(sys.stdout.encoding, locale.getpreferredencoding(False), sys.flags.utf8_mode))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             env=hermes_like_env(extra)).stdout
        report["child_stdout_encoding"][tag] = out.decode("utf-8", "replace").strip()
    report["child_env_has_pythonioencoding"] = "PYTHONIOENCODING" in hermes_like_env()
    report["child_env_has_pythonutf8"] = "PYTHONUTF8" in hermes_like_env()

    # A. 逐 server 抓字节
    for name, command, path, webui_tool in specs:
        rec = run_server(name, command, [path])
        entry, tools = tools_of(rec)
        tool_name, desc = first_cjk_desc(tools)
        summary = {
            "label": name,
            "spawn": [command, path],
            "tools_count": len(tools),
            "lines_valid_utf8": [e.get("valid_utf8") for e in rec["lines"]],
            "decoded_by": [e.get("decoded_by") for e in rec["lines"]],
            "all_utf8": all(e.get("valid_utf8") for e in rec["lines"]),
            "sample_tool": tool_name,
            "sample_desc_gbk_or_utf8": desc,
            "error": rec["error"],
            "duration_s": rec["duration_s"],
        }
        if entry and not entry.get("valid_utf8"):
            summary["sample_line_utf8_replace"] = entry.get("utf8_replace")
        report["servers"].append(summary)
        report.setdefault("_raw", []).append({"label": name, "lines": rec["lines"]})

    # B. 修复对照（同一 server + UTF-8 强制 env）
    for tag, extra in (("+PYTHONIOENCODING=utf-8", {"PYTHONIOENCODING": "utf-8"}),
                       ("+PYTHONUTF8=1", {"PYTHONUTF8": "1"})):
        rec = run_server("dw-catalog" + tag, py312, [T("dw_catalog_mcp.py")], extra_env=extra)
        entry, tools = tools_of(rec)
        tool_name, desc = first_cjk_desc(tools)
        report["servers"].append({
            "label": "dw-catalog" + tag,
            "spawn": [py312, T("dw_catalog_mcp.py")],
            "tools_count": len(tools),
            "lines_valid_utf8": [e.get("valid_utf8") for e in rec["lines"]],
            "decoded_by": [e.get("decoded_by") for e in rec["lines"]],
            "all_utf8": all(e.get("valid_utf8") for e in rec["lines"]),
            "sample_tool": tool_name,
            "sample_desc_gbk_or_utf8": desc,
            "error": rec["error"],
            "duration_s": rec["duration_s"],
        })

    # C. 与 8787 WebUI 逐字比对
    web = webui_tools()
    report["webui"] = {"status": web["status"], "total": web.get("total"),
                       "source": "GET http://127.0.0.1:8787/api/mcp/tools (X-Portal-User, 只读)"}
    for summary in report["servers"]:
        label = summary["label"]
        # 找到该 server 对应的 WebUI 工具（按 prefix）
        if label.startswith("dw-catalog"):
            prefix = "mcp__dw_catalog__"
        elif label.startswith("d1-research"):
            prefix = "mcp__d1_research__"
        elif label.startswith("dw-governance"):
            prefix = "mcp__dw_governance__"
        elif label.startswith("dw-quality"):
            prefix = "mcp__dw_quality__"
        elif label.startswith("huangzhong"):
            prefix = "mcp__huangzhong__"
        elif label.startswith("kb-corpus"):
            prefix = "mcp__kb_corpus__"
        elif label.startswith("factor-tools"):
            prefix = "mcp__factor_tools__"
        elif label.startswith("stock-data"):
            prefix = "mcp__stock_data__"
        else:
            continue
        candidates = [(k, v) for k, v in web["tools"].items() if str(k).startswith(prefix)]
        if not candidates:
            continue
        webui_name, webui_desc = candidates[0]
        # 从 server 原始字节重建 WebUI 会看到的「utf-8 replace」描述
        suffix = webui_name[len(prefix):]
        line = summary.get("sample_line_utf8_replace")
        reconstructed = None
        if line:
            try:
                obj = json.loads(line)  # 已是 replacement 文本，JSON 结构可解析
                for tool in (obj.get("result") or {}).get("tools") or []:
                    if tool.get("name") == suffix:
                        reconstructed = tool.get("description")
            except ValueError:
                pass
        summary["webui_compare"] = {
            "webui_tool": webui_name,
            "webui_desc_is_garbled": "\ufffd" in str(webui_desc or ""),
            "equals_server_utf8_replace": reconstructed is not None and reconstructed == webui_desc,
            "server_utf8_replace_reconstructed": reconstructed,
            "webui_desc": webui_desc,
        }

    # D. tools/call 返回文本同样受累（只读 catalog_snapshot）
    rec = run_server("dw-catalog[tools/call catalog_snapshot]", py312,
                     [T("dw_catalog_mcp.py")], call="catalog_snapshot")
    call_entry = next((e for e in rec["lines"] if e["line"] == 3), None)
    if call_entry is not None:
        impact = {"valid_utf8": call_entry.get("valid_utf8"),
                  "decoded_by": call_entry.get("decoded_by")}
        msg = call_entry.get("message") or {}
        content = (msg.get("result") or {}).get("content") or []
        impact["result_text_gbk_decoded"] = (content[0].get("text") if content else None)
        if call_entry.get("utf8_replace"):
            try:
                bad = json.loads(call_entry["utf8_replace"])
                bad_content = (bad.get("result") or {}).get("content") or []
                impact["result_text_utf8_replace"] = bad_content[0].get("text") if bad_content else None
            except ValueError:
                impact["result_text_utf8_replace"] = call_entry["utf8_replace"][:300]
        report["tools_call_impact"] = impact

    # 去掉内部 raw 明细（保留摘要；原始行另存）
    raw = report.pop("_raw", [])
    out = OUT / "2026-11-04_mcp_stdio_encoding_raw.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    raw_out = OUT / "2026-11-04_mcp_stdio_encoding_rawlines.json"
    raw_out.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({
        "out": str(out),
        "child_stdout_encoding": report["child_stdout_encoding"],
        "webui": report["webui"],
        "server_utf8_verdict": {s["label"]: ("utf8" if s["all_utf8"] else "gbk/non-utf8")
                                for s in report["servers"]},
        "webui_equals_utf8replace": {s["label"]: (s.get("webui_compare") or {}).get(
            "equals_server_utf8_replace") for s in report["servers"] if s.get("webui_compare")},
        "tools_call_impact": {k: report["tools_call_impact"].get(k)
                              for k in ("valid_utf8", "decoded_by")},
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
