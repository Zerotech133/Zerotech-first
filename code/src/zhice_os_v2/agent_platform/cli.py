"""agent_platform CLI — 容量体检 / 回测 / 单分片影子运行 / 学习闭环毕业。

用法：
  python -m agent_platform.cli capacity --symbols 37 --methods 3 --hz 5s
  python -m agent_platform.cli backtest --market CRYPTO --days 30 --freq 15m \
      [--symbols BTC/USDT,ETH/USDT] [--real-llm-sample 20] [--out-dir D:\\reports]
  python -m agent_platform.cli watch --market CRYPTO --cycles 3     (影子运行)
  python -m agent_platform.cli watch --market CRYPTO --dryrun-card  (学习闭环演示)
  python -m agent_platform.cli graduate --market CRYPTO             (毕业权重闭环)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from zhice_os_v2.agent_platform.config import AgentPlatformConfig


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent_platform", description="盯盘 Agent 中台 CLI（对 zhice 主干只读）"
    )
    p.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING")
    sub = p.add_subparsers(dest="cmd", required=True)

    # capacity
    cap = sub.add_parser("capacity", help="容量自检：S×M×c_eval ≤ 预算×η")
    cap.add_argument("--symbols", type=int, default=37)
    cap.add_argument("--methods", type=int, default=3)
    cap.add_argument("--hz", default="5s", help="5s/10s/3m 或秒数")
    cap.add_argument("--benchmark", action="store_true", help="实测 c_eval（合成 fixture）")

    # backtest
    bt = sub.add_parser("backtest", help="历史 bar 逐根重放回测（本版核心）")
    bt.add_argument("--market", default="CRYPTO")
    bt.add_argument("--days", type=int, default=30)
    bt.add_argument("--freq", default="15m", choices=["5m", "15m", "1h"])
    bt.add_argument("--symbols", default="", help="逗号分隔；缺省=全池")
    bt.add_argument("--real-llm-sample", type=int, default=0,
                    help=">0 时前 N 次冲突真调 Ollama 对照（共享令牌桶）")
    bt.add_argument("--out-dir", default="", help="报告目录（缺省 code/agent_platform_reports）")
    bt.add_argument("--warmup", type=int, default=60, help="预热 bar 数")
    bt.add_argument("--window", type=int, default=120, help="滚动窗口 bar 数")
    bt.add_argument("--methods", default="chan,volume,rsi_spec", help="逗号分隔方法集")
    bt.add_argument("--playbook", default="",
                    help="T07 打法包 id（如 org_style_cn）——按绑定分组重放；"
                         "CN 全池过大时自动取成交额 top50")

    # watch（影子运行）
    w = sub.add_parser("watch", help="单分片影子运行（只出决策不发卡片）")
    w.add_argument("--market", default="CRYPTO")
    w.add_argument("--cycles", type=int, default=1)
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--symbols", default="", help="逗号分隔；缺省=发现全池")
    w.add_argument("--dryrun-card", action="store_true",
                   help="学习闭环演示：决策经 CardBridge 送审（无凭证=dryrun 卡片落盘），"
                        "并轮询 ack_inbox 处理人审回执 → 案例落库 → 记忆沉淀")

    # graduate（学习闭环 · 权重毕业）
    g = sub.add_parser("graduate", help="毕业判据 v2：评估+应用+打印报告（闭环合拢）")
    g.add_argument("--market", default="CRYPTO")
    g.add_argument("--methods", default="chan,volume,rsi_spec", help="逗号分隔方法集")
    g.add_argument("--apply", action="store_true", default=True,
                   help="评估后直接应用权重（aw_method + registry 热更新）")
    g.add_argument("--no-apply", dest="apply", action="store_false",
                   help="只评估不应用（dry 体检）")
    g.add_argument("--playbook", default="",
                   help="T07 按 (method, playbook) 独立毕业（如 org_style_cn）；"
                        "缺省=全量旧口径")

    # ack-serve（学习闭环 · HTTP ack 端点）
    a = sub.add_parser("ack-serve", help="ack HTTP 端点：POST /ack 回写处置记录")
    a.add_argument("--port", type=int, default=None,
                   help="缺省读 AW_ACK_PORT（默认 9105）")

    # watch-daemon（常驻盯盘）
    d = sub.add_parser("watch-daemon", help="WatchAgent 常驻循环（SIGTERM 优雅退出）")
    d.add_argument("--market", default="CRYPTO")
    d.add_argument("--symbols", default="", help="逗号分隔；缺省=发现全池")
    d.add_argument("--interval", type=float, default=None,
                   help="循环间隔秒；缺省=cfg.snapshot_hz")
    d.add_argument("--no-db", action="store_true", help="不落库（纯内存影子）")
    d.add_argument("--no-data", action="store_true", help="不挂真实数据源（空转冒烟）")
    d.add_argument("--cycles", type=int, default=None, help="限定周期数（冒烟用）")
    d.add_argument("--playbook-bind", action="append", default=None, metavar="SYM=PB",
                   help="T07 绑定打法包（可重复）：--playbook-bind \"000001.SZ=org_style_cn\"；"
                        "无此参数时自动读 aw_playbook_binding 表")

    # playbooks（T06 打法包管理）
    pb = sub.add_parser("playbooks", help="打法包管理：list/bind/unbind/show")
    pb_sub = pb.add_subparsers(dest="pb_cmd", required=True)
    pb_sub.add_parser("list", help="列出可用包与标的绑定状态")
    pb_bind = pb_sub.add_parser("bind", help="标的绑定打法包（写 aw_playbook_binding）")
    pb_bind.add_argument("--symbol", required=True)
    pb_bind.add_argument("--playbook", required=True, help="playbook_id")
    pb_unbind = pb_sub.add_parser("unbind", help="解绑标的（回默认包）")
    pb_unbind.add_argument("--symbol", required=True)
    pb_show = pb_sub.add_parser("show", help="展示包详情（方法集/权重/仲裁参数）")
    pb_show.add_argument("--playbook", required=True, help="playbook_id 或 yaml 路径")

    # events（T07 事件催化登记：event_window 数据源，手工维护）
    ev = sub.add_parser("events", help="事件催化登记（event_window 数据源）")
    ev_sub = ev.add_subparsers(dest="ev_cmd", required=True)
    ev_add = ev_sub.add_parser("add", help="登记事件（写 aw_playbook_event）")
    ev_add.add_argument("--symbol", required=True)
    ev_add.add_argument("--date", required=True, help="事件日 YYYY-MM-DD")
    ev_add.add_argument("--type", required=True,
                        choices=["order_tender", "price_hike", "theme"],
                        help="order_tender=订单中标 / price_hike=涨价 / theme=题材")
    ev_add.add_argument("--note", default="", help="事件备注（如订单金额）")
    ev_add.add_argument("--source", default="manual", help="来源标记（manual/api）")
    ev_list = ev_sub.add_parser("list", help="列出已登记事件")
    ev_list.add_argument("--symbol", default="", help="只看该标的（缺省全部）")
    return p


# ── 子命令实现 ─────────────────────────────────────────────────────────────
def cmd_capacity(args, cfg: AgentPlatformConfig) -> int:
    from zhice_os_v2.agent_platform.capacity import CapacityGuard

    guard = CapacityGuard(cfg.capacity)
    if args.benchmark:
        ms = guard.benchmark()
        print(f"c_eval 实测: {ms:.3f} ms/次（已回填）")
    report = guard.check(args.symbols, args.methods, args.hz)
    print(report.summary_line())
    print(json.dumps({"ok": report.ok, "utilization": round(report.utilization, 4),
                      "max_symbols_per_agent": report.max_symbols_per_agent,
                      "suggested_shards": report.suggested_shards}, ensure_ascii=False))
    return 0 if report.ok else 2


def cmd_backtest(args, cfg: AgentPlatformConfig) -> int:
    from zhice_os_v2.agent_platform.backtest.engine import BacktestEngine
    from zhice_os_v2.agent_platform.backtest.report import write_reports
    from zhice_os_v2.agent_platform.methods import MethodRegistry

    cfg.backtest_freq = args.freq
    cfg.warmup_bars = args.warmup
    cfg.window_bars = args.window

    # T07：--playbook 满编包回测（组方法集/权重/仲裁参数 + CN 数据装配）
    playbook = None
    if args.playbook:
        from zhice_os_v2.agent_platform.runtime.watch_daemon import (
            load_available_playbooks,
        )

        pbs = load_available_playbooks(cfg)
        if args.playbook not in pbs:
            print(f"未知 playbook_id: {args.playbook}（可选: {sorted(pbs)}）",
                  file=sys.stderr)
            return 2
        playbook = pbs[args.playbook]
        # 包内方法集需要注册（满编 registry；--methods 参数此时被包覆盖）
        from zhice_os_v2.agent_platform.methods import create_cn_registry, create_default_registry
        from zhice_os_v2.agent_platform.playbooks.spec import default_playbook

        full_reg = create_cn_registry() if args.market.upper() == "CN" else create_default_registry()
        missing = [mid for mid in playbook.method_ids() if full_reg.by_id(mid) is None]
        if missing:
            print(f"包方法未实现: {missing} — 无法回测", file=sys.stderr)
            return 2
        # org_style_cn 等包缺省方法集即为包声明（覆盖 --methods）
        engine = BacktestEngine(cfg, registry=full_reg)
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
        if args.real_llm_sample < 0:
            print("参数错误: --real-llm-sample 不能为负数", file=sys.stderr)
            return 2
        # CN 全池过大时取成交额 top50（v2_bar_daily.turnover 汇总）
        if symbols is None and args.market.upper() == "CN":
            symbols = _cn_top_by_turnover(cfg.db_url, start=None, top=50)
            if symbols:
                print(f"CN 全池过大 → 取成交额 top{len(symbols)}（指数/ETF 已排除："
                      f"前缀 000/399/899 + .SH 指数段过滤）")
        print(f"回测启动: market={args.market} days={args.days} freq={args.freq} "
              f"playbook={args.playbook} methods={playbook.method_ids()} "
              f"symbols={len(symbols) if symbols else '全池'}")
        try:
            result = engine.run(market=args.market, days=args.days, freq=args.freq,
                                symbols=symbols, real_llm_sample=args.real_llm_sample,
                                playbook=playbook)
        except RuntimeError as exc:
            if "无可用 bar 数据" in str(exc):
                print(f"无数据（market={args.market}, days={args.days}）——"
                      f"请检查数据窗口。原始原因: {exc}", file=sys.stderr)
                return 2
            raise
        if result.sentiment_synthetic:
            print("⚠ sentiment 序列为合成数据（synthetic=True，非真实行情）")
        if result.daily_source == "resample":
            print("⚠ 日线缺失：trend_break/event_window 用 15m 重采样近似")
        out_dir = args.out_dir or cfg.report_dir
        md_path, json_path, stats = write_reports(
            result, out_dir=out_dir, tag=args.playbook)
        print(f"决策 {stats['decision_total']} 条 | 路径 {stats.get('path_distribution', {})} "
              f"| 冲突率 {stats.get('conflict_rate', 0):.1%} | "
              f"耗时 {stats['elapsed_s']}s（{stats['avg_ms_per_bar']}ms/bar）")
        if result.freq_fallback:
            print(f"⚠ 档位降级: {result.fallback_reason}")
        print(f"报告: {md_path}")
        print(f"      {json_path}")
        return 0

    registry = MethodRegistry()
    from zhice_os_v2.agent_platform.methods.chan_method import ChanMethod
    from zhice_os_v2.agent_platform.methods.rsi_spec_method import RsiSpecMethod
    from zhice_os_v2.agent_platform.methods.volume_method import VolumeMethod
    factory = {"chan": ChanMethod, "volume": VolumeMethod, "rsi_spec": RsiSpecMethod}
    for mid in [m.strip() for m in args.methods.split(",") if m.strip()]:
        if mid not in factory:
            print(f"未知方法: {mid}（可选: {list(factory)}）", file=sys.stderr)
            return 2
        registry.register(factory[mid]())

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    if args.real_llm_sample < 0:
        print("参数错误: --real-llm-sample 不能为负数（0=全 mock，>0=前 N 次真调用）",
              file=sys.stderr)
        return 2
    engine = BacktestEngine(cfg, registry=registry)
    print(f"回测启动: market={args.market} days={args.days} freq={args.freq} "
          f"symbols={symbols or '全池'} real_llm_sample={args.real_llm_sample}")
    try:
        result = engine.run(market=args.market, days=args.days, freq=args.freq,
                            symbols=symbols, real_llm_sample=args.real_llm_sample)
    except RuntimeError as exc:
        # 数据缺失（常见：标的不存在/窗口内无数据）→ 友好提示而非裸栈
        if "无可用 bar 数据" in str(exc):
            shown = ",".join(symbols) if symbols else "全池"
            print(f"标的 {shown} 无数据（market={args.market}, days={args.days}, "
                  f"freq={args.freq}）——请检查标的代码与数据窗口。原始原因: {exc}",
                  file=sys.stderr)
            return 2
        raise
    out_dir = args.out_dir or cfg.report_dir
    md_path, json_path, stats = write_reports(result, out_dir=out_dir)
    print(f"决策 {stats['decision_total']} 条 | 路径 {stats.get('path_distribution', {})} "
          f"| 冲突率 {stats.get('conflict_rate', 0):.1%} | "
          f"耗时 {stats['elapsed_s']}s（{stats['avg_ms_per_bar']}ms/bar）")
    if result.freq_fallback:
        print(f"⚠ 档位降级: {result.fallback_reason}")
    print(f"报告: {md_path}")
    print(f"      {json_path}")
    return 0


def _cn_top_by_turnover(db_url: str, start=None, top: int = 50) -> list[str]:
    """CN 成交额 top N 个股（排除指数/ETF：代码前缀过滤）。失败返 []（全池）。"""
    from datetime import timedelta

    from sqlalchemy import text

    from zhice_os_v2.agent_platform.config import utc_now
    from zhice_os_v2.agent_platform.db import shared_engine

    try:
        start = start or (utc_now() - timedelta(days=45))
        sql = text(
            "select symbol, sum(turnover) tot from v2_bar_daily "
            "where market = 'CN' and trade_date >= :s and symbol not like '000%' "
            "and symbol not like '399%' and symbol not like '899%' "
            "group by symbol order by tot desc nulls last limit :n"
        )
        with shared_engine(db_url).connect() as conn:
            rows = conn.execute(sql, {"s": start.date(), "n": top}).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:
        print(f"⚠ CN 成交额 top 拉取失败({str(exc)[:80]}) — 回退全池", file=sys.stderr)
        return []


def cmd_watch(args, cfg: AgentPlatformConfig) -> int:
    """影子运行 N 周期（v1 无 data_provider 时为空转冒烟——验证启动自检与主循环）。

    --dryrun-card：接入学习闭环演示链——每周期决策经 CardBridge 送审
    （无飞书凭证 = dryrun 卡片 JSON 落盘 cards_outbox/），随后轮询 ack_inbox/
    处理人工回执（ack → 案例落库 → 记忆沉淀）。无 DB 时 aw 表更新自动跳过。
    """
    import time

    from zhice_os_v2.agent_platform.runtime.shard import ShardPolicy
    from zhice_os_v2.agent_platform.runtime.watch_agent import WatchAgent

    cfg.market = args.market
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        try:
            from zhice_os_v2.agent_platform.backtest.data_loader import discover_symbols

            symbols = discover_symbols(args.market, cfg.db_url)
        except Exception as exc:
            print(f"符号发现失败（回退空池）: {exc}", file=sys.stderr)
            symbols = []
    shard = ShardPolicy(market=args.market, symbols=symbols, snapshot_hz=cfg.snapshot_hz)

    card_bridge = case_writer = memory_sink = None
    if args.dryrun_card:
        from zhice_os_v2.agent_platform.learning import (
            CardBridge,
            CaseWriter,
            MemorySink,
        )

        card_bridge = CardBridge()
        case_writer = CaseWriter()          # 无 session_factory → aw 更新留日志跳过
        memory_sink = MemorySink(start_worker=True)
        mode = "dryrun(卡片落盘)" if card_bridge.dryrun else "飞书直连"
        print(f"[学习闭环] CardBridge={mode} outbox={card_bridge.outbox_dir} "
              f"ack_inbox={card_bridge.ack_inbox_dir}")

    agent = WatchAgent(shard=shard, cfg=cfg)
    agent.startup_check()
    for i in range(args.cycles):
        ctxs = []
        if args.dryrun_card and agent.data_provider is None:
            # 无线上数据时用合成 K 线演示（chan_div 形态走真实投票+仲裁出 SELL）
            ctxs = _demo_contexts(args.market)
        decisions = agent.run_cycle(ctxs=ctxs)
        print(f"cycle {i + 1}/{args.cycles}: {len(decisions)} 决策, "
              f"{agent.last_cycle_seconds * 1000:.1f}ms")
        if card_bridge is not None:
            sells = [d for d in decisions if d.action == "SELL"]
            for dec in sells:           # 仅 SELL 送审（PENDING_CONFIRM 语义）
                card_bridge.send_decision_card(dec.to_dict())
            if sells:
                print(f"  送审 {len(sells)} 条 SELL 决策 → "
                      f"{card_bridge.outbox_dir if card_bridge.dryrun else '飞书'}")
            for ack in card_bridge.poll_acks():
                case_writer.on_ack(ack)
        if i < args.cycles - 1:
            time.sleep(args.interval)
    if memory_sink is not None:
        memory_sink.close()
    print(f"影子运行完成 agent={agent.agent_id} cycles={agent.cycles}")
    return 0


def _demo_contexts(market: str) -> list:
    """--dryrun-card 演示数据：合成 K 线（vol_div 量价背离 + neutral）构造 WatchContext。

    vol_div 形态三方法仲裁稳定产出 SELL（WEIGHTED 路径）——保证 dryrun 演示必有卡片。
    """
    from zhice_os_v2.agent_platform.methods import WatchContext
    from zhice_os_v2.agent_platform.tests_support import synth_bars

    out = []
    for sym, pattern in [("DEMO-BTC/USDT", "vol_div"), ("DEMO-ETH/USDT", "neutral")]:
        df = synth_bars(200, seed=7, pattern=pattern)
        out.append(WatchContext(
            ts_code=sym, market=market,
            snapshot_tail=df.tail(3), bars_df=df.tail(120),
            regime_risk_off=False, memory_hints=[], position_value=0.0,
        ))
    return out


def cmd_graduate(args, cfg: AgentPlatformConfig) -> int:
    """毕业判据 v2 一键评估+应用+报告。--playbook 按 (method, playbook) 独立毕业。"""
    from zhice_os_v2.agent_platform.learning import GraduationService
    from zhice_os_v2.agent_platform.methods import MethodRegistry

    registry = MethodRegistry()
    from zhice_os_v2.agent_platform.methods import (
        ChanMethod,
        ChanStructureAlias,
        EventWindowMethod,
        RsiSpecMethod,
        SentimentRegimeMethod,
        TrendBreakMethod,
        VolumeMethod,
    )
    factory = {"chan": ChanMethod, "volume": VolumeMethod, "rsi_spec": RsiSpecMethod,
               "sentiment_regime": SentimentRegimeMethod, "trend_break": TrendBreakMethod,
               "event_window": EventWindowMethod, "chan_structure": ChanStructureAlias}
    method_ids = [m.strip() for m in args.methods.split(",") if m.strip()]
    # --playbook 时不指定 methods → 默认取该包方法集
    playbook_id = args.playbook or None
    if playbook_id and args.methods == "chan,volume,rsi_spec":
        from zhice_os_v2.agent_platform.runtime.watch_daemon import (
            load_available_playbooks,
        )

        pbs = load_available_playbooks(cfg)
        if playbook_id not in pbs:
            print(f"未知 playbook_id: {playbook_id}（可选: {sorted(pbs)}）",
                  file=sys.stderr)
            return 2
        method_ids = pbs[playbook_id].method_ids()
    for mid in method_ids:
        if mid not in factory:
            print(f"未知方法: {mid}（可选: {sorted(factory)}）", file=sys.stderr)
            return 2
        registry.register(factory[mid]())

    session_factory = None
    try:
        from zhice_os_v2.agent_platform.db import create_aw_tables, make_engine
        from sqlalchemy.orm import sessionmaker

        engine = make_engine(cfg=cfg)
        create_aw_tables(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    except Exception as exc:
        print(f"⚠ aw 库不可用({type(exc).__name__}: {str(exc)[:120]})——"
              f"仅 registry 热更新，不留 aw_graduation 痕", file=sys.stderr)

    svc = GraduationService(session_factory=session_factory, registry=registry)
    print(f"毕业评估 market={args.market} methods={method_ids} playbook={playbook_id or '-'} "
          f"(min_samples={svc.min_samples.get(args.market, svc.min_samples['DEFAULT'])})")
    reports = svc.evaluate_all(args.market, method_ids, playbook_id=playbook_id)
    print(f"\n{'方法':<18} {'n':>4} {'mean_fwd':>10} {'判定':<8} "
          f"{'旧权重':>7} {'新权重':>7}  原因")
    for r in reports:
        mean_s = f"{r['mean_fwd_ret']:+.2%}" if r["mean_fwd_ret"] is not None else "-"
        print(f"{r['method_id']:<18} {r['sample_size']:>4} {mean_s:>10} "
              f"{r['decision']:<8} {r['old_weight']:>7.3f} {r['new_weight']:>7.3f}  "
              f"{r['reason']}")
    if args.apply:
        weights = svc.apply_weights(reports, playbook_id=playbook_id)
        print(f"\n权重已应用: " + ", ".join(
            f"{k}={v:.3f}" for k, v in weights.items()))
        if playbook_id:
            print(f"aw_method 已按 (method@{playbook_id}) 复合行留痕（跨包默认权重行不动）")
        print("registry 热更新完成（下一轮仲裁即用新权重）")
    else:
        print("\n--no-apply：仅评估，权重未变更")
    return 0


def cmd_playbooks(args, cfg: AgentPlatformConfig) -> int:
    """T06 打法包管理：list/bind/unbind/show（绑定写 aw_playbook_binding）。"""
    from pathlib import Path

    from zhice_os_v2.agent_platform.playbooks.builtin import load_builtin_playbooks
    from zhice_os_v2.agent_platform.playbooks.mapping import PlaybookMapping
    from zhice_os_v2.agent_platform.playbooks.spec import (
        DEFAULT_PLAYBOOK_ID,
        default_playbook,
        load_playbook,
    )

    # 内置包 + 默认包 = 可用包集合
    playbooks = load_builtin_playbooks()
    playbooks[DEFAULT_PLAYBOOK_ID] = default_playbook(cfg.market, cfg)

    def _make_session_factory():
        try:
            from sqlalchemy.orm import sessionmaker

            from zhice_os_v2.agent_platform.db import create_aw_tables, make_engine

            engine = make_engine(cfg=cfg)
            create_aw_tables(engine)
            return sessionmaker(bind=engine, expire_on_commit=False)
        except Exception as exc:
            print(f"⚠ aw 库不可用({str(exc)[:100]}) — 绑定仅内存态", file=sys.stderr)
            return None

    if args.pb_cmd == "list":
        mapping = PlaybookMapping(session_factory=_make_session_factory())
        bindings = mapping.bindings()
        print(f"可用打法包（{len(playbooks)}）：")
        for pid, spec in sorted(playbooks.items()):
            n_bound = sum(1 for p in bindings.values() if p == pid)
            print(f"  {pid:<24} {spec.name}  market={spec.market} "
                  f"{spec.snapshot_hz:g}s/{spec.bar_freq} "
                  f"methods={len(spec.available_methods())}/{len(spec.methods)} "
                  f"绑定标的={n_bound}")
        rev = {}
        for sym, pid in sorted(bindings.items()):
            rev.setdefault(pid, []).append(sym)
        print(f"\n绑定状态（{len(bindings)} 条，未列出=default）：")
        for pid, syms in sorted(rev.items()):
            print(f"  {pid}: {', '.join(syms)}")
        if not bindings:
            print("  （无显式绑定，全部标的走默认包）")
        return 0

    if args.pb_cmd == "bind":
        if args.playbook not in playbooks:
            print(f"未知 playbook_id: {args.playbook}（可选: "
                  f"{sorted(playbooks)} 或 yaml 路径）", file=sys.stderr)
            return 2
        sf = _make_session_factory()
        mapping = PlaybookMapping(session_factory=sf)
        mapping.bind(args.symbol, args.playbook)
        state = "持久化+内存" if sf is not None else "仅内存（aw 库不可用）"
        print(f"已绑定 {args.symbol} → {args.playbook}（{state}）")
        return 0

    if args.pb_cmd == "unbind":
        sf = _make_session_factory()
        mapping = PlaybookMapping(session_factory=sf)
        old = mapping.unbind(args.symbol)
        print(f"已解绑 {args.symbol}（原 {old} → {DEFAULT_PLAYBOOK_ID}）")
        return 0

    if args.pb_cmd == "show":
        p = Path(args.playbook)
        if p.exists():
            spec = load_playbook(p)      # 任意 yaml 路径可预览
        elif args.playbook in playbooks:
            spec = playbooks[args.playbook]
        else:
            print(f"未知 playbook: {args.playbook}（可选 {sorted(playbooks)} "
                  f"或 yaml 路径）", file=sys.stderr)
            return 2
        print(spec.describe())
        for m in spec.methods:
            tag = " [unavailable·T07预留]" if m.unavailable else ""
            print(f"  - {m.id:<18} weight={m.weight:<4} {m.role}{tag}")
        ap = spec.arbitration
        print(f"  arbitration: quorum={ap.quorum_min_votes} "
              f"tie_margin={ap.tie_margin} regime_veto={ap.regime_veto}"
              f"（None=回退 config）")
        print(f"  graduation_tier={spec.graduation_tier} "
              f"entry_style_binding={spec.entry_style_binding} risk={spec.risk}")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = AgentPlatformConfig()
    if args.cmd == "capacity":
        return cmd_capacity(args, cfg)
    if args.cmd == "backtest":
        return cmd_backtest(args, cfg)
    if args.cmd == "watch":
        return cmd_watch(args, cfg)
    if args.cmd == "graduate":
        return cmd_graduate(args, cfg)
    if args.cmd == "ack-serve":
        return cmd_ack_serve(args, cfg)
    if args.cmd == "watch-daemon":
        return cmd_watch_daemon(args, cfg)
    if args.cmd == "playbooks":
        return cmd_playbooks(args, cfg)
    if args.cmd == "events":
        return cmd_events(args, cfg)
    parser.error(f"未知子命令: {args.cmd}")
    return 2


def cmd_ack_serve(args, cfg: AgentPlatformConfig) -> int:
    """ack HTTP 端点（T05）：POST /ack {decision_id, action, operator}。"""
    from zhice_os_v2.agent_platform.learning import CaseWriter
    from zhice_os_v2.agent_platform.learning.ack_server import AckServer

    session_factory = None
    try:
        from sqlalchemy.orm import sessionmaker

        from zhice_os_v2.agent_platform.db import create_aw_tables, make_engine

        engine = make_engine(cfg=cfg)
        create_aw_tables(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    except Exception as exc:
        print(f"⚠ aw 库不可用({str(exc)[:100]}) — ack 仅触发记忆侧", file=sys.stderr)

    server = AckServer(CaseWriter(session_factory=session_factory),
                       session_factory=session_factory, port=args.port)
    print(f"ack server listening 127.0.0.1:{server.port}/ack "
          f"(token={'on' if server.token else 'off(local-only)'}, Ctrl-C 退出)")
    try:
        server.serve_forever_safe()
    except KeyboardInterrupt:
        print("\nack server 退出")
    finally:
        server.server_close()
    return 0


def cmd_events(args, cfg: AgentPlatformConfig) -> int:
    """T07 事件催化登记（event_window 数据源，手工/外部注入）。"""
    from datetime import date as _date

    from sqlalchemy.orm import sessionmaker

    from zhice_os_v2.agent_platform.db import create_aw_tables, make_engine
    from zhice_os_v2.agent_platform.models import AwPlaybookEventModel

    engine = make_engine(cfg=cfg)
    create_aw_tables(engine)      # 幂等（aw_playbook_event 新表自动建）
    sf = sessionmaker(bind=engine, expire_on_commit=False)

    if args.ev_cmd == "add":
        try:
            ev_date = _date.fromisoformat(args.date)
        except ValueError:
            print(f"事件日期格式错误: {args.date!r}（应为 YYYY-MM-DD）", file=sys.stderr)
            return 2
        with sf() as s:
            s.add(AwPlaybookEventModel(
                symbol=args.symbol, event_date=ev_date,
                event_type=args.type, note=args.note, source=args.source))
            s.commit()
        print(f"已登记事件: {args.symbol} {ev_date} {args.type} {args.note}")
        return 0

    if args.ev_cmd == "list":
        with sf() as s:
            q = s.query(AwPlaybookEventModel)
            if args.symbol:
                q = q.filter(AwPlaybookEventModel.symbol == args.symbol)
            rows = q.order_by(AwPlaybookEventModel.event_date).all()
        if not rows:
            print("（无事件登记）")
            return 0
        print(f"{'symbol':<14} {'event_date':<12} {'type':<14} {'source':<8} note")
        for r in rows:
            print(f"{r.symbol:<14} {str(r.event_date):<12} {r.event_type:<14} "
                  f"{r.source:<8} {r.note}")
        return 0
    return 2


def cmd_watch_daemon(args, cfg: AgentPlatformConfig) -> int:
    """WatchAgent 常驻循环（T05）：决策落 aw_decision，SIGTERM/Ctrl-C 优雅退出。

    T07：--playbook-bind SYM=PB 显式绑定（可重复）；无参数时自动读
    aw_playbook_binding 表装配分组（读取失败降级全默认并 warn）。
    """
    import logging as _logging

    from zhice_os_v2.agent_platform.runtime.watch_daemon import build_daemon

    cfg = AgentPlatformConfig()
    cfg.market = args.market
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        try:
            from zhice_os_v2.agent_platform.backtest.data_loader import discover_symbols

            symbols = discover_symbols(args.market, cfg.db_url)
        except Exception as exc:
            print(f"符号发现失败（回退空池）: {exc}", file=sys.stderr)
            symbols = []

    session_factory = None
    if not args.no_db:
        try:
            from sqlalchemy.orm import sessionmaker

            from zhice_os_v2.agent_platform.db import create_aw_tables, make_engine

            engine = make_engine(cfg=cfg)
            create_aw_tables(engine)
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        except Exception as exc:
            print(f"⚠ aw 库不可用({str(exc)[:100]}) — 决策仅内存留痕", file=sys.stderr)

    # T07 playbook 绑定：显式参数优先；缺省读绑定表（失败降级全默认）
    playbook_binds = None
    if args.playbook_bind:
        playbook_binds = []
        for item in args.playbook_bind:
            if "=" not in item:
                print(f"--playbook-bind 格式错误: {item!r}（应为 SYM=playbook_id）",
                      file=sys.stderr)
                return 2
            sym, pid = item.split("=", 1)
            playbook_binds.append((sym.strip(), pid.strip()))
    elif session_factory is not None:
        from zhice_os_v2.agent_platform.runtime.watch_daemon import (
            load_bindings_from_db,
        )

        try:
            playbook_binds = load_bindings_from_db(session_factory)
            if playbook_binds:
                print(f"已从 aw_playbook_binding 装配 {len(playbook_binds)} 条绑定: "
                      + ", ".join(f"{s}={p}" for s, p in playbook_binds))
        except Exception as exc:
            _logging.getLogger(__name__).warning(
                "aw_playbook_binding 读取失败(%s) — 降级全默认包", exc)
            print(f"⚠ 绑定表读取失败({str(exc)[:80]}) — 全部标的走默认包",
                  file=sys.stderr)
            playbook_binds = None

    try:
        daemon = build_daemon(cfg=cfg, symbols=symbols, session_factory=session_factory,
                              use_db_data=not args.no_data, interval_s=args.interval,
                              playbook_binds=playbook_binds)
    except ValueError as exc:
        print(f"装配失败: {exc}", file=sys.stderr)
        return 2
    print(f"watch-daemon 启动 market={args.market} symbols={len(symbols)} "
          f"interval={daemon.interval_s}s db={'on' if session_factory else 'off'} "
          f"groups={len(daemon.playbook_plan) if daemon.playbook_plan else 'default-only'}")
    return daemon.run(max_cycles=args.cycles)


if __name__ == "__main__":
    sys.exit(main())
