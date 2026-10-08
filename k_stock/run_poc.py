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
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from k_stock.kstock.llm import LLMClient
from k_stock.kstock.orchestrator import Orchestrator, load_team

TEAM = HERE / "config" / "team_poc.json"
OUT_DIR = HERE / "data"


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


def load_guard(threshold_s: float = 6.0) -> tuple[float, float]:
    """两段式负载守卫（2026-10-08 修正）：
    第 1 探针 = 冷启动预热（Ollama keep_alive 默认 5m 过期即卸载模型，重载 70s+，不计判定）；
    第 2 探针 = 热态真实推理速度，超阈值判定负载挤压，中止跑批。
    阈值 6s（标定曲线，10-08 实证）：热态 2s→5/5 全绿 | 8.8s→3/5（T2/N1 崩）|
    10.8s→1/5（T1/N1 合法 JSON 前缀截断=生成流中途夭折）。S1 判据要有效数据，
    宁缺毋滥；空闲基线 ~2s，6s 有 3 倍余量。
    返回 (冷耗时, 热耗时)。守卫顺带完成预热，一举两得。"""
    import time
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t_cold = t_warm = 0.0
    for i in range(2):
        t0 = time.time()
        body = json.dumps({"model": "qwen3.5:9b", "stream": False, "keep_alive": 1800,
                           "messages": [{"role": "user", "content": "回复两个字：正常"}]}).encode()
        d = json.loads(op.open(urllib.request.Request(
            "http://127.0.0.1:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"}), timeout=300).read())
        _ = d["message"]["content"]
        if i == 0:
            t_cold = time.time() - t0
        else:
            t_warm = time.time() - t0
    return t_cold, t_warm


def main():
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
            print(f"[guard] 冷启动 {t_cold:.1f}s（预热，不计判定）| 热态推理 {t_warm:.1f}s", flush=True)
            if t_warm > 6.0:
                print(f"[guard] 热态超 6s——系统负载挤压推理（标定：2s 全绿/8.8s 半崩/10.8s 仅 1 节点存活），中止。"
                      f"建议换时段（收盘后低峰/清晨）或降低系统负载后重试。", flush=True)
                sys.exit(3)
        except Exception as e:
            print(f"[guard] Ollama 不可达: {e}", flush=True)
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

    out = Path(args.out) if args.out else OUT_DIR / f"poc_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved ->", out)


if __name__ == "__main__":
    main()
