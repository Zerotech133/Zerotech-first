# -*- coding: utf-8 -*-
"""K-ORG-190 指挥台端到端预演（容器重启前的预验凭据）

背景：K-ORG-190 后端（portal_api.py + org_command_api.py）代码层已全落地
（34/34 单测绿），但线上 ai-portal 容器未重启、新端点未生效。本脚本在本机
拉起一个**隔离的 portal_api 实例**（绑定 127.0.0.1 随机高位端口，OS 分配，
绝不触碰 8080/8091），对真实 STATION **只读**打端点，验证接线与数据面全链。

启动方式结论（调查于 2026-10-19）：
  portal_api.py:175  PORT = 8091 写死，不可参数化；
  portal_api.py:4566 服务仅在 if __name__ == "__main__" 块内启动，
                     模块导入本身无端口副作用；
  portal_api.py:465  PORTAL_NO_WARMUP=1 可跳过启动预热线程（官方测试钩子）。
  故采用：测试进程内 import portal_api，自建 ThreadingHTTPServer 绑
  127.0.0.1:0（端口 0 = OS 随机分配空闲高位端口），与既有冒烟脚本
  _smoke_algo_market.py 同模式。

铁律：
  ① 全链只读——唯一 POST 的 /api/preview 是"只算不写"的预览端点
    （org_command_api.api_preview 仅返回 plan 行，不落盘）；
  ② /api/register **不**对合法 payload 调用（会写真 registry），仅用
    非法 payload（agent_id 小写开头，_validate_agent_payload 首关即拒，
    早于任何 backup/write）验证 400 路径；
  ③ 测试结束 finally 里关停服务，并断言端口已释放（连接拒绝）。

运行：C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe portal/test_org_e2e.py
零三方依赖，纯标准库。
"""
import http.server
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)

# 必须在 import portal_api 之前设置：跳过启动预热（真实 datamart.read
# 冷算 ~100s，与测试无关且会产生后台线程竞态）
os.environ["PORTAL_NO_WARMUP"] = "1"
# 与生产启动 cwd 一致（部分模块以相对路径兜底）
os.chdir(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, STATION)
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

import portal_api as pa  # noqa: E402
import logging

RESULTS = []          # (步骤名, ok, 详情)
SERVER = None         # 全局引用，finally 清理用
SERVER_THREAD = None
PORT = None


# ---------------- 基础设施 ----------------

def start_server():
    """进程内拉起隔离实例：绑定 127.0.0.1:0（OS 随机分配空闲高位端口）。"""
    global SERVER, SERVER_THREAD, PORT
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), pa.Handler)
    port = srv.server_address[1]
    # 防御性断言：绝不落到 8080/8091 等在役端口
    assert port not in (8080, 8091, 8791), "随机端口撞上在役端口 %d" % port
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    SERVER, SERVER_THREAD, PORT = srv, th, port
    return port


def stop_server():
    """关停服务并断言端口释放（Windows：ThreadingHTTPServer 已带
    allow_reuse_address，靠"新连接被拒绝"判定释放，而非重复绑定——
    Windows 下 SO_REUSEADDR 允许绑定活跃 LISTEN 端口，绑定成功不构成证据）。"""
    ok_stop, ok_release = False, False
    if SERVER is not None:
        SERVER.shutdown()                      # 让 serve_forever 返回
        SERVER_THREAD.join(timeout=10)
        ok_stop = not SERVER_THREAD.is_alive()
        SERVER.server_close()                  # 关监听 socket
    if PORT is not None:
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
        except OSError:
            ok_release = True                  # 连接被拒 = 无监听 = 已释放
    return ok_stop, ok_release


def http_req(path, method="GET", body=None, timeout=60):
    """打本机隔离实例；返回 (status, text)。4xx/5xx 不抛异常（读响应体）。"""
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"}
                                 if data else {})
    try:
        with safe_urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def wait_ready(deadline_s=60):
    """轮询 /health 直到服务可应答（导入后首请求可能撞冷启动）。"""
    global PORT
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            code, _ = http_req("/health", timeout=5)
            if code == 200:
                return True
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/test_org_e2e.py", exc)
        time.sleep(0.3)
    return False


def step(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))


def file_stamp(path):
    """文件指纹（mtime_ns, size），用于"只预览不落盘"守卫；缺失返回 None。"""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# ---------------- 端到端预演 ----------------

def main():
    port = start_server()
    print("隔离实例已启动：http://127.0.0.1:%d（与 8080/8091 完全隔离）" % port)
    if not wait_ready():
        step("服务就绪", False, "60s 内 /health 未应答")
        return 1
    step("服务就绪", True, "port=%d（随机高位端口，非 8080/8091）" % port)

    # ---- ① GET /api/org：数据面 bundle ----
    code, text = http_req("/api/org")
    org = json.loads(text) if code == 200 else {}
    agents = (org.get("bundle") or {}).get("agents") or {}
    a0 = agents.get("A0") or {}
    step("① GET /api/org ok:true",
         code == 200 and org.get("ok") is True,
         "http=%d ok=%r" % (code, org.get("ok")))
    step("① bundle.agents >= 30（实证 50）",
         len(agents) >= 30,
         "实际 %d 个（stats=%r）" % (len(agents),
                                    (org.get("bundle") or {}).get("stats")))
    step("① 含 A0 且心跳三态合法",
         "A0" in agents and a0.get("heartbeat_state") in
         ("green", "stale", "no_ledger"),
         "A0.heartbeat_state=%r" % a0.get("heartbeat_state"))

    # ---- ② GET /api/org/meta：模型档位表 + 技能索引 ----
    code, text = http_req("/api/org/meta")
    meta = json.loads(text) if code == 200 else {}
    skills = meta.get("skills") or {}
    n_tools, n_algos = len(skills.get("tools") or []), len(skills.get("algos") or [])
    n_autom = len(skills.get("automations") or [])
    step("② GET /api/org/meta ok:true",
         code == 200 and meta.get("ok") is True,
         "http=%d ok=%r" % (code, meta.get("ok")))
    step("② models=4",
         len(meta.get("models") or {}) == 4,
         "实际 %d 个：%s" % (len(meta.get("models") or {}),
                            sorted((meta.get("models") or {}).keys())))
    step("② skills.tools=22 / algos=6 / automations=5（skill_registry 实证口径）",
         (n_tools, n_algos, n_autom) == (22, 6, 5),
         "tools=%d algos=%d automations=%d 总量=%d"
         % (n_tools, n_algos, n_autom, n_tools + n_algos + n_autom))
    step("② 技能总量 >= 33", n_tools + n_algos + n_autom >= 33,
         "总量=%d" % (n_tools + n_algos + n_autom))

    # ---- ③④⑤ 页面三投影 ----
    code, text = http_req("/org")
    step("③ GET /org → 200 且含'田丰'", code == 200 and "田丰" in text,
         "http=%d len=%d" % (code, len(text)))
    code, text = http_req("/aim")
    step("④ GET /aim → 200 且含'技能'（同源投影行）",
         code == 200 and "技能" in text, "http=%d len=%d" % (code, len(text)))
    code, text = http_req("/portal")
    step("⑤ GET /portal → 200 且含'数据口径'",
         code == 200 and "数据口径" in text, "http=%d len=%d" % (code, len(text)))

    # ---- ⑥ POST /api/preview：只预览不落盘 ----
    # 落盘守卫：预览前后比对 registry 关键文件指纹
    reg = os.path.join(STATION, "agent_platform", "registry")
    guard = {f: file_stamp(os.path.join(reg, f))
             for f in ("org.json", "org_audit.jsonl", "model_tiers.json")}
    payload = {"agent_id": "TEST1", "grade": "E1", "model": "qwen3.5:9b",
               "tool_preset": "devtask", "dept": "dev"}
    code, text = http_req("/api/preview", method="POST", body=payload)
    prev = json.loads(text) if text else {}
    plan = prev.get("plan") or []
    step("⑥ POST /api/preview ok:true 且 plan 非空",
         code == 200 and prev.get("ok") is True and len(plan) > 0,
         "http=%d ok=%r plan行数=%d" % (code, prev.get("ok"), len(plan)))
    untouched = all(file_stamp(os.path.join(reg, f)) == st
                    for f, st in guard.items())
    step("⑥ 落盘守卫：预览未改任何 registry 文件", untouched,
         "org.json/org_audit.jsonl/model_tiers.json 指纹不变")

    # ---- ⑦ POST /api/register：非法 payload 走 400（绝不真调用合法路径）----
    # agent_id 小写开头 → _validate_agent_payload 首关即拒（早于任何
    # backup/write，org_command_api.py:377），对真 registry 零风险。
    bad = {"agent_id": "test1", "grade": "E1", "model": "qwen3.5:9b",
           "tool_preset": "devtask", "dept": "dev"}
    code, text = http_req("/api/register", method="POST", body=bad)
    reg_out = json.loads(text) if text else {}
    step("⑦ POST /api/register 非法 payload → ok:false + HTTP 400",
         code == 400 and reg_out.get("ok") is False,
         "http=%d error=%r" % (code, (reg_out.get("error") or "")[:60]))
    step("⑦ 落盘守卫：register 拒绝路径未改任何 registry 文件",
         all(file_stamp(os.path.join(reg, f)) == st
             for f, st in guard.items()), "指纹不变")

    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    finally:
        ok_stop, ok_release = stop_server()
        print("[清理] serve 线程已停=%s 端口 %s 已释放=%s"
              % (ok_stop, PORT, ok_release))
        step("服务进程清理", ok_stop and ok_release,
             "port=%d 连接拒绝即无监听" % (PORT or 0))
        n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
        print("=" * 56)
        print("端到端预演：%d 项断言，%d 失败 → %s"
              % (len(RESULTS), n_fail, "全部通过" if n_fail == 0 else "存在失败"))
        sys.exit(exit_code if n_fail == 0 else 1)
