# -*- coding: utf-8 -*-
"""P1 判定源修复 + org 写回端点守卫补丁回归测试（2026-10-19 老田批准执行）。

两层覆盖：
  A. cainiao_auth.verify_request 判定源单元级：
     - 反代链（X-Portal-Proxy=1，nginx 覆写注入、客户端不可伪造）下
       client_address 恒回环，不再构成信任源：X-Real-IP 局域网 + 无 token
       → 拒绝（修复前该场景被 local_trusted 兜底放行——核心回归凭据）；
     - 反代链 + X-Real-IP 回环 → local_trusted（本机浏览器经 8080 无感）；
     - 反代链 + 缺 X-Real-IP → 拒绝（closed on failure）；
     - 直连 8091（无反代标记）+ 回环 remote_addr → local_trusted
       （本机直连零改造，向后兼容锚点）；
     - CAINIAO_LOCAL_TRUSTED 配置化受信清单（CIDR 段）生效与越界拒绝。
  B. 隔离 HTTP 实例（127.0.0.1:0 随机高位端口，绝不触碰 8080/8091）：
     - org 4 写端点（/api/register、/api/project/register、
       /api/agent/update、/api/agent/learn）已在 _MUTATION_POSTS；
     - 反代头 + 局域网 X-Real-IP + 无 token → 403 闸门拒绝（4 端点逐一）；
     - 本机直连无 token → 闸门放行（到达业务层校验 400，非 403）；
     - 反代头 + 局域网 X-Real-IP + 正确 X-Portal-Token → 闸门放行
       （到达业务层校验 400，非 403）。
     探针全用非法 payload（org_command_api 校验先于任何落盘，实证
     org_command_api.py:447/:567/:638），全程 registry 文件指纹守卫零写入。

运行：C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe portal/test_org_guard_p1.py
独立进程（不与 test_minute_router_isolated.py 的 sys.modules 断言同跑）。
"""
import http.server
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)

# 必须在 import portal_api 之前：跳过启动预热（官方测试钩子）+ 确定性 token
os.environ["PORTAL_NO_WARMUP"] = "1"
os.environ["PORTAL_API_TOKEN"] = "p1-guard-test-token"
os.chdir(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, STATION)
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

import cainiao_auth  # noqa: E402

RESULTS = []          # (步骤名, ok, 详情)
SERVER = None
SERVER_THREAD = None
PORT = None
TOKEN = "p1-guard-test-token"


def step(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))


# ---------------- A. 判定源单元级 ----------------

def unit_tests():
    # A1 核心回归：反代入站（client_address 恒回环）+ 局域网 X-Real-IP +
    #    无 token → 必须拒绝（修复前 local_trusted 放行）
    r = cainiao_auth.verify_request(
        {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.50"},
        "127.0.0.1", portal_token=TOKEN)
    step("A1 反代+局域网 X-Real-IP 无 token → 拒绝",
         not r.ok and r.reason != "local_trusted",
         "ok=%s reason=%r" % (r.ok, r.reason))

    # A2 反代 + 本机浏览器（X-Real-IP 回环）→ local_trusted（无感）
    r = cainiao_auth.verify_request(
        {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"},
        "127.0.0.1", portal_token=TOKEN)
    step("A2 反代+X-Real-IP 回环 → local_trusted",
         r.ok and r.reason == "local_trusted", "reason=%r" % r.reason)

    # A3 反代 + 缺 X-Real-IP → 拒绝（closed on failure）
    r = cainiao_auth.verify_request(
        {"X-Portal-Proxy": "1"}, "127.0.0.1", portal_token=TOKEN)
    step("A3 反代+缺 X-Real-IP → 拒绝",
         not r.ok and r.reason != "local_trusted",
         "ok=%s reason=%r" % (r.ok, r.reason))

    # A4 向后兼容锚点：直连 8091（无反代标记）回环 → local_trusted
    r = cainiao_auth.verify_request({}, "127.0.0.1", portal_token=TOKEN)
    step("A4 直连回环 → local_trusted（零改造）",
         r.ok and r.reason == "local_trusted", "reason=%r" % r.reason)
    r = cainiao_auth.verify_request({}, "::1", portal_token=TOKEN)
    step("A4b 直连 IPv6 回环 → local_trusted",
         r.ok and r.reason == "local_trusted", "reason=%r" % r.reason)

    # A5 机器通道不受判定源修复影响
    r = cainiao_auth.verify_request(
        {"X-Portal-Token": TOKEN, "X-Real-IP": "192.168.1.50"},
        "127.0.0.1", portal_token=TOKEN)
    step("A5 反代+局域网+正确机器 token → machine_channel",
         r.ok and r.reason == "machine_channel", "reason=%r" % r.reason)

    # A6 配置化：CAINIAO_LOCAL_TRUSTED CIDR 段生效与越界拒绝
    try:
        os.environ["CAINIAO_LOCAL_TRUSTED"] = "192.168.1.0/24"
        r = cainiao_auth.verify_request(
            {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.77"},
            "127.0.0.1", portal_token=TOKEN)
        step("A6 清单内网段 → local_trusted",
             r.ok and r.reason == "local_trusted", "reason=%r" % r.reason)
        r = cainiao_auth.verify_request(
            {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.2.77"},
            "127.0.0.1", portal_token=TOKEN)
        step("A6b 清单外网段 → 拒绝",
             not r.ok and r.reason != "local_trusted",
             "ok=%s reason=%r" % (r.ok, r.reason))
    finally:
        os.environ.pop("CAINIAO_LOCAL_TRUSTED", None)
    r = cainiao_auth.verify_request(
        {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.77"},
        "127.0.0.1", portal_token=TOKEN)
    step("A6c 环境变量清除后恢复默认（仅回环）→ 拒绝",
         not r.ok, "ok=%s" % r.ok)


# ---------------- B. 隔离 HTTP 实例 ----------------

def start_server():
    global SERVER, SERVER_THREAD, PORT
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), None)
    # 惰性绑定 Handler：portal_api 导入放在 main 里按需进行
    return srv


def http_req(path, method="GET", body=None, headers=None, timeout=60):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with safe_urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw}


PROXY_LAN = {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.50"}
ORG_WRITE_PATHS = ("/api/register", "/api/project/register",
                   "/api/agent/update", "/api/agent/learn")
# 非法探针 payload：org_command_api 校验首关即拒（早于任何 backup/write）
BAD_PAYLOADS = {
    "/api/register": {"agent_id": "test1"},          # 小写开头，校验拒绝
    "/api/project/register": {},                     # project_id 缺失，校验拒绝
    "/api/agent/update": {"agent_id": ""},           # 空 aid，校验拒绝
    "/api/agent/learn": {"agent_id": "A0", "doc": ""},  # doc 空，首关拒绝
}


def http_tests():
    import portal_api as pa
    global SERVER, SERVER_THREAD, PORT

    # B0 清单成员断言（防再漂移）
    for p in ORG_WRITE_PATHS:
        step("B0 %s 已入 _MUTATION_POSTS" % p, p in pa._MUTATION_POSTS)
    for p in ("/api/preview", "/api/project/preview"):
        step("B0 %s 未入 _MUTATION_POSTS（preview 只读不纳）" % p,
             p not in pa._MUTATION_POSTS)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), pa.Handler)
    PORT = srv.server_address[1]
    assert PORT not in (8080, 8091, 8791), "随机端口撞上在役端口"
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    SERVER, SERVER_THREAD = srv, th
    step("B1 隔离实例就绪", True, "port=%d（非 8080/8091）" % PORT)

    # 落盘守卫：全程指纹比对（探针均非法 payload，校验先于落盘）
    reg = os.path.join(STATION, "agent_platform", "registry")
    guard_files = ("org.json", "org_audit.jsonl", "model_tiers.json")

    def stamp():
        out = {}
        for f in guard_files:
            fp = os.path.join(reg, f)
            try:
                st = os.stat(fp)
                out[f] = (st.st_mtime_ns, st.st_size)
            except OSError:
                out[f] = None
        return out

    before = stamp()

    # B2 反代+局域网+无 token → 4 端点逐一 403 闸门拒绝（修复前可写）
    for p in ORG_WRITE_PATHS:
        code, body = http_req(p, method="POST", body=BAD_PAYLOADS[p],
                              headers=PROXY_LAN)
        step("B2 %s 反代+局域网无 token → 403" % p,
             code == 403 and "forbidden" in (body.get("error") or ""),
             "http=%d error=%r" % (code, (body.get("error") or "")[:40]))

    # B3 本机直连无 token → 闸门放行（业务层 400 校验拒绝，非 403）
    for p in ORG_WRITE_PATHS:
        code, body = http_req(p, method="POST", body=BAD_PAYLOADS[p])
        step("B3 %s 直连无 token → 闸门放行（400 业务校验）" % p,
             code == 400 and body.get("ok") is False,
             "http=%d error=%r" % (code, (body.get("error") or "")[:40]))

    # B4 反代+局域网+正确 X-Portal-Token → 闸门放行（400 业务校验，非 403）
    tok_headers = dict(PROXY_LAN)
    tok_headers["X-Portal-Token"] = TOKEN
    for p in ORG_WRITE_PATHS:
        code, body = http_req(p, method="POST", body=BAD_PAYLOADS[p],
                              headers=tok_headers)
        step("B4 %s 反代+局域网+token → 闸门放行（400 业务校验）" % p,
             code == 400 and body.get("ok") is False,
             "http=%d error=%r" % (code, (body.get("error") or "")[:40]))

    after = stamp()
    step("B5 落盘守卫：全程未改任何 registry 文件",
         before == after, "org.json/org_audit.jsonl/model_tiers.json 指纹不变")


def stop_server():
    global SERVER, SERVER_THREAD, PORT
    ok_stop, ok_release = False, False
    if SERVER is not None:
        SERVER.shutdown()
        SERVER_THREAD.join(timeout=10)
        ok_stop = not SERVER_THREAD.is_alive()
        SERVER.server_close()
    if PORT is not None:
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
        except OSError:
            ok_release = True
    return ok_stop, ok_release


def main():
    print("=" * 56)
    print("P1 判定源修复 + org 守卫补丁回归测试")
    print("=" * 56)
    unit_tests()
    http_tests()
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    finally:
        ok_stop, ok_release = stop_server()
        step("服务清理", ok_stop and ok_release,
             "port=%s 连接拒绝即无监听" % (PORT or 0))
        n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
        print("=" * 56)
        print("回归测试：%d 项断言，%d 失败 → %s"
              % (len(RESULTS), n_fail, "全部通过" if n_fail == 0 else "存在失败"))
        sys.exit(exit_code if n_fail == 0 else 1)
