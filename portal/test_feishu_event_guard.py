# K-STOCK-397 守卫测试：回调验签字节化等价性 + 未验签降级告警状态机。
# 运行：python portal/test_feishu_event_guard.py（不真发飞书——send 被
# monkeypatch；flag 走 FEISHU_EVENT_FLAG 注入的临时路径，不碰生产态）。
import hashlib, json, os, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── T1：签名公式等价/失真修复 ─────────────────────────
ts, nc, ek = "1760000000", "abc123", "test-encrypt-key"
ok_body = json.dumps({"type": "url_verification",
                      "challenge": "中文挑战✓"}).encode("utf-8")
old = hashlib.sha256((ts + nc + ek + ok_body.decode("utf-8", "replace")
                      ).encode("utf-8")).hexdigest()
new = hashlib.sha256((ts + nc + ek).encode("utf-8") + ok_body).hexdigest()
assert old == new, "合法 UTF-8 body 上新旧公式应等价"

bad_body = ok_body + b'\xff\xfe\x80'          # 非法 UTF-8 尾巴
old_bad = hashlib.sha256((ts + nc + ek + bad_body.decode("utf-8", "replace")
                          ).encode("utf-8")).hexdigest()
new_bad = hashlib.sha256((ts + nc + ek).encode("utf-8") + bad_body).hexdigest()
assert new_bad != old_bad, "旧公式在非 UTF-8 body 上应失真（新=官方字节真值）"
print("T1 验签字节化 OK（合法UTF-8等价 / 非UTF-8失真修复）")

# ── T2/T3：降级告警状态机（同日一次 + 计数 + 跨日重置） ──
import api_misc  # noqa: E402
H = api_misc.Handler
_called = []
# 桩前先保存原引用：api_misc.subprocess 就是标准库 subprocess 模块本体，
# 模块级替换 = 污染全进程（2026-10-20 判例：后续 test_subscription_smoke
# 的子进程调用拿到无 stdout 的桩对象 → AttributeError）。文件末尾恢复。
_orig_subprocess_run = api_misc.subprocess.run
api_misc.subprocess.run = (lambda *a, **k:
                           _called.append(a) or types.SimpleNamespace(
                               returncode=0))
fake = types.SimpleNamespace(log_message=lambda *a, **k: _called.append(("log", a)))

flag = os.path.join(tempfile.gettempdir(), "_test_397_flag.json")
os.environ["FEISHU_EVENT_FLAG"] = flag

H._feishu_unverified_alert(fake)   # 第 1 次：推送 + 日志
H._feishu_unverified_alert(fake)   # 第 2/3 次：只计数
H._feishu_unverified_alert(fake)
sends = [c for c in _called if c and c[0] != "log"]
logs = [c for c in _called if c and c[0] == "log"]
assert len(sends) == 1 and len(logs) == 1, (sends, logs)
with open(flag, encoding="utf-8") as f:
    st = json.load(f)
assert st["count"] == 3 and st["alerted"] is True, st
print("T2 同日节流 OK: count=%d 推送=%d 日志=%d" % (st["count"],
                                                   len(sends), len(logs)))

with open(flag, "w", encoding="utf-8") as f:   # 伪造昨日态
    json.dump({"day": "1970-01-01", "count": 9, "alerted": True}, f)
_called.clear()
H._feishu_unverified_alert(fake)
sends = [c for c in _called if c and c[0] != "log"]
assert len(sends) == 1, "跨日应重新告警"
with open(flag, encoding="utf-8") as f:
    assert json.load(f)["count"] == 1, "跨日计数应归零"
print("T3 跨日重置 OK")

# 收尾：flag 写归档态（保留文件规避删除红线；day=远古自然失效）
with open(flag, "w", encoding="utf-8") as f:
    json.dump({"day": "1970-01-01", "count": 0, "alerted": True}, f)
del os.environ["FEISHU_EVENT_FLAG"]

# ── T4（K-STOCK-399 F2 收紧）：事件分发路由 ───────────
def _fake_handler():
    resp, routed = [], []
    h = types.SimpleNamespace(
        headers={}, _json=lambda obj, code=200: resp.append((obj, code)),
        _handle_card_action=lambda ev: routed.append(ev),
        _feishu_encrypt_key=lambda: None,
        _feishu_unverified_alert=lambda: None,
        log_message=lambda *a, **k: None)
    return h, resp, routed

# T4a：外来事件含字符串 action 字段（如 contact/approval 变更）→ 不误路由
h, resp, routed = _fake_handler()
H._api_feishu_event(h, json.dumps({
    "header": {"event_type": "contact.user.updated_v3"},
    "event": {"action": "created", "obj": {}}}).encode("utf-8"))
assert not routed, "字符串 action 不得进卡片处理器"
assert resp and resp[0][1] == 200, resp

# T4b：旧 schema 卡按钮（body 顶层 action.value）→ 正常路由
h, resp, routed = _fake_handler()
H._api_feishu_event(h, json.dumps({
    "action": {"value": {"tag": "subscribe", "kind": "intraday"}}}
).encode("utf-8"))
assert len(routed) == 1 and routed[0]["action"]["value"]["tag"] == "subscribe"

# T4c：v2 schema card.action.trigger → 正常路由
h, resp, routed = _fake_handler()
H._api_feishu_event(h, json.dumps({
    "header": {"event_type": "card.action.trigger"},
    "event": {"action": {"value": {"tag": "subscribe"}}, "operator": {}}
}).encode("utf-8"))
assert len(routed) == 1

# T4d：含 dict action 但无 value 键的外来事件 → 不误路由（收紧核心）
h, resp, routed = _fake_handler()
H._api_feishu_event(h, json.dumps({
    "event": {"action": {"type": "unknown", "form": []}}}).encode("utf-8"))
assert not routed and resp and resp[0][1] == 200, resp
print("T4 分发收紧 OK（字符串/无value action 不误路由；两种卡schema 正常路由）")

# ── T5（K-STOCK-407 F1）：时间戳新鲜度窗口防重放 ───────────
# 场景：攻击者截获一条完整合法回调（头+body 原样重发）——验签必过，
# 只有时间戳窗口能拦。用真 encrypt key + 正确签名构造各时间偏移。
import hashlib as _hl
import time as _tm

_EK407 = "test-encrypt-key-407"
os.environ["FEISHU_ENCRYPT_KEY"] = _EK407  # _feishu_encrypt_key 读 env


def _probe(body_dict, ts):
    """构造 (handler, resp)——ts 为时间戳字符串，签名按官方字节算法算。"""
    nc, raw = "nonce407", json.dumps(body_dict).encode("utf-8")
    sig = _hl.sha256((ts + nc + _EK407).encode("utf-8") + raw).hexdigest()
    resp, routed = [], []
    hh = types.SimpleNamespace(
        headers={"X-Lark-Request-Timestamp": ts, "X-Lark-Request-Nonce": nc,
                 "X-Lark-Signature": sig},
        _json=lambda obj, code=200: resp.append((obj, code)),
        _handle_card_action=lambda ev: routed.append(ev),
        _feishu_encrypt_key=lambda: _EK407,
        _feishu_unverified_alert=lambda: None,
        log_message=lambda *a, **k: None)
    return hh, resp, routed


_body407 = {"type": "url_verification", "challenge": "fresh407"}

# T5a：新鲜时间戳 + 正确签名 → 200 challenge 回显（不误伤真回调）
h, resp, _ = _probe(_body407, str(int(_tm.time())))
H._api_feishu_event(h, json.dumps(_body407).encode("utf-8"))
assert resp and resp[0][1] == 200 and resp[0][0].get("challenge") == "fresh407", resp

# T5b：1 小时前（合法签名的重放）→ 403
h, resp, _ = _probe(_body407, str(int(_tm.time()) - 3600))
H._api_feishu_event(h, json.dumps(_body407).encode("utf-8"))
assert resp and resp[0][1] == 403, resp

# T5c：未来 1 小时（时钟偏移攻击面同样封死）→ 403
h, resp, _ = _probe(_body407, str(int(_tm.time()) + 3600))
H._api_feishu_event(h, json.dumps(_body407).encode("utf-8"))
assert resp and resp[0][1] == 403, resp

# T5d：时间戳非数字（带匹配签名）→ 403
h, resp, _ = _probe(_body407, "not-a-number")
H._api_feishu_event(h, json.dumps(_body407).encode("utf-8"))
assert resp and resp[0][1] == 403, resp

# T5e：窗口边缘内（-290s，默认 300s 窗）→ 放行（容忍边界）
h, resp, _ = _probe(_body407, str(int(_tm.time()) - 290))
H._api_feishu_event(h, json.dumps(_body407).encode("utf-8"))
assert resp and resp[0][1] == 200, resp

del os.environ["FEISHU_ENCRYPT_KEY"]
# 恢复被桩替换的 subprocess.run（脚本式测试无 teardown，就地恢复，
# 防止后续测试文件继承假 run——fail-open 禁令的测试侧镜像）
api_misc.subprocess.run = _orig_subprocess_run
print("T5 防重放窗口 OK（新鲜200 / 过去403 / 未来403 / 非数字403 / 边界内200）")
print("ALL PASS")
