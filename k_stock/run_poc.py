# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · 日终 runner

用法：
  python k_stock/run_poc.py 600519.SH 贵州茅台 [--mock] [--out data/poc_YYYYMMDD.json]
  python k_stock/run_poc.py --batch data/watchlist.txt   # 批量（每行 "代码 名称"）

产出：data/poc_<日期>.json（五节点结构化输出+trace+degraded，lineage 可追溯）
S1 判据挂钩：连续 5 个交易日无人干预跑通；picks 次日收益秩相关>0（对照脚本 S2 挂）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from k_stock.kstock.llm import LLMClient
from k_stock.kstock.orchestrator import Orchestrator, load_team

TEAM = HERE / "config" / "team_poc.json"
OUT_DIR = HERE / "data"
GUARD_LOG = HERE / "data" / "guard_aborts.jsonl"   # 守卫中止结构化留痕（append jsonl）
HEARTBEAT = HERE / "data" / "last_run.json"        # 跑批心跳（mtime 即判"今天跑没跑"）


def _append_guard_log(record: dict) -> None:
    """守卫中止落盘（K-STOCK-20261019-GUARDLOG）。
    O_APPEND + 单次 os.write：小体积追加在单写者语义下即原子，多进程并发最多
    交错行不会撕裂行（与 llm_cost jsonl 判例同风格）。
    落盘失败静默降级——日志失败绝不改变主流程退出码（exit 3 语义保持不变）。"""
    try:
        GUARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(GUARD_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass


def _write_heartbeat(rc: int, elapsed_s: float, summary: dict) -> None:
    """跑批心跳：正常完成/异常退出（含守卫 exit 3）均落 data/last_run.json，
    「今天跑没跑」看文件 mtime 即判，零产物不再无法区分未触发/被拦截。
    tmp+os.replace 原子换血，落盘失败静默。"""
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "rc": rc, "elapsed_s": round(elapsed_s, 1), "summary": summary}
        tmp = HEARTBEAT.with_name(HEARTBEAT.name + ".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(HEARTBEAT))
    except Exception:
        pass


def run_one(stock: str, context: str = "", mock: bool = False, auto: bool = False) -> dict:
    nodes = load_team(TEAM)
    if auto:
        from k_stock.kstock.datafeed import build_context
        ctx = build_context(stock.split()[0])  # "代码 名称" 取代码
        context = (f"[技术面]\n{ctx['tech']}\n[新闻情绪]\n{ctx['news']}\n"
                   f"[基本面]\n{ctx['funda']}")
    client = LLMClient(mock=mock)
    orch = Orchestrator(nodes, client.chat)
    result = orch.run(stock=stock, context=context)
    result["stock"] = stock
    result["asof"] = datetime.now().isoformat(timespec="seconds")
    result["mock"] = mock
    result["auto_context"] = auto
    return result


def _competitor_note() -> str:
    """中止归因：探测竞争推理服务（llama-server 等）是否满载（>1GB≈模型驻留推理态）。
    背景：10-08 实锤第三方私起 llama-server（监听 54199，非 K259 的 8030 入口）
    满载 8.22GB 时与 Ollama 互相踩踏，idle 时自动卸载回 263MB。"""
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True).stdout.decode("gbk", errors="replace")
        for line in out.splitlines():
            if "llama-server" in line.lower():
                parts = line.split('","')
                if len(parts) >= 5:
                    mem_kb = int(parts[4].replace(',', '').replace('"', '').replace(" K", "").strip())
                    mem_gb = mem_kb / 1024 / 1024
                    if mem_gb > 1.0:
                        return (f"竞争者定位：llama-server.exe 满载 {mem_gb:.2f}GB（推理态，"
                                f"与 Ollama 踩踏主因）——请确认归属后关闭或错峰。")
                    return f"竞争者在册但 idle（{mem_gb * 1024:.0f}MB，已卸载）——负载另有来源。"
        return ""
    except Exception:
        return ""


def load_guard(threshold_s: float = 6.0, retries: int = 2, cooldown_s: float = 180.0) -> tuple[float, float]:
    """两段式负载守卫（2026-10-08 修正）：
    第 1 探针 = 冷启动预热（Ollama keep_alive 默认 5m 过期即卸载模型，重载 70s+，不计判定）；
    第 2 探针 = 热态真实推理速度，超阈值判定负载挤压 → 冷却 cooldown_s 后重探，
    最多 retries 次（负载是波动的：18:05 实测 18.0s→18:15 回落；第三方 llama-server
    等竞争者有 idle 自动卸载机制，满载→冷却→回落后即可放行）。全重试仍超 → 由调用方中止。
    阈值 6s（标定曲线，10-08 实证）：热态 2s→5/5 全绿 | 8.8s→3/5（T2/N1 崩）|
    10.8s→1/5（T1/N1 合法 JSON 前缀截断=生成流中途夭折）。S1 判据要有效数据，
    宁缺毋滥；空闲基线 ~2s，6s 有 3 倍余量。
    返回 (冷耗时, 最终热耗时)。守卫顺带完成预热，一举两得。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _probe() -> float:
        t0 = time.time()
        body = json.dumps({"model": "qwen3.5:9b", "stream": False, "keep_alive": 1800,
                           "messages": [{"role": "user", "content": "回复两个字：正常"}]}).encode()
        d = json.loads(op.open(urllib.request.Request(
            "http://127.0.0.1:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"}), timeout=300).read())
        _ = d["message"]["content"]
        return time.time() - t0

    t_cold = _probe()                       # 预热，不计判定
    t_warm = _probe()
    attempt = 0
    while t_warm > threshold_s and attempt < retries:
        attempt += 1
        print(f"[guard] 热态 {t_warm:.1f}s 超阈（第 {attempt}/{retries} 次）——"
              f"冷却 {cooldown_s:.0f}s 后重探（等竞争者 idle 卸载）…", flush=True)
        time.sleep(cooldown_s)
        t_warm = _probe()
    return t_cold, t_warm


def main():
    t0 = time.time()
    rc = 0
    summary: dict = {}
    try:
        _run_targets(summary)
    except SystemExit as e:                       # 守卫 exit 3 / argparse exit 2 均留心跳
        rc = e.code if isinstance(e.code, int) else 1
        raise
    except BaseException:
        rc = 1
        raise
    finally:
        _write_heartbeat(rc, time.time() - t0, summary)


def _run_targets(summary: dict) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stock", nargs="?", help="如 600519.SH 贵州茅台（加引号）")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--auto", action="store_true", help="三路 context 自动接入（D0 正式）")
    ap.add_argument("--batch", help="清单文件，每行 '代码 名称' 或纯代码")
    ap.add_argument("--watchlist", type=int, metavar="N",
                    help="自动取监控池成交额前 N 只（与 --auto 配套）")
    ap.add_argument("--out", help="输出 json 路径（默认 data/poc_<时间戳>.json）")
    ap.add_argument("--no-guard", action="store_true", help="跳过负载守卫")
    args = ap.parse_args()

    targets: list[str] = []
    if args.watchlist:
        from k_stock.kstock.datafeed import watchlist_from_quotes
        targets = watchlist_from_quotes(args.watchlist)
    elif args.batch:
        for line in Path(args.batch).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)
    elif args.stock:
        targets.append(args.stock)
    else:
        ap.error("需要 stock / --batch / --watchlist 之一")

    if not args.mock and not args.no_guard:
        try:
            t_cold, t_warm = load_guard()
            print(f"[guard] 冷启动 {t_cold:.1f}s（预热，不计判定）| 热态推理 {t_warm:.1f}s"
                  f"（含冷却重探，最多 2 次×180s）", flush=True)
            if t_warm > 6.0:
                culprit = _competitor_note()
                print(f"[guard] 热态超 6s（含重试）——系统负载挤压推理"
                      f"（标定：2s 全绿/8.8s 半崩/10.8s 仅 1 节点存活），中止。{culprit}"
                      f"建议换时段（清晨 07:05 自动化窗口）或关闭竞争推理服务后重试。", flush=True)
                _append_guard_log({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "phase": "probe_hot", "latency_s": round(t_warm, 2),
                    "t_cold_s": round(t_cold, 2), "threshold_s": 6.0,
                    "reason": "热态超6s（含冷却重试）中止", "culprit": culprit,
                    "ollama_alive": True, "watchlist_size": len(targets),
                })
                summary.update({"stage": "guard", "phase": "probe_hot"})
                sys.exit(3)
        except Exception as e:
            print(f"[guard] Ollama 不可达: {e}", flush=True)
            _append_guard_log({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "phase": "probe_unreachable", "latency_s": None,
                "reason": f"Ollama 不可达: {e}", "ollama_alive": False,
                "watchlist_size": len(targets),
            })
            summary.update({"stage": "guard", "phase": "probe_unreachable"})
            sys.exit(3)

    OUT_DIR.mkdir(exist_ok=True)
    results = []
    for stock in targets:
        print(f"[run] {stock} ...", flush=True)
        try:
            r = run_one(stock, mock=args.mock, auto=args.auto)
            d1 = r["outputs"].get("D1", {})
            print(f"  -> D1: {d1.get('action')} conf={d1.get('confidence')} "
                  f"degraded={r['degraded'] or '无'}", flush=True)
        except Exception as e:
            r = {"stock": stock, "error": str(e)[:300],
                 "asof": datetime.now().isoformat(timespec="seconds")}
            print(f"  -> FAIL: {r['error']}", flush=True)
        results.append(r)

    ok = sum(1 for r in results if "error" not in r)
    degraded = sum(1 for r in results if r.get("degraded"))
    summary.update({"targets": len(targets), "ok": ok, "fail": len(results) - ok,
                    "degraded": degraded, "mock": args.mock})
    out = Path(args.out) if args.out else OUT_DIR / f"poc_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved ->", out)


if __name__ == "__main__":
    main()
