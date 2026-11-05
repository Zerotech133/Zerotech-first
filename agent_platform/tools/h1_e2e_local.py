# -*- coding: utf-8 -*-
"""H1 端到端本地验收（不部署）：真实 portal_api Handler + 内部分析网关 + stock-analysis MCP。

做法：
1) 在 127.0.0.1 随机端口启动真实 portal_api Handler（含 api_analysis_internal 路由）；
2) 设置 ANALYSIS_INTERNAL_TOKEN=test token；
3) 以子进程启动 stock_analysis_mcp.py，走 JSON-RPC 调用 9 个工具；
4) 验证：200/403、watchlist/data-status/report/events/overview 结果、提交类入队（job_id）。
不产生生产改动（队列文件会写，但 job 为真实入队，可被盘后批消费；测试后清理 done 文件）。
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
if not os.path.isdir(os.path.join(ROOT, "portal")):
    ROOT = r"C:\项目\老田的AI助手"
PORTAL = os.path.join(ROOT, "portal")
# 不落任何固定 token：优先取环境变量，否则每次运行生成随机测试 token（仅本机进程内使用）
TOKEN = os.environ.get("H1_E2E_TOKEN") or secrets.token_hex(16)
os.environ["ANALYSIS_INTERNAL_TOKEN"] = TOKEN

sys.path.insert(0, ROOT)
sys.path.insert(0, PORTAL)

import portal_api  # noqa: E402

server = ThreadingHTTPServer(("127.0.0.1", 0), portal_api.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % port
print("portal on", BASE)


def http(path, token=TOKEN):
    req = urllib.request.Request(BASE + path, headers={"X-Analysis-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


results = {}
st, body = http("/api/analysis/internal/health", token="")     # 403
results["no_token"] = (st, body.get("error"))
st, body = http("/api/analysis/internal/health", token="wrong")  # 403
results["wrong_token"] = (st, body.get("error"))
st, body = http("/api/analysis/internal/health")
results["health"] = (st, body.get("ok"), body.get("queue"))
st, body = http("/api/analysis/internal/watchlist")
results["watchlist"] = (st, body.get("codes"))
st, body = http("/api/analysis/internal/overview")
results["overview"] = (st, body.get("ok"), len(body.get("latest") or []), body.get("events"))
st, body = http("/api/analysis/internal/data-status?code=600337.SH")
results["data_status"] = (st, body.get("has_run"), (body.get("summary") or {}).get("coverage"))
run_id = (body.get("run") or {}).get("run_id")
if run_id:
    st, body = http("/api/analysis/internal/report?run_id=" + run_id)
    results["report"] = (st, body.get("ok"), len((body.get("content") or "")[:80]))
st, body = http("/api/analysis/internal/events?code=600337.SH&limit=3&status=all")
results["events"] = (st, body.get("ok"), len(body.get("events") or []))
st, body = http("/api/analysis/internal/data-status?code=BAD")
results["bad_code"] = (st, body.get("error"))

# ---- MCP stdio 子进程 ----
env = dict(os.environ)
env["ANALYSIS_GATEWAY_URL"] = BASE
env["ANALYSIS_INTERNAL_TOKEN"] = TOKEN
env["ANALYSIS_INTERNAL_TOKEN_FILE"] = r"C:\项目\commercial\analysis_internal.token"
mcp = subprocess.Popen(
    [sys.executable, "-X", "utf8", os.path.join(ROOT, "agent_platform", "tools", "stock_analysis_mcp.py")],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8")


def rpc(obj):
    mcp.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    mcp.stdin.flush()
    return json.loads(mcp.stdout.readline())


def call(name, args=None):
    return rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": args or {}}})


r = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
results["mcp_initialize"] = (r["result"]["serverInfo"]["name"], r["result"]["protocolVersion"])
r = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
results["mcp_tools"] = [t["name"] for t in r["result"]["tools"]]
for name, args in (("sa_health", {}), ("sa_watchlist", {}), ("sa_overview", {}),
                   ("sa_data_status", {"code": "600337.SH"}),
                   ("sa_events", {"code": "600337.SH", "limit": 2})):
    r = call(name, args)
    payload = json.loads(r["result"]["content"][0]["text"])
    results["mcp_" + name] = (payload.get("ok"), r["result"]["isError"])
# 报告工具（取最新 run）
r = call("sa_report", {"code": "600337.SH"})
payload = json.loads(r["result"]["content"][0]["text"])
results["mcp_sa_report"] = (payload.get("ok"), bool(payload.get("content")))
# 提交类：sa_analyze（会入文件队列；随后清理该 pending 文件）
r = call("sa_analyze", {"code": "600337.SH"})
payload = json.loads(r["result"]["content"][0]["text"])
results["mcp_sa_analyze"] = (payload.get("ok"), bool(payload.get("job_id")))
job_id = payload.get("job_id")
if job_id:
    # 清理测试入队文件，避免污染盘后批
    for k in ("pending", "running", "done"):
        p = os.path.join(r"C:\项目\analysis_work\queue", k, job_id + ".json")
        if os.path.exists(p):
            os.remove(p)
            results.setdefault("cleanup", []).append(k)
r = call("sa_batch", {"codes": ["600337.SH", "BAD"]})
payload = json.loads(r["result"]["content"][0]["text"])
results["mcp_sa_batch_invalid"] = (payload.get("ok"), r["result"]["isError"])

mcp.stdin.close()
try:
    mcp.wait(timeout=5)
except Exception:
    mcp.kill()
server.shutdown()

print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
ok = (
    results["no_token"][0] == 403 and results["wrong_token"][0] == 403
    and results["health"][0] == 200 and results["health"][1] is True
    and results["watchlist"][0] == 200 and len(results["watchlist"][1] or []) >= 3
    and results["data_status"][0] == 200 and results["data_status"][2] is not None
    and results["bad_code"][0] == 400
    and results["mcp_initialize"][0] == "stock-analysis"
    and len(results["mcp_tools"]) == 9
    and results["mcp_sa_analyze"][0] is True
    and results["mcp_sa_batch_invalid"][0] is False
)
print("E2E_RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
