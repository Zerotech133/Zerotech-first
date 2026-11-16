# -*- coding: utf-8 -*-
"""2026-11-16 CR-01/03/04 落地的回归测试。

- chanlun_queue_refresh：只扩覆盖不评级、幂等、最新轮口径
- akshare_news：纯函数（strip_em/norm_code/append_jsonl/jsonp 解析/降级检测）
- gap_scan._re_for：日期型 ts 可匹配（research 判例）
全部无网络。
"""
import json

import gap_scan as G
import akshare_news as A
import chanlun_queue_refresh as R


# ------------------------------------------------------------ gap_scan 正则
def test_re_for_matches_date_only_ts():
    m = G._re_for("ts").search('{"ts": "2024-06-24", "org": "x"}')
    assert m and m.group(1) == "2024-06-24"


def test_re_for_still_matches_full_ts():
    m = G._re_for("ts").search('{"ts": "2026-11-16 16:46:00"}')
    assert m and m.group(1) == "2026-11-16 16:46:00"


# ------------------------------------------------------------ 刷新工具
def _mk_pool(latest="2026-11-13"):
    return [
        {"code": "600519.SH", "name": "A", "type": "1买", "dt": "2026-11-01",
         "detect_date": latest, "first_seen": latest + "T23:00:00Z"},
        {"code": "000001.SZ", "name": "B", "type": "2卖", "dt": "2026-11-02",
         "detect_date": latest, "first_seen": latest + "T23:00:00Z"},   # 卖点不入
        {"code": "000002.SZ", "name": "C", "type": "3买", "dt": "2026-11-05",
         "detect_date": "2026-11-06", "first_seen": "2026-11-06T23:00:00Z"},  # 旧轮不入
    ]


def _mk_queue():
    return {"asof": "2026-11-06", "total": 1,
            "all": [{"code": "600519.SH", "name": "A", "grade": "watch",
                     "type": "1买", "stage": "new", "sig_dt": "2026-11-01"}],
            "actionable": [{"code": "600519.SH", "grade": "watch"}]}


def test_build_refresh_appends_latest_round_buy_only():
    latest, by_code = R.load_pool_buy_rounds.__wrapped__(_mk_pool()) \
        if hasattr(R.load_pool_buy_rounds, "__wrapped__") else (None, None)
    # 直接用内存池：绕过文件读取，验证 build_refresh 纯函数
    pool = _mk_pool()
    latest = max(r["detect_date"] for r in pool)
    by_code = {}
    for r in pool:
        if "买" not in r["type"]:
            continue
        c = r["code"]
        old = by_code.get(c)
        if old is None or r["detect_date"] > old["detect_date"]:
            by_code[c] = r
    new_q, st = R.build_refresh(_mk_queue(), latest, by_code)
    assert st["n_added"] == 0          # 池内最新轮买点只有 600519，已在队列
    assert st["n_after"] == st["n_before"]
    codes = {e["code"] for e in new_q["all"]}
    assert "000001.SZ" not in codes    # 卖点永不入
    assert "000002.SZ" not in codes    # 旧轮不入


def test_build_refresh_keeps_actionable_and_grades():
    pool = _mk_pool() + [{"code": "300001.SZ", "name": "D", "type": "2买",
                          "dt": "2026-11-10", "detect_date": "2026-11-13",
                          "first_seen": "2026-11-13T23:00:00Z"}]
    latest = "2026-11-13"
    by_code = {r["code"]: r for r in pool if "买" in r["type"]}
    new_q, st = R.build_refresh(_mk_queue(), latest, by_code)
    assert st["n_added"] == 1
    e = new_q["all"][-1]
    assert e["grade"] == "pool" and e["stage"] == "pool_scan"   # 显式未评级
    assert new_q["actionable"] == _mk_queue()["actionable"]     # actionable 原封
    assert new_q["refresh_note"]                                # 判据写进文件


def test_build_refresh_idempotent():
    pool = _mk_pool() + [{"code": "300001.SZ", "name": "D", "type": "2买",
                          "dt": "2026-11-10", "detect_date": "2026-11-13"}]
    by_code = {r["code"]: r for r in pool if "买" in r["type"]}
    new_q, st1 = R.build_refresh(_mk_queue(), "2026-11-13", by_code)
    # 把 new_q 当输入再跑一遍 → 不再新增
    new_q2, st2 = R.build_refresh(new_q, "2026-11-13", by_code)
    assert st2["n_added"] == 0 and st2["n_after"] == st1["n_after"]


def test_load_pool_buy_rounds_reads_file(tmp_path):
    f = tmp_path / "pool.jsonl"
    with open(f, "w", encoding="utf-8") as fh:
        for r in _mk_pool():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    latest, by_code = R.load_pool_buy_rounds(str(f))
    assert latest == "2026-11-13"
    # load_pool_buy_rounds 收全历史买点（按 code 取最新一条），
    # 「只并最新轮」的过滤在 build_refresh 里做（上面的用例已锁）
    assert set(by_code) == {"600519.SH", "000002.SZ"}


# ------------------------------------------------------------ akshare_news 纯函数
def test_strip_em():
    assert A.strip_em("<em>达利</em>食品") == "达利食品"
    assert A.strip_em("") == ""


def test_norm_code():
    assert A.norm_code("002737") == "002737"
    assert A.norm_code(" 002737\r") == "002737"       # CRLF 判例
    assert A.norm_code("60051") == ""                 # 5 位拒
    assert A.norm_code("600519.SZ") == ""             # 带后缀拒（入口侧）


def test_append_jsonl_dedup_and_corrupt_line(tmp_path):
    f = tmp_path / "600519.jsonl"
    rows = [{"url": "u1", "ts": "2026-11-16 10:00:00"},
            {"url": "u2", "ts": "2026-11-16 11:00:00"}]
    assert A.append_jsonl(str(f), rows, ["url"]) == 2
    assert A.append_jsonl(str(f), rows, ["url"]) == 0     # 幂等
    f.write_text("{broken\n" + f.read_text(encoding="utf-8"), encoding="utf-8")
    rows2 = [{"url": "u3", "ts": "2026-11-16 12:00:00"}]
    assert A.append_jsonl(str(f), rows2, ["url"]) == 1    # 坏行不清文件


def test_fetch_em_news_flags_degraded_response(monkeypatch):
    """反爬降级（result 只有 passportWeb）必须报错，不能当无新闻落盘。"""
    class FakeResp:
        text = 'jQuery1({"bizCode":"0","result":{"passportWeb":[{"uid":"x"}]}})'

    class FakeHttp:
        @staticmethod
        def curl_get(*a, **k):
            return FakeResp()

    monkeypatch.setattr(A, "_em_http", lambda: FakeHttp())
    try:
        A.fetch_em_news("002737")
        assert False, "降级响应未报错"
    except RuntimeError as e:
        assert "降级" in str(e)


def test_fetch_em_news_parses_articles(monkeypatch):
    arts = [{"date": "2026-11-16 10:00:00", "title": "<em>葵花</em>药业",
             "content": "c<em>x</em>", "mediaName": "证券时报", "code": "A1"}]
    payload = json.dumps({"result": {"cmsArticleWebOld": arts}},
                         ensure_ascii=False)

    class FakeResp:
        text = "jQuery1(" + payload + ")"

    class FakeHttp:
        @staticmethod
        def curl_get(*a, **k):
            return FakeResp()

    monkeypatch.setattr(A, "_em_http", lambda: FakeHttp())
    rows = A.fetch_em_news("002737")
    assert len(rows) == 1
    assert rows[0]["title"] == "葵花药业" and "<em>" not in rows[0]["content"]
    assert rows[0]["url"].endswith("A1.html")
