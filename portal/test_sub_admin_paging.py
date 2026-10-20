# -*- coding: utf-8 -*-
"""`/api/subscription/list` 分页接线 + 订阅运营面翻页（WO-FS-131 · P3.10-lite O-A）。

背景：store 的 ``list_subscribers`` 早在 P3.9-lite（N-A）就有 ``offset`` /
``has_more``，但调用方全部仍在单页取数——``portal_api`` 只解析 ``?limit=``，
``sub_admin.html`` 固定 ``?limit=200`` 且无翻页 UI，超过 200 个订阅者时运营面
永远看不到后半段，且页面无从判断「到底了」还是「被截断」。

本文件钉住四件事：
1. 端点 ``?offset=`` 接线：非法值一律归 0 且不报 4xx；超界是空页不是错误；
   ``next_offset`` 只追加、只由服务端给，既有五个键语义一个不动；
2. 键闸不被分页绕过：401 仍在读账本之前短路，响应零行数据；
3. 分页拼接与全量集合严格一致（无重、无漏、顺序不变）；
4. 页面三态（可能还有更多 / 已到末尾 / 无法继续翻页）、加载中防连点、
   0 行 + has_more 的矛盾信号显式停止、翻页中途 401 不清空已取到的行。

隔离纪律（P3.7-lite 判例）：不往 ``sys.modules`` 注桩，env ``SUBSCRIPTION_DB``
锚到 tmp 临时 sqlite，HTTP 探针只打 127.0.0.1:0 随机高位端口；不碰生产库、
不重启在役服务、不真发消息。模块级仅临时改 env，teardown 原样还原。
"""

import http.server
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL))

# 测试环境跳过启动预热（portal_api 模块级 daemon 线程，冷算约百秒）
os.environ.setdefault("PORTAL_NO_WARMUP", "1")

import portal_api  # noqa: E402
import subscription_store as ss  # noqa: E402

API = PORTAL / "portal_api.py"
PAGE = PORTAL / "static" / "sub_admin.html"

TOTAL = 250  # 超过单页上限 200，必须翻页才能取全
KEY = "adm-paging-key-3c7d"
STORE_KEYS = {"ok", "count", "subscribers", "offset", "has_more"}


def oid(i):
    return "ou_p%03d" % i


def _read(p):
    return p.read_text(encoding="utf-8")


def _body_text(body):
    return json.dumps(body, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 夹具：临时库（250 人）+ 随机端口探针
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base_url():
    srv = http.server.HTTPServer(("127.0.0.1", 0), portal_api.Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.shutdown()
        th.join(timeout=5)
        srv.server_close()


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """250 个订阅者；env 在模块内生效、teardown 原样还原（不外泄到别的模块）。"""
    p = tmp_path_factory.mktemp("sub_admin_paging") / "sub_admin_paging.sqlite3"
    old_db = os.environ.get("SUBSCRIPTION_DB")
    old_own = os.environ.pop("SUBSCRIPTION_OWN_DOMAIN", None)
    os.environ["SUBSCRIPTION_DB"] = str(p)
    try:
        ss._connect(create=True).close()
        for i in range(TOTAL):
            ss.upsert_subscriber(oid(i), "分页%03d" % i)
        ss.grant_subscription(oid(0), "basic_monthly", 30)
        yield p
    finally:
        if old_db is None:
            os.environ.pop("SUBSCRIPTION_DB", None)
        else:
            os.environ["SUBSCRIPTION_DB"] = old_db
        if old_own is not None:
            os.environ["SUBSCRIPTION_OWN_DOMAIN"] = old_own


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """默认两把钥匙都不配；只有显式 setenv 的用例才开闸。"""
    monkeypatch.delenv("PORTAL_SUB_ADMIN_KEY", raising=False)
    monkeypatch.delenv("PORTAL_SUB_STATUS_KEY", raising=False)


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 1. 缺省 offset：与旧行为逐字段一致（纯追加）
# ---------------------------------------------------------------------------

def test_01_default_offset_matches_legacy_payload(base_url, db):
    """不带 offset：行集与 store 默认调用逐字段一致，只在顶层追加新键。"""
    code, body = _get(base_url + "/api/subscription/list")
    assert code == 200, body
    base = ss.list_subscribers(limit=50)
    assert body["ok"] is True and body["count"] == 50 == len(body["subscribers"])
    assert body["offset"] == 0 and body["has_more"] is True
    assert body["subscribers"] == base["subscribers"]  # 行内字段零改动
    assert set(body) == STORE_KEYS | {"auth_mode", "next_offset"}
    for k in STORE_KEYS:  # 既有键逐个照抄 store，语义不变
        assert body[k] == base[k], k


# ---------------------------------------------------------------------------
# 2. offset=200 取余 50 / 超界 / 非法值
# ---------------------------------------------------------------------------

def test_02_offset_200_returns_tail_50(base_url, db):
    """limit=200&offset=200：250 人里取尾部 50 行，has_more=False。"""
    code, body = _get(base_url + "/api/subscription/list?limit=200&offset=200")
    assert code == 200, body
    assert body["ok"] is True and body["offset"] == 200
    assert body["count"] == 50 == len(body["subscribers"])
    assert [s["open_id"] for s in body["subscribers"]] == \
        [oid(i) for i in range(200, TOTAL)]
    assert body["has_more"] is False


@pytest.mark.parametrize("bad", ["abc", "3.7", "-50", "", "  ", "1e3"])
def test_03_illegal_offset_falls_back_to_zero_not_4xx(base_url, db, bad):
    """非数字 / 浮点串 / 负数 / 空串一律归 0，且不是 4xx（旧调用方零破坏）。"""
    code, body = _get(base_url + "/api/subscription/list?limit=20&offset=" + bad)
    assert code == 200, (bad, body)  # 关键：不得因为 offset 拼错就 400
    assert body["ok"] is True and body["offset"] == 0
    assert [s["open_id"] for s in body["subscribers"]] == \
        [oid(i) for i in range(20)]


@pytest.mark.parametrize("off", [TOTAL, 10 ** 6])
def test_04_offset_beyond_end_is_empty_ok_not_error(base_url, db, off):
    """offset 超界 → 空列表但 ok=True（空 ≠ 错误），has_more=False 且无下一页。"""
    code, body = _get(base_url + "/api/subscription/list?limit=200&offset=%d" % off)
    assert code == 200, body
    assert body["ok"] is True and "error" not in body
    assert body["count"] == 0 and body["subscribers"] == []
    assert body["offset"] == off and body["has_more"] is False
    assert body.get("next_offset") is None


# ---------------------------------------------------------------------------
# 3. has_more 与 next_offset 的配对口径
# ---------------------------------------------------------------------------

def test_05_next_offset_equals_offset_plus_count(base_url, db):
    """has_more 为真时 next_offset == 生效 offset + 本页行数（服务端算，不猜）。"""
    for q, want_off, want_n in (("?limit=200&offset=0", 200, 200),
                                ("?limit=100&offset=100", 200, 100),
                                ("?limit=50&offset=100", 150, 50)):
        code, body = _get(base_url + "/api/subscription/list" + q)
        assert code == 200, (q, body)
        assert body["has_more"] is True, q
        assert body["next_offset"] == want_off, q
        assert body["next_offset"] == body["offset"] + body["count"]
        assert body["count"] == want_n, q


def test_06_tail_page_has_no_next_offset(base_url, db):
    """末尾页 has_more=False → 不给 next_offset（缺键即「没有下一页」，不编造）。"""
    code, body = _get(base_url + "/api/subscription/list?limit=200&offset=200")
    assert code == 200, body
    assert body["has_more"] is False
    assert "next_offset" not in body
    assert body.get("next_offset") is None  # 与 None 同义，页面同一分支判定


# ---------------------------------------------------------------------------
# 4. 鉴权：未配 key 分页照常；错 key 401 零行数据；401 先于读账本
# ---------------------------------------------------------------------------

def test_07_gate_off_auth_mode_none_paging_works(base_url, db):
    """未配置 key：auth_mode=none，且分页取数完全正常（不因无闸而少页）。"""
    code, body = _get(base_url + "/api/subscription/list?limit=50&offset=100")
    assert code == 200, body
    assert body["auth_mode"] == "none"
    assert body["offset"] == 100 and body["count"] == 50
    assert body["subscribers"][0]["open_id"] == oid(100)
    assert body["next_offset"] == 150


def test_08_wrong_key_401_body_has_no_rows(base_url, db, monkeypatch):
    """错 key → 401，响应体既无行数据也无分页字段（分页不得绕过键闸）。"""
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list?limit=200&offset=0",
                      {"X-Portal-Key": "nope"})
    assert code == 401, (code, body)
    assert set(body) <= {"ok", "error"}
    text = _body_text(body)
    for needle in (oid(0), "分页000", "display_name", "subscribers", "plan_id",
                   "has_more", "next_offset", "offset"):
        assert needle not in text, "401 泄露数据/分页字段: %s" % needle


def test_09_401_short_circuits_before_store_on_paged_request(base_url, tmp_path,
                                                            monkeypatch):
    """账本缺失 + 错 key + 带 offset → 仍是 401（闸先于读账本）；对 key 才暴露错误。"""
    monkeypatch.setenv("SUBSCRIPTION_DB",
                       str(tmp_path / "no_such_store.sqlite3"))
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list?limit=200&offset=200",
                      {"X-Portal-Key": "bad"})
    assert code == 401, (code, body)
    assert body.get("error") == "unauthorized"
    assert "store_unavailable" not in _body_text(body)
    code2, body2 = _get(base_url + "/api/subscription/list?limit=200&offset=200",
                        {"X-Portal-Key": KEY})
    assert code2 == 200, body2
    assert body2 == {"ok": False, "error": "store_unavailable",
                     "auth_mode": "key"}  # 失败分支不追加 next_offset


def test_10_right_key_passes_with_auth_mode_key(base_url, db, monkeypatch):
    """对 key → 200，auth_mode=key，分页字段照给。"""
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list?limit=5&offset=5",
                      {"X-Portal-Key": KEY})
    assert code == 200, body
    assert body["auth_mode"] == "key"
    assert body["count"] == 5 and body["offset"] == 5
    assert body["subscribers"][0]["open_id"] == oid(5)
    assert body["next_offset"] == 10


# ---------------------------------------------------------------------------
# 5. 分页拼接 == 全量集合
# ---------------------------------------------------------------------------

def test_11_paging_walk_equals_full_set(base_url, db):
    """只按服务端 next_offset 走：无重、无漏、顺序与全量一致，且能走到底。"""
    collected, offset, pages = [], 0, 0
    while True:
        code, body = _get(
            base_url + "/api/subscription/list?limit=100&offset=%d" % offset)
        assert code == 200, body
        collected.extend(s["open_id"] for s in body["subscribers"])
        pages += 1
        assert pages <= 10, "翻页未收敛，疑死循环"
        nxt = body.get("next_offset")
        if not body["has_more"] or nxt is None:
            break
        assert nxt > offset, "next_offset 未前进"
        offset = nxt
    assert collected == [oid(i) for i in range(TOTAL)]  # 顺序 + 条数即覆盖全量
    assert len(set(collected)) == TOTAL  # 无重复
    assert [r["open_id"] for r in ss.iter_all_subscribers()["subscribers"]] \
        == collected  # 与 store 侧全量遍历同序同集


# ---------------------------------------------------------------------------
# 6. 页面：三态文案、防连点、矛盾信号、中途中止
# ---------------------------------------------------------------------------

def test_12_page_paging_ui_and_three_states():
    """页面首屏参数、三态文案、「已显示 N 条」、加载更多、只认服务端 offset。"""
    html = _read(PAGE)
    assert "limit='+PAGE_SIZE+'&offset='" in html, "首屏必须显式带 offset"
    assert "fetchPage(0)" in html, "首屏 offset 必须是 0"
    assert "已显示 " in html, "底部面板必须显示「已显示 N 条」"
    for state in ("可能还有更多", "已到末尾", "无法继续翻页"):
        assert state in html, "缺三态之一: %s" % state
    assert "无法继续翻页（服务端未给出 next_offset）" in html
    assert "加载更多" in html and 'id="load-more"' in html
    assert "加载中…" in html and ".disabled=true" in html.replace(" ", ""), \
        "加载中必须禁用按钮防连点"
    assert "next_offset" in html, "页面必须消费服务端 next_offset"
    # 不得自己猜 offset：页面里不得出现 offset 自增/自算。
    #
    # 断言口径（P3.8-lite 判例：禁的是能力，不是词）——加载提示文案里会拼
    # '正在取 offset='+reqOffset+' 的下一页'，那是把**服务端给的**起点回显给
    # 用户，不是自行推算，不能当违规。真正要禁的是「用页大小做加法得到下一页
    # 起点」这一类能力。
    for banned in ("offset+PAGE_SIZE", "offset+=PAGE_SIZE", "offset = offset +",
                   "offset+PAGE", "subState.next=reqOffset+"):
        assert banned not in html, "页面自行推算 offset: %s" % banned
    # 起点唯一来源：服务端 next_offset 解析出的 nxt
    assert "subState.next=nxt" in html, \
        "下一页起点只能来自服务端 next_offset（nxt），不得来自本地推算"
    # 反退 / 不前进的 next_offset 必须被拒绝（防死循环与重复页）
    assert "nxt<=reqOffset" in html, "nxt 反退或原地踏步时必须停下，不得继续请求"
    # 翻页不得清空已取到的行（renderSubList 从累计状态渲染，不吞旧行）
    assert "subState.rows.push(s)" in html
    assert "replaceChildren()" in html  # 表格重绘仍是整体重绘，但数据源是累计态


def test_13_page_zero_rows_with_has_more_stops_explicitly():
    """某页 0 行但 has_more 仍真 → 显式异常并停止（不得死循环、不得当成到底）。"""
    html = _read(PAGE)
    assert "!rows.length&&more" in html.replace(" ", ""), "缺 0 行+has_more 守护"
    assert "翻页口径异常" in html
    assert "两者矛盾" in html
    assert "已停止" in html
    assert "死循环" in html or "不在死循环" in html or "停止翻页" in html


def test_14_page_mid_paging_401_keeps_rows():
    """翻页中途 401：提示「鉴权在翻页过程中失效」并停止，但不清空已取到的行。"""
    html = _read(PAGE)
    assert "鉴权在翻页过程中失效" in html
    assert "原样保留" in html
    assert "不清空" in html
    # loadMore 内的 401 分支必须置 stopped，而不是回退到首屏空态
    block = html[html.index("async function loadMore"):]
    block = block[:block.index("/* 鉴权状态自述")]
    assert "errStatus===401" in block, "loadMore 必须单独识别 401"
    assert "subState.stopped=true" in block.replace(" ", "")


def test_15_page_401_branch_still_before_generic_error():
    """401 分支仍先于「账本不可用」通用分支（否则 401 会被渲染成账本故障）。"""
    html = _read(PAGE)
    assert "e.status=r.status" in html
    assert html.index("errStatus===401") < html.index("账本不可用（")
    for banned in ("localStorage", "sessionStorage", "document.cookie",
                   "headers:"):
        assert banned not in html, "页面不得具备密钥存储/携带能力: %s" % banned
    for quoted in ("'X-Portal-Key'", '"X-Portal-Key"'):
        assert quoted not in html


# ---------------------------------------------------------------------------
# 7. 端点静态锚：offset 解析、next_offset 只加在成功分支、无写函数
# ---------------------------------------------------------------------------

def test_16_branch_static_offset_before_store_and_append_only():
    """list 段：offset 解析先于取数、next_offset 只加在成功分支、零写函数。"""
    src = _read(API)
    m = src[src.index('path == "/api/subscription/list"'):]
    m = m[:m.index("\n        elif path == ")]
    assert "list_subscribers(limit=_limit" in m
    assert m.index("_raw_off") < m.index("list_subscribers(limit=_limit")
    assert m.index("hmac.compare_digest") < m.index("list_subscribers")
    # 追加语句（写 _view 的那一行）必须在取数之后
    assert m.index('_view["next_offset"]') > m.index(
        "list_subscribers(limit=_limit")
    # 失败分支的三键结构不得被污染：错误分支里不出现 next_offset。
    #
    # 锚点必须落在**实际语句**上，不能落在字符串字面量的首次出现处：该三键
    # 结构的字面量在上方分页注释里也出现过，按 index 取会从注释尾巴开始切，
    # 把成功分支的注释一并包进来 → 假红（P3.10-lite 收编判例：静态断言的
    # 切片必须锚在代码分支上，不能锚在可能被注释复述的文本上）。
    fail_start = m.index("if not isinstance(_res, dict)")
    fail_end = m.index("else:", fail_start)
    fail = m[fail_start:fail_end]
    assert "next_offset" not in fail, "失败分支不得追加分页键"
    assert '"error": "store_unavailable"' in fail, \
        "失败分支必须保留稳定错误结构（不泄露上游文本）"
    for banned in ("upsert_subscriber", "grant_subscription",
                   "expire_sweep", "entitlement_log"):
        assert banned not in m, "list 段不得出现写函数 %s" % banned
