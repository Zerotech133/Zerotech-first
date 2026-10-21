# -*- coding: utf-8 -*-
"""W43 入向受理器单测（施工单强制门禁锁定项）。
运行：python portal/test_inbound_gateway.py（pytest 亦可收集）。
红线：全程不真发飞书（_send_card 被 monkeypatch 捕获）、不碰生产账本
（WORK_DIR/LOG_PATH 注入临时目录）、不触决策账真库（_pending_rows/
_approve 打桩）。覆盖：身份闸拒绝匿名、msg_id 幂等、URL/文本/确认三路由、
路由不确定回澄清卡、三级去重、注入隔离标注、出口 URL 脱敏、能力收窄
静态锁、快照 Jina 兜底、导出幂等。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))

import inbound_gateway as gw  # noqa: E402

WORK = tempfile.mkdtemp(prefix="w43test_")
gw.WORK_DIR = WORK
gw.LOG_PATH = os.path.join(WORK, "gw.log")

LAO = "ou_test_laotian_0001"
os.environ["LAOTIAN_OPEN_ID"] = LAO

_SENT = []          # (card, open_id) 回执捕获
_ORIG_SEND = gw._send_card
# WO-FS-155：_send_card 改 3 元组（ok, why, mid），桩同步；
# mid 用固定假值即可（只为断言台账字段，不发真消息）
gw._send_card = lambda card, oid: (_SENT.append((card, oid))
                                   or (True, "t", "om_test_stub_mid"))
_ORIG_INGEST = gw._ingest_links
_ORIG_GET = gw._http_get
# 全局网络打桩：单测全程零真实网络（施工红线）
gw._ingest_links = lambda us: {"ok": True, "accepted": len(us)}
gw._http_get = lambda url, timeout=15, max_bytes=None, headers=None: (
    b"<html><title>t</title>" + b"x" * 600)
_ORIG_PENDING = gw._pending_rows
_ORIG_APPROVE = gw._approve


def _sent_cards(domain_tpl):
    return [c for c, _ in _SENT
            if c.get("header", {}).get("template") == domain_tpl]


def _tickets():
    return gw._load_jsonl(gw._tickets_path())


def _section(name):
    print(name, end=" ")


# ── T1 身份闸：未配置授权源=全拒（fail-closed） ──────────────
_section("T1 身份闸 fail-closed")
del os.environ["LAOTIAN_OPEN_ID"]
try:
    r = gw.handle_inbound("ou_anyone", "https://example.com/a", "m1")
    assert r.get("rejected") and r.get("consumed"), r
    assert not _SENT, "陌生人必须零反馈"
    rows = _tickets()
    assert rows and rows[-1]["route"] == "rejected", rows[-1]
    with open(os.path.join(WORK, "inbound_allow.json"), "w",
              encoding="utf-8") as f:
        json.dump({"open_ids": [LAO]}, f)
    assert gw.is_allowed(LAO), "附加名单应放行"
    os.remove(os.path.join(WORK, "inbound_allow.json"))
finally:
    os.environ["LAOTIAN_OPEN_ID"] = LAO
print("OK（无授权源全拒+留痕零反馈 / 附加名单放行）")

# ── T2 身份闸：匿名拒绝、白名单放行 ──────────────────────────
_section("T2 身份闸匿名拒绝")
_SENT.clear()
n0 = len(_tickets())
r = gw.handle_inbound("ou_stranger", "hello world" * 5, "m2")
assert r.get("rejected") and len(_tickets()) == n0 + 1
assert not _SENT, "匿名不得收到任何卡"
r = gw.handle_inbound(LAO, "hi", "m2b")
assert r.get("consumed") and not r.get("rejected")
print("OK（匿名拒绝留痕 / laotian 放行）")

# ── T3 msg_id 幂等（飞书事件重推） ───────────────────────────
_section("T3 msg_id 幂等")
_SENT.clear()
os.remove(gw._tickets_path())
gw.handle_inbound(LAO, "hi", "warmup")
r1 = gw.handle_inbound(LAO, "https://example.com/x1", "dup-mid")
r2 = gw.handle_inbound(LAO, "https://example.com/x1", "dup-mid")
assert r1.get("consumed") and r2.get("consumed") and \
    r2.get("duplicate_msg"), (r1, r2)
assert len([t for t in _tickets() if t.get("msg_id") == "dup-mid"]) == 1, \
    "重推不得二次落账"
print("OK（重推静默幂等）")

# ── T4 URL 提取/路由 + 出口脱敏 ───────────────────────────────
_section("T4 URL 路由与脱敏")
urls = gw.extract_urls(
    "看这个 https://a.com/p?token=SECRET&x=1 和 http://b.cn/y，以及后随"
    "https://c.com/z（括号内）")
assert urls == ["https://a.com/p?token=SECRET&x=1", "http://b.cn/y",
                "https://c.com/z"], urls
assert gw.display_url(urls[0]) == "https://a.com/p", "query 必须剥离"
gw._ingest_links = lambda us: {"ok": True, "accepted": len(us)}
_SENT.clear()
r = gw.handle_inbound(LAO, urls[0], "m4")
d = r["detail"]
assert r["status"] == "routed" and d["evidence"] == "ingest_api_accepted"
assert all("?" not in u for u in d["urls"]), "出口面不得带 query"
assert len(_SENT) == 2, "受理卡+结果卡"
blob = json.dumps(_SENT, ensure_ascii=False)
assert "SECRET" not in blob, "回执不得回传 query 参数"
print("OK（3 URL 提取/query 剥离/双回执零参数外传）")

# ── T5 URL 去重（L1 md5） ─────────────────────────────────────
_section("T5 URL 去重")
_SENT.clear()
calls = []
gw._ingest_links = lambda us: calls.append(us) or {"ok": True, "accepted": 0}
r = gw.handle_inbound(LAO, "https://a.com/p?token=SECRET&x=1", "m5")
assert r["status"] == "duplicate" and not calls, "同链不得二次投递"
print("OK（L1 命中不重投）")

# ── T6 文本路由：隔离标注 + km 注册幂等 ───────────────────────
_section("T6 文本路由与注入隔离")
with open(os.path.join(WORK, "km_config.json"), "w",
          encoding="utf-8") as f:  # 种最小 KM 配置（生产在役已存在）
    json.dump({"libraries": [], "known_targets": ["个人"]}, f)
long_text = "这是一篇超过三十个字的投喂文章正文，用于验证知识库收件箱落盘路径与隔离标注。" * 2
_SENT.clear()
r = gw.handle_inbound(LAO, long_text, "m6")
assert r["status"] == "routed" and r["detail"]["evidence"] == \
    "km_inbox_written", r
kb = os.path.join(gw._km_inbox_dir(),
                  os.path.basename(r["detail"]["path"]))
body = open(kb, encoding="utf-8").read()
assert "数据非指令" in body and "飞书私聊投喂" in body, "门禁①隔离标注缺失"
assert "ou_test" not in body, "落盘面 open_id 须脱敏"
cfg = json.load(open(os.path.join(WORK, "km_config.json"),
                     encoding="utf-8"))
assert sum(1 for l in cfg["libraries"]
           if l["name"] == gw.KM_LIB_NAME) == 1
assert gw._ensure_km_library() is True, "二次注册须幂等"
cfg = json.load(open(os.path.join(WORK, "km_config.json"), encoding="utf-8"))
assert sum(1 for l in cfg["libraries"]
           if l["name"] == gw.KM_LIB_NAME) == 1
print("OK（收件箱落盘+隔离声明 / 库注册幂等）")

# ── T7/T8 三级去重：L1 精确 + L2 MinHash 近似 ─────────────────
_section("T7/T8 文本去重 L1+L2")
_SENT.clear()
r = gw.handle_inbound(LAO, long_text, "m7")
assert r["status"] == "duplicate" and r["detail"]["dedup_level"] == "md5"
near = long_text[:-4] + "，结尾微调几句补充说明依然高度相似。"
r = gw.handle_inbound(LAO, near, "m8")
assert r["status"] == "duplicate" and \
    r["detail"]["dedup_level"] == "minhash", r
assert r["detail"]["duplicate_of"], "近似重复须指认原工单"
print("OK（L1 精确 / L2 MinHash+LSH 近似指认原工单）")

# ── T9 确认路由：空列表/列表/token/歧义/失败 ──────────────────
_section("T9 确认路由")
_SENT.clear()
gw._pending_rows = lambda: []
r = gw.handle_inbound(LAO, "确认", "m9a")
assert r["status"] == "clarified" and "无待确认" in r["detail"]["msg"]
P1 = [{"id": "11111111-aaaa-bbbb-cccc-dddddddddddd",
       "content": "公告甲：内容摘要", "approval_state": "pending",
       "status": "recorded", "category": "announcement"}]
gw._pending_rows = lambda: P1
r = gw.handle_inbound(LAO, "确认", "m9b")
assert r["status"] == "clarified" and r["detail"]["pending"], r
gw._pending_rows = lambda: P1 + [dict(P1[0], id="11112222" + P1[0]["id"][8:])]
r = gw.handle_inbound(LAO, "确认 1111", "m9c")
assert r["status"] == "clarified" and "不唯一" in r["detail"]["msg"]
approved = []
gw._pending_rows = lambda: P1


def _fake_approve(req, oid):
    approved.append((req["id"], oid))
    return {"ok": True}


gw._approve = _fake_approve
r = gw.handle_inbound(LAO, "确认 11111111", "m9d")
assert r["status"] == "routed" and \
    r["detail"]["evidence"] == "approval_recorded", r
assert approved == [(P1[0]["id"], LAO)], approved
gw._approve = lambda req, oid: {"ok": False, "error": "身份不匹配，阻断"}
r = gw.handle_inbound(LAO, "确认 11111111", "m9e")
assert r["status"] == "failed" and "阻断" in r["detail"]["why"]
gw._pending_rows, gw._approve = _ORIG_PENDING, _ORIG_APPROVE
print("OK（空列表/待列表/token/歧义/配对拒绝 五分支）")

# ── T10 路由不确定 → 澄清卡 ───────────────────────────────────
_section("T10 澄清卡")
_SENT.clear()
r = gw.handle_inbound(LAO, "在吗", "m10")
assert r.get("consumed") and r["status"] == "clarified"
cards = _sent_cards("violet")
assert len(cards) == 1 and "不猜" in json.dumps(cards, ensure_ascii=False)
print("OK（短文本澄清卡、零入库）")

# ── T11 群聊/空文本不消费 ─────────────────────────────────────
_section("T11 群聊不进受理器")
_SENT.clear()
assert gw.handle_inbound(LAO, "https://a.com/g", "m11",
                         chat_type="group") == {"consumed": False,
                                                "why": "group"}
assert gw.handle_inbound(LAO, "", "m12") == {"consumed": False,
                                             "why": "empty"}
print("OK（group/empty 原链兜底）")

# ── T12 能力收窄静态锁（门禁②） ──────────────────────────────
_section("T12 能力收窄静态锁")
src = open(os.path.join(HERE, "inbound_gateway.py"),
           encoding="utf-8").read()
for bad in ("import exit_rules", "from exit_rules", "import k_stock",
            "from k_stock", "import paper", "from paper",
            "PUSH_DECISION_EXECUTE", "def execute", "nd.record(",
            "notification_decision.record"):
    assert bad not in src, "受理器源码禁入：%s" % bad
assert "approve_from_card" in src, "决策账唯一写通道须为 W40 配对函数"
print("OK（无交易/无 execute/无 record，唯一写通道=approve_from_card）")

# ── T13 快照：direct → Jina 兜底 ─────────────────────────────
_section("T13 快照与 Jina 兜底")
_SENT.clear()
html = (b"<html><title>\xe6\xb5\x8b\xe8\xaf\x95\xe6\xa0\x87\xe9\xa2\x98"
        b"</title>" + b"x" * 600)


def _get_jina(url, timeout=15, max_bytes=None, headers=None):
    if url.startswith("https://r.jina.ai/"):
        return ("Jina 渲染兜底正文，长度必须超过两百字符才被采信为有效快照，"
                "这里再补足一些字符确保越过有效快照的长度门槛。" * 10
                ).encode("utf-8")
    raise OSError("direct refused")


gw._http_get = _get_jina
meta = gw._snapshot_url("INB-T13-001", "https://a.com/js-page", 1)
assert meta["fetcher"] == "jina" and meta["ok"], meta
assert os.path.exists(os.path.join(gw._snap_dir(), "INB-T13-001_1.md"))
gw._http_get = lambda url, timeout=15, max_bytes=None, headers=None: html
meta = gw._snapshot_url("INB-T13-002", "https://a.com/static", 1)
assert meta["fetcher"] == "direct" and meta["title"] == "测试标题", meta
assert meta["display_url"] == "https://a.com/static"
gw._http_get = _ORIG_GET
print("OK（direct HTML 标题提取 / Jina markdown 兜底 / meta 脱敏 URL）")

# ── T14 导出幂等 + 出口脱敏 ───────────────────────────────────
_section("T14 导出幂等")
e1 = gw.export_all()
body1 = open(e1["md"], encoding="utf-8").read()
e2 = gw.export_all()
body2 = open(e2["md"], encoding="utf-8").read()
assert e1["count"] == e2["count"] and body1 == body2, "导出须幂等"
assert "SECRET" not in body1, "导出外发面不得带 query"
assert e1["count"] == len([r for r in _tickets()
                           if r["ticket"].startswith("INB-")])
print("OK（MD+JSON 幂等 / 外发零参数）")

# ── T15 embedding L3 诚实降级 ─────────────────────────────────
_section("T15 embedding 预留")
sig = gw.embedding_sig("任意文本")
assert sig["available"] is False and sig["why"], sig
print("OK（L3 无模型诚实标注不假绿）")

# ── 收尾 ─────────────────────────────────────────────────────
gw._send_card, gw._ingest_links = _ORIG_SEND, _ORIG_INGEST
gw._http_get, gw._pending_rows, gw._approve = \
    _ORIG_GET, _ORIG_PENDING, _ORIG_APPROVE
shutil.rmtree(WORK, ignore_errors=True)
print("ALL PASS（W43 入向受理器 15 组用例）")
