#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OOS 首评包装器（W5b 任务A）：不改 jev_eval.py，运行时注入三件事：
  1. DPAPI 解密 JEV_API_KEY 注入环境（jev_eval 在 import 时读 env）；
  2. QUESTIONS_FILE 指向 oos_question_set_v2.json（2026-10-14 起：v1 为开卷题已作废，
     禁止再指向 v1）；
  3. call_jev 加"失败重试 1 次 + 连续 5 题失败熔断"。
评分/汇总逻辑全部沿用 jev_eval.run()（repeats=1）。

⚠ 一次性评估包装器，勿被 import（本模块 import 即做进程级 monkeypatch，
  且会覆盖环境变量；只允许作为脚本直接执行）。

用法：
  python run_oos_eval.py --dry-run   # 请求构造预览，不调 API
  python run_oos_eval.py             # 正式跑 72 题×1 遍
退出码：0 正常完成；3 连续 5 题失败熔断。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from load_env_dpapi import get_secret  # noqa: E402

# DPAPI 配置先注入环境（jev_eval 模块级读 env，必须先于 import）
# 审计修复（2026-10-14）：setdefault → 强制覆盖。调度环境可能残留旧明文值，
# setdefault 会让旧值静默胜出（评估打到旧凭据/旧网关，结果不可信）。
for _k in ("JEV_API_KEY", "JEV_BASE_URL", "JEV_MODEL"):
    _v = get_secret(_k)
    if _v:
        if _k in os.environ:
            # 只报键名，绝不打印值（新旧值都不落日志）
            print(f"WARN: 环境已有 {_k} 残留值，已用 DPAPI 解密值强制覆盖", flush=True)
        os.environ[_k] = _v
if not os.environ.get("JEV_API_KEY"):
    sys.exit("DPAPI 解密 JEV_API_KEY 失败，中止（不落明文日志）")

# K-STOCK-366（084 判例）：CF 对大陆出口 geo-block → 直连 typesafe 全端点 451。
# 与产线 signal_review_gate.py 同款：出站走本机 7890 代理（urllib 默认
# opener 读 getproxies()，HTTPS_PROXY/HTTP_PROXY 环境变量等价生效，不动 jev_eval.py）。
_ts_proxy = os.environ.get("TS_PROXY", "http://127.0.0.1:7890")
# 强制覆盖（不能 setdefault：宿主环境可能自带别的代理，出口地域被 451）
os.environ["HTTP_PROXY"] = _ts_proxy
os.environ["HTTPS_PROXY"] = _ts_proxy

import urllib.error  # noqa: E402

import jev_eval  # noqa: E402

jev_eval.QUESTIONS_FILE = HERE / "oos_question_set_v2.json"

_orig_call = jev_eval.call_jev
_state = {"attempt": 0, "consec_fail": 0}  # attempt: 当前请求已试次数；consec_fail: 连续失败请求数


def call_with_retry(body: dict):
    try:
        out = _orig_call(body)
        _state["attempt"] = 0
        _state["consec_fail"] = 0
        return out
    except urllib.error.HTTPError as e:
        # 401/403/451 = 内容级确定性拒绝（084 判例：TypeSafe 合规过滤，重试必然同错）
        if e.code in (401, 403, 451):
            _state["attempt"] = 0
            _state["consec_fail"] += 1
            print(f"    HTTP {e.code}（内容级确定性拒绝，不重试），连续失败 {_state['consec_fail']}/5", flush=True)
            if _state["consec_fail"] >= 5:
                print("!! 连续 5 题失败，按纪律中止评估（退出码 3）", flush=True)
                sys.exit(3)
            raise
        return _call_with_retry_inner(body, e)
    except Exception as e:  # noqa: BLE001
        return _call_with_retry_inner(body, e)


def _call_with_retry_inner(body: dict, first_err: Exception):
    _state["attempt"] += 1
    if _state["attempt"] <= 1:  # 首次失败 → 重试 1 次（仅网络/瞬时类错误）
        print(f"    请求失败（{type(first_err).__name__}: {first_err}），2s 后重试 1 次...", flush=True)
        time.sleep(2.0)
        try:
            out = _orig_call(body)
            _state["attempt"] = 0
            _state["consec_fail"] = 0
            return out
        except Exception as e2:  # noqa: BLE001
            first_err = e2
    # 重试仍失败：交给 jev_eval 记 None（计错题），并累加熔断计数
    _state["attempt"] = 0
    _state["consec_fail"] += 1
    print(f"    重试后仍失败（{type(first_err).__name__}: {first_err}），连续失败 {_state['consec_fail']}/5", flush=True)
    if _state["consec_fail"] >= 5:
        print("!! 连续 5 题失败，按纪律中止评估（退出码 3）", flush=True)
        sys.exit(3)
    raise first_err


jev_eval.call_jev = call_with_retry

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv[1:]
    print(f"OOS 首评：题集={jev_eval.QUESTIONS_FILE.name} repeats=1 model={jev_eval.MODEL}", flush=True)
    jev_eval.run(repeats=1, dry_run=dry)
