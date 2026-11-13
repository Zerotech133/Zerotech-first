# -*- coding: utf-8 -*-
"""契约：jev_feed 的 LLM 判读必须落 judged_at（2026-11-14）。

背景：feed 的 LLM 回写原为 `UPDATE news SET jev=?`，不写 judged_at、不写 model。
后果是每晚 500 条真判在 judged_at 上不可见 —— 全库 jev 非空 113,554 行里
judged_at 为空，按 judged_at 统计产能会系统性少算 500/夜（11-13 那张逐日
平衡表第一版就是这么算错的）。

本组用例锁两件事：
1. LLM 路径必须写 judged_at，且格式为 'YYYY-MM-DD HH:MM:SS'（与 backfill 一致）；
2. 规则粗筛路径（src=prefilter）**不得**写 judged_at —— 它不是 LLM 判的，
   与公告豁免同一口径：不把「非 LLM 判定」记成「LLM 真判过」。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jev_feed as jf

JEV_OK = {"bias": "bull", "bias_cn": "利多", "conf": 0.7, "companies": []}


def _tmp_db(tmp_path):
    p = tmp_path / "news.db"
    db = sqlite3.connect(str(p))
    db.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, "
        "content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER, "
        "created_at TEXT, stocks TEXT, jev TEXT, tag_conf TEXT, "
        "model TEXT, judged_at TEXT, attempt INTEGER, last_error TEXT)"
    )
    rows = [
        ("n1", "xd_news", "2026-11-14 09:30", "某公司中标公告", "[]"),
        ("n2", "xd_news", "2026-11-14 09:31", "行业景气回升", "[]"),
        ("n3", "xd_news", "2026-11-14 09:32", "例行披露", "[]"),
    ]
    db.executemany(
        "INSERT INTO news(id,source,ts,title,stocks,jev) VALUES (?,?,?,?,?,NULL)", rows
    )
    db.commit()
    db.close()
    return str(p)


@pytest.fixture
def env(tmp_path, monkeypatch):
    jf.DB = _tmp_db(tmp_path)
    # 绕开时间窗守卫（本用例只验回写字段，不验窗口）
    monkeypatch.setattr(jf, "_in_night_window", lambda: False)
    monkeypatch.setattr(jf, "_in_day_guard", lambda: False)
    monkeypatch.delenv("JEV_PREFILTER", raising=False)
    # 本用例的源是 xd_news，不在公告豁免名单里；不触碰豁免环境变量。
    yield jf.DB


def _fetch(db_path, sql, args=()):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(sql, args).fetchall()
    finally:
        db.close()


def test_llm_path_stamps_judged_at(env, monkeypatch):
    def fake(items):
        return {it["id"]: dict(JEV_OK) for it in items}

    monkeypatch.setattr(jf.news_ai, "jev_judge_batch", fake)
    res = jf.judge_pending("news", 10)
    assert res["judged"] == 3

    rows = _fetch(env, "SELECT id, jev, judged_at, model FROM news ORDER BY id")
    for r in rows:
        assert r["jev"], "jev 必须写入"
        assert r["judged_at"], "LLM 判读必须落 judged_at"
        # 格式：YYYY-MM-DD HH:MM:SS（与 jev_backfill 同口径，空格分隔）
        assert len(r["judged_at"]) == 19 and r["judged_at"][10] == " "
        assert r["model"] is None, "feed 拿不到模型名，不得编造"


def test_prefilter_path_does_not_stamp(env, monkeypatch):
    """规则粗筛写 neutral 但不写 judged_at（非 LLM 判定）。"""
    monkeypatch.setenv("JEV_PREFILTER", "safe")
    db = sqlite3.connect(env)
    db.execute(
        "UPDATE news SET title=? WHERE id=?", ("投资者关系活动记录表", "n1")
    )
    db.commit()
    db.close()

    def fake(items):  # 只有非粗筛行会走到这里
        return {it["id"]: dict(JEV_OK) for it in items}

    monkeypatch.setattr(jf.news_ai, "jev_judge_batch", fake)
    jf.judge_pending("news", 10)

    rows = _fetch(env, "SELECT id, jev, judged_at FROM news ORDER BY id")
    by_id = {r["id"]: r for r in rows}
    d = json.loads(by_id["n1"]["jev"])
    if d.get("src") == "prefilter":
        assert by_id["n1"]["judged_at"] is None, "粗筛行不得冒充 LLM 真判"
    else:  # 该标题没被规则命中，走 LLM
        assert by_id["n1"]["judged_at"]
    # 其余行必然走 LLM，必须有时间戳
    assert by_id["n2"]["judged_at"] and by_id["n3"]["judged_at"]
