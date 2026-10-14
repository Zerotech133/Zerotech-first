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
print("ALL PASS")
