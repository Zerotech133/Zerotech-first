"""JEV 公告范围治理「一周影子抽样」观测（RFC-20261107 §8 用例 3/7）。

纯只读：不写 news.db、不调用 Ollama、不抢 ollama_batch.lock、不改任何生产配置。
每次运行产出一份 JSON 快照；与最近一次快照相减即为当日增量。

用法：
    python C:/aios/tools/jev_scope_observe.py
    python C:/aios/tools/jev_scope_observe.py --out C:/Temp/xxx.json

影子一致率说明（重要，勿误解）：
    本脚本的「一致率」是**离线代理指标** —— 用近 30 日「命中同一批新模板、
    且已由 9B 真实判定」的历史样本 neutral 率，作为今日 prefilter skip 的
    预期正确率。它不是 LLM 复判一致率。若要做真实影子复判（抽 N 条重新
    调 judge 对比），须显式单独批准，因为那会占用 Ollama 单槽与锁。
"""
import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))

DB = r"C:/aios/news_collector/news.db"
OUT_DIR = r"C:/aios/jev-eval/review_data"
LOW = ("xd_notice", "announcement")
NOTICE_REASON = "例行公告"
LEGACY_REASON = "例行/行情"
HIST_DAYS = 30


def _q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _latest_snapshot(before):
    files = sorted(glob.glob(os.path.join(OUT_DIR, "jev_scope_observe_*.json")))
    files = [f for f in files if f != before]
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8") as f:
            return json.load(f), os.path.basename(files[-1])
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="报告输出路径（默认按日期落 review_data）")
    args = ap.parse_args(argv)

    today = datetime.datetime.now().strftime("%Y%m%d")
    out = args.out or os.path.join(OUT_DIR, "jev_scope_observe_%s.json" % today)

    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=15)
    left_total = _q(con, "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''")[0][0]
    left_high = _q(
        con,
        "SELECT COUNT(*) FROM news WHERE (jev IS NULL OR jev='') "
        "AND (source IS NULL OR source NOT IN (?,?))", LOW,
    )[0][0]
    left_low = _q(
        con,
        "SELECT COUNT(*) FROM news WHERE (jev IS NULL OR jev='') "
        "AND source IN (?,?)", LOW,
    )[0][0]

    # prefilter 落库按 reason 分组（新模板 reason=例行公告 是「新代码已生效」的直接证据）
    reason_rows = _q(
        con,
        "SELECT json_extract(jev,'$.pref.reason') r, COUNT(*) c "
        "FROM news WHERE json_extract(jev,'$.src')='prefilter' GROUP BY r",
    )
    reasons = {str(r): int(c) for r, c in reason_rows}
    notice_skip = reasons.get(NOTICE_REASON, 0)
    legacy_skip = reasons.get(LEGACY_REASON, 0)
    pref_total = sum(reasons.values())

    # 当日 9B 真实判定量（judged_at 只有 LLM 判定会写）
    judged_today = _q(
        con,
        "SELECT COUNT(*) FROM news WHERE substr(judged_at,1,10)=?",
        (datetime.datetime.now().strftime("%Y-%m-%d"),),
    )[0][0]

    # 当日入库量（按 created_at）
    inserted_today = _q(
        con,
        "SELECT COUNT(*) FROM news WHERE substr(created_at,1,10)=?",
        (datetime.datetime.now().strftime("%Y-%m-%d"),),
    )[0][0]

    # 历史同模板 9B neutral 率（离线影子基线）
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=HIST_DAYS)).strftime("%Y-%m-%d")
    hist = _q(
        con,
        "SELECT title, source, jev FROM news "
        "WHERE source IN (?,?) AND jev IS NOT NULL AND jev<>'' "
        "AND json_extract(jev,'$.src')!='prefilter' AND ts>=?",
        LOW + (cutoff,),
    )
    try:
        import jev_prefilter as pf
    except Exception as e:
        print("import jev_prefilter failed: %r" % e)
        con.close()
        return 1

    os.environ.pop(pf.NOTICE_ENV, None)  # 以生产默认（开启）口径复算
    hit_n = hit_neutral = 0
    for title, source, jev in hist:
        r = pf.decide(title or "", source or "", mode="safe")
        if r["decision"] == "skip" and r["reason"] == NOTICE_REASON:
            hit_n += 1
            try:
                if (json.loads(jev).get("bias") or "neutral") == "neutral":
                    hit_neutral += 1
            except Exception:
                pass
    hist_rate = round(hit_neutral / hit_n, 5) if hit_n else None
    con.close()

    prev = _latest_snapshot(out)
    delta = {}
    if prev:
        p, pname = prev
        delta = {
            "baseline_file": pname,
            "days_since": None,
            "left_total_delta": left_total - int(p.get("left_total", left_total)),
            "left_high_delta": left_high - int(p.get("left_high", left_high)),
            "notice_skip_delta": notice_skip - int(p.get("notice_skip", notice_skip)),
            "legacy_skip_delta": legacy_skip - int(p.get("legacy_skip", legacy_skip)),
            "pref_total_delta": pref_total - int(p.get("pref_total", pref_total)),
        }

    report = {
        "ts": _now(),
        "left_total": left_total,
        "left_high": left_high,
        "left_low": left_low,
        "pref_total": pref_total,
        "notice_skip": notice_skip,
        "legacy_skip": legacy_skip,
        "prefilter_reasons": reasons,
        "judged_today_9b": judged_today,
        "inserted_today": inserted_today,
        "new_code_effective_in_prod": notice_skip > 0,
        "shadow_baseline": {
            "kind": "offline_proxy_not_llm_replay",
            "hist_days": HIST_DAYS,
            "hist_hit_n": hit_n,
            "hist_neutral_n": hit_neutral,
            "hist_neutral_rate": hist_rate,
            "expected_false_skip_today": (
                round(delta["notice_skip_delta"] * (1 - hist_rate), 2)
                if delta and hist_rate is not None else None
            ),
        },
        "delta_vs_last": delta or None,
        "scope_definition": {
            "low_priority_sources": list(LOW),
            "high_left_sql": "source NOT IN (xd_notice, announcement)",
        },
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("written", out)
    print("left_total=%d left_high=%d left_low=%d" % (left_total, left_high, left_low))
    print("notice_skip=%d legacy_skip=%d new_code_effective=%s"
          % (notice_skip, legacy_skip, report["new_code_effective_in_prod"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
