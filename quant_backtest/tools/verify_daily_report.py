# -*- coding: utf-8 -*-
"""XueqiuDailyReport 产物自动验收（2026-10-18）。

背景：19:00 Windows 计划任务 XueqiuDailyReport 产出雪球情报日报
（latest.json + daily/xq_intel_YYYYMMDD.json + .md），此前失败只能人工发现。
本脚本由计划任务 XueqiuReportVerify 每日 19:05 调用，做机器验收。

语义（fail-visible，逐条带原因）：
  - 15 个顶层键齐全（实测自 2020-05-13 产物与 latest.json）；
  - extractor_version == xq_classify._TICKER_VERSION（动态抓取，兜底 "v1.3"）；
  - pipeline 含 llm_model="qwen3.5:9b"（动态 xq_classify.MODEL）与非空 prompt_version；
  - word_tempo 含 bearish/bullish 两列（list）；
  - content_type 为 dict；posts_today>0 且为空 dict = 异常（18:00 爬虫后应非空）；
  - 容器键类型抽查（sentiment/event_type/top_codes/top_themes dict，
    anomaly_signals/high_confidence list）；
  - 新鲜度：latest.json mtime 在今天 19:00 之后（验收 19:05 跑）。

退出码：
  0 = 全部通过（追加一行 ASCII 到 verify_status.log）；
  1 = 任一失败（告警全文覆盖式落 verify_alert.log，含时间戳与逐项原因）；
  2 = latest.json 尚不存在（报告未生成，极早期态，不算 crash，不告警）。

--smoke：只验链路（脚本能跑、latest.json 存在且 JSON 可读），
不判 19:00 新鲜度与内容语义（供注册后冒烟——冒烟时日报还没跑）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

LATEST_JSON = r"C:\aios\automation_runs\xq_report\latest.json"
STATUS_LOG = r"C:\aios\automation_runs\xq_report\verify_status.log"
ALERT_LOG = r"C:\aios\automation_runs\xq_report\verify_alert.log"
# 采集侧降级事实（xueqiu_crawl.py 写）：hot 是新帖唯一来源，hot 失败时
# 日报文件可以照常「新鲜」，但内容必然为空（2026-10-19~25 断更 6 天教训）。
MANIFEST_JSON = r"C:\项目\老田的AI助手\quant_backtest\data\xueqiu\_manifest.json"
# 重启后任务补跑时 verify 可能先于 report 完成（2026-10-24 20:23 verify、
# 20:48 report 才写完 → 假失败）。等待上限 30 分钟，每 30 秒重看一次。
FRESHNESS_WAIT_SECONDS = 1800

# 15 键（实测 2026-10-18 latest.json 与 daily/xq_intel_20200513.json 完全一致）
REQUIRED_KEYS = [
    "anomaly_signals",
    "classified_today",
    "content_type",
    "date",
    "event_type",
    "extractor_version",
    "generated_at",
    "high_confidence",
    "ingested_today",
    "pipeline",
    "posts_today",
    "sentiment",
    "top_codes",
    "top_themes",
    "word_tempo",
]

DICT_KEYS = ["event_type", "sentiment", "content_type", "top_codes", "top_themes"]
LIST_KEYS = ["anomaly_signals", "high_confidence"]

# 动态抓取失败时的兜底（与 xq_classify.py 当前值一致）
FALLBACK_VERSION = "v1.3"
FALLBACK_MODEL = "qwen3.5:9b"


def load_expected_pipeline() -> tuple:
    """动态抓取 xq_classify 的版本三元组（extractor_version, llm_model, prompt_version）。

    xq_daily_report.py 同款动态语义：验收不硬编码版本，抓当前源码事实。
    导入失败（环境漂移等）退回字面量兜底，验收仍可独立运行。
    """
    try:
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from xq_classify import _TICKER_VERSION, MODEL, PROMPT_VERSION

        return str(_TICKER_VERSION), str(MODEL), str(PROMPT_VERSION)
    except Exception:
        return FALLBACK_VERSION, FALLBACK_MODEL, FALLBACK_VERSION


def check_payload(data, expect: tuple) -> list:
    """内容语义检查（full 模式）。返回 [(检查名, ASCII 原因)]，空列表 = 全过。"""
    fails = []
    ver, model, prompt = expect

    if not isinstance(data, dict):
        return [("json_shape", "latest.json is not a JSON object")]

    # 1) 15 键齐全性
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        fails.append(("required_keys", "missing keys: %s" % ",".join(missing)))

    # 2) extractor_version == 动态 _TICKER_VERSION
    if "extractor_version" in data:
        if data["extractor_version"] != ver:
            fails.append(
                (
                    "extractor_version",
                    "got %r expect %r (xq_classify._TICKER_VERSION)"
                    % (data["extractor_version"], ver),
                )
            )

    # 3) pipeline: llm_model + prompt_version
    pipe = data.get("pipeline")
    if "pipeline" in data:
        if not isinstance(pipe, dict):
            fails.append(("pipeline", "pipeline is not a dict"))
        else:
            if pipe.get("llm_model") != model:
                fails.append(
                    (
                        "pipeline_llm_model",
                        "got %r expect %r (xq_classify.MODEL)"
                        % (pipe.get("llm_model"), model),
                    )
                )
            pv = pipe.get("prompt_version")
            if not (isinstance(pv, str) and pv.strip()):
                fails.append(
                    (
                        "pipeline_prompt_version",
                        "prompt_version missing/empty: %r" % (pv,),
                    )
                )

    # 4) word_tempo: bearish/bullish 两列
    if "word_tempo" in data:
        wt = data["word_tempo"]
        if not isinstance(wt, dict):
            fails.append(("word_tempo", "word_tempo is not a dict"))
        else:
            for col in ("bearish", "bullish"):
                if col not in wt or not isinstance(wt[col], list):
                    fails.append(
                        ("word_tempo_" + col, "word_tempo[%r] missing/not a list" % col)
                    )

    # 5) content_type: dict；posts_today>0 且空 = 异常
    if "content_type" in data:
        ct = data["content_type"]
        if not isinstance(ct, dict):
            fails.append(("content_type", "content_type is not a dict"))
        elif data.get("posts_today", 0) and len(ct) == 0:
            fails.append(
                (
                    "content_type_empty",
                    "content_type empty dict but posts_today=%s>0"
                    % data.get("posts_today"),
                )
            )

    # 6) 容器键类型抽查
    for k in DICT_KEYS:
        if k in data and not isinstance(data[k], dict):
            fails.append(
                ("type_" + k, "%s should be dict, got %s" % (k, type(data[k]).__name__))
            )
    for k in LIST_KEYS:
        if k in data and not isinstance(data[k], list):
            fails.append(
                ("type_" + k, "%s should be list, got %s" % (k, type(data[k]).__name__))
            )
    return fails


def check_freshness(mtime_ts: float, now: datetime.datetime) -> list:
    """新鲜度：latest.json mtime 应在今天 19:00 之后（仅 full 模式调用）。"""
    threshold = now.replace(hour=19, minute=0, second=0, microsecond=0)
    mt = datetime.datetime.fromtimestamp(mtime_ts)
    if mt < threshold:
        return [
            (
                "freshness",
                "latest.json mtime %s is before today 19:00 (%s)"
                % (
                    mt.strftime("%Y-%m-%d %H:%M:%S"),
                    threshold.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
        ]
    return []


def check_source_health(manifest_path: str = MANIFEST_JSON) -> list:
    """采集侧降级检查（full 模式）：hot 是新帖唯一来源。

    只看文件新鲜度不够：2026-10-19~25 latest.json 天天更新（verify 10-25 rc=0），
    但 posts_today 恒为 0，因为 xq_hot_post 一直 failed（token 400016/出口黑名单）。
    manifest 读不到时 fail-open（不误报老环境）。
    """
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        return []
    hot = m.get("xq_hot_post") or {}
    st = hot.get("status")
    if st in ("failed", "ip_blocked"):
        note = str(hot.get("note") or "")[:120]
        return [
            (
                "source_degraded",
                "xq_hot_post status=%s (%s): no fresh posts source" % (st, note),
            )
        ]
    return []


def _wait_for_report(
    latest_path: str, now: datetime.datetime, max_wait_s: int = None
) -> float:
    """重启补跑场景：若今天有 cron_r_*.out 报告进程痕迹而 latest.json 还没更新，
    轮询等待（默认 30s 间隔，上限 FRESHNESS_WAIT_SECONDS）。返回最新的 mtime。
    """
    if max_wait_s is None:
        max_wait_s = FRESHNESS_WAIT_SECONDS
    threshold = now.replace(hour=19, minute=0, second=0, microsecond=0)
    mt = os.path.getmtime(latest_path)
    if datetime.datetime.fromtimestamp(mt) >= threshold:
        return mt
    report_dir = os.path.dirname(latest_path)
    waited = 0
    while waited < max_wait_s:
        running = False
        try:
            for fn in os.listdir(report_dir):
                if not fn.startswith("cron_r_"):
                    continue
                p = os.path.join(report_dir, fn)
                if time.time() - os.path.getmtime(p) < 3600:
                    running = True
                    break
        except OSError:
            break
        if not running:
            break
        time.sleep(30)
        waited += 30
        mt = os.path.getmtime(latest_path)
        if datetime.datetime.fromtimestamp(mt) >= threshold:
            break
    return mt


def run(
    latest_path: str = LATEST_JSON,
    mode: str = "full",
    now: datetime.datetime = None,
    expect: tuple = None,
    status_log: str = None,
    alert_log: str = None,
    manifest_path: str = None,
) -> int:
    """验收主入口。返回退出码 0/1/2。status_log/alert_log 为 None 则不落盘（测试用）。

    manifest_path：采集侧 manifest 路径，None 用默认 MANIFEST_JSON。
    2026-11-08 新增注入点：此前 run() 直接吃默认值，测试无法隔离线上的
    xq_hot_post 状态——线上一旦 failed，健康路径用例（test_run_full_ok_rc0）
    就会假红，属于测试耦合环境而非产品回归。默认行为不变。
    """
    if now is None:
        now = datetime.datetime.now()
    if expect is None:
        expect = load_expected_pipeline()
    default_logs = status_log is None and alert_log is None
    if default_logs:
        status_log, alert_log = STATUS_LOG, ALERT_LOG

    ts = now.strftime("%Y-%m-%dT%H:%M:%S")

    # 链路第一关：latest.json 存在且可读（smoke/full 共用）
    if not os.path.exists(latest_path):
        _emit_status(status_log, ts, 2, mode, "latest.json not generated yet")
        print("[verify] rc=2 latest.json not generated (early state, not a crash)")
        return 2
    try:
        with open(latest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        if mode == "smoke":
            _write_alert(
                alert_log, ts, mode, [("json_read", "latest.json unreadable: %s" % e)]
            )
            _emit_status(status_log, ts, 1, mode, "json unreadable")
            print("[verify] rc=1 latest.json unreadable: %s" % e)
            return 1
        # full 模式 JSON 坏了属于内容失败，走统一 1 号出口
        fails = [("json_read", "latest.json unreadable: %s" % e)]
        _write_alert(alert_log, ts, mode, fails)
        _emit_status(status_log, ts, 1, mode, "%d failures" % len(fails))
        print("[verify] rc=1 %d failures" % len(fails))
        for name, why in fails:
            print("  FAIL %s: %s" % (name, why))
        return 1

    if mode == "smoke":
        # 冒烟只验链路：脚本能跑 + latest.json 可读，不判语义/新鲜度
        _emit_status(status_log, ts, 0, mode, "smoke ok (chain only)")
        print("[verify] rc=0 smoke ok (chain only, semantics skipped)")
        return 0

    # full 模式：键齐全性 + 内容语义 + 采集降级 + 新鲜度（带补跑等待）
    fails = check_payload(data, expect)
    mt = _wait_for_report(latest_path, now)
    fails += check_freshness(mt, now)
    fails += check_source_health(manifest_path or MANIFEST_JSON)

    if fails:
        _write_alert(alert_log, ts, mode, fails)
        _emit_status(status_log, ts, 1, mode, "%d failures" % len(fails))
        print("[verify] rc=1 %d failures" % len(fails))
        for name, why in fails:
            print("  FAIL %s: %s" % (name, why))
        return 1

    _emit_status(
        status_log, ts, 0, mode, "all %d checks passed" % (len(REQUIRED_KEYS) + 5)
    )
    print("[verify] rc=0 all checks passed")
    return 0


def _emit_status(status_log: str, ts: str, rc: int, mode: str, note: str) -> None:
    """状态日志追加一行（纯 ASCII，判例同 report_status.log）。"""
    try:
        with open(status_log, "a", encoding="ascii", errors="replace") as f:
            f.write("%s rc=%d mode=%s %s\n" % (ts, rc, mode, note))
    except Exception as e:
        print("[verify] WARN status log write failed: %s" % e)


def _write_alert(alert_log: str, ts: str, mode: str, fails: list) -> None:
    """告警日志覆盖式落盘（含时间戳与逐项失败原因，纯 ASCII）。"""
    lines = ["[%s] XueqiuDailyReport verify FAILED (mode=%s)" % (ts, mode)]
    for name, why in fails:
        lines.append("  FAIL %s: %s" % (name, why))
    try:
        with open(alert_log, "w", encoding="ascii", errors="replace") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print("[verify] WARN alert log write failed: %s" % e)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify XueqiuDailyReport output")
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="chain-only check: skip 19:00 freshness and semantics",
    )
    ap.add_argument(
        "--latest", default=LATEST_JSON, help="override latest.json path (tests)"
    )
    args = ap.parse_args()
    return run(latest_path=args.latest, mode="smoke" if args.smoke else "full")


if __name__ == "__main__":
    sys.exit(main())
