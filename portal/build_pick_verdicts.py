# -*- coding: utf-8 -*-
"""选股信号后验计算器：历史池子里的票，信号日后 1/3/5 交易日的真实反应。

数据地基：portal/data/picks_history/todays_<YYYYMMDD>.json（build_todays_picks.py 每日归档）。
口径：信号日 = 归档的 scan_run_date（数据基准收盘日）。可成交口径（九审）：
      画像日 D 的清单在 D+1 15:10 才产出、最早 D+2 开盘可操作——买入价 =
      信号日后第 2 个交易日开盘，卖出 = 买入日后第 1/3/5 个交易日收盘，
      剔除「高开买不进」的虚假收益（原 D 收盘起算对强势共振票系统性虚高）。
      湖价（ts_daily，未复权原价口径与页面行情列一致）；对照 = 同窗口
      全市场开盘-收盘中位数（d0 开盘×d1 收盘，K-STOCK-077 修——旧实现
      d1 单日 pct 中位与个股 w 天累计错配；三十四审同步此 docstring）。
      复权口径（2026-10-11）：个股收益在 ts_adj_factor 窗口齐备时按
      open×adj / close×adj 计算（除权自动修正、样本不 void）；因子
      不可用时降级三十一审缺口守卫（≤-30.5% 判除权 void 剔除）。
输出：portal/data/pick_verdicts.json
      summary: 按窗口的整体命中率/均值/超额 + 按策略分层（二十五审方案A：
      by_strategy 只收 T1/T2 可操作层事件，windows 整体仍为全池口径）
      per_code: 每票最近一次信号的后验（前端详情卡直接消费，win 带 exec 首买日）
      四十审（2026-10-10）事件级样本口径：windows/by_strategy 的 n 为
      事件数——同票跨档再次入选=独立事件（各自 exec 首买日入桶，九审
      设计），n 可大于去重票数，与 per_code（每票最近一次）是两个视图。
窗口未满（湖还没走到首次可操作日或窗口终点）如实标 pending，不硬凑。
用法（推荐：固定 py311 + 超时 + 分片 + 独立输出 + 进度日志）:
  & "C:/Program Files/Python311/python.exe" portal/run_build_pick_verdicts_safe.py --help
直跑（生产链 portal/start_todays_picks.ps1 用法，全量 + 写生产 JSON）:
  "C:/Program Files/Python311/python.exe" portal/build_pick_verdicts.py
可调环境变量（安全运行器注入；直跑缺省=全量写生产，行为不变）:
  VERDICTS_OUT            输出路径，默认 portal/data/pick_verdicts.json
  VERDICTS_MAX_ARCHIVES   只算尾部 N 份归档（0/缺省=全量；增量/分片）
  VERDICTS_ARCHIVE_FROM   只算信号日 >= YYYYMMDD 的归档（含）
  VERDICTS_ARCHIVE_TO     只算信号日 <= YYYYMMDD 的归档（含）
  VERDICTS_PROGRESS_EVERY 档内每 N 只股票打一行进度（默认 25）
  VERDICTS_NO_PY_WARN=1   关闭「非 Python311」告警（仅诊断/测试）
py312 曾出 0xC0000005：WER 实证 python312.dll，且只在 faulthandler 定时栈
dump 命中 pandas/pyarrow 读链时复现（非本文件业务逻辑），故生产/回归统一
固定 Python311；别在该负载上用 faulthandler.dump_traceback_later 诊断。
"""

import glob
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
QB = os.path.join(ROOT, "quant_backtest")
HIST_DIR = os.path.join(HERE, "data", "picks_history")
PROD_OUT = os.path.abspath(os.path.join(HERE, "data", "pick_verdicts.json"))
OUT = os.path.abspath(os.environ.get("VERDICTS_OUT") or PROD_OUT)
WINDOWS = (1, 3, 5)


def _trading_days(cal):
    """湖全市场交易日序列（升序 YYYYMMDD 字符串）。"""
    return sorted(cal)


def _code_to_ts(code):
    """600540.SH → ts_daily 的 ts_code 同形态（湖本身就是 .SH 风格）。"""
    return (code or "").strip().upper()


def _env_int(name, default=0):
    """读整数环境变量；非法值忽略并留痕（配置笔误不该中断产线）。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        print("[verdicts] 环境变量 %s=%r 非整数，按 %d 处理" % (name, raw, default))
        return default


def _norm_day(value):
    """YYYY-MM-DD / YYYYMMDD → YYYYMMDD（只留数字前 8 位）。"""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8]


def _archive_date(fp):
    """归档文件名里的信号日（build_todays_picks 以 scan_run_date 命名）。"""
    return _norm_day(os.path.basename(fp))


def select_archives(archs, max_archives=0, date_from="", date_to=""):
    """按信号日区间 + 尾部 N 档筛选待算归档（分片/回归用）。

    只选择「算哪些归档」，不改变单档计算与汇总口径；区间先筛、再取尾部，
    保证 max_archives 永远命中最新档。纯函数，供测试固化分片语义。
    """
    selected = sorted(archs)
    d_from = _norm_day(date_from)
    d_to = _norm_day(date_to)
    if d_from or d_to:
        selected = [
            fp
            for fp in selected
            if (not d_from or _archive_date(fp) >= d_from)
            and (not d_to or _archive_date(fp) <= d_to)
        ]
    if max_archives and int(max_archives) > 0:
        selected = selected[-int(max_archives):]
    return selected


def _python_warn():
    """非 Python311 告警：不阻断（诊断需要），只提示走安全运行器。"""
    if os.environ.get("VERDICTS_NO_PY_WARN") == "1":
        return
    ver = sys.version_info[:2]
    if ver != (3, 11):
        print(
            "[verdicts] WARN 当前 Python %d.%d 非生产口径 Python311；"
            "py312 曾出 0xC0000005（faulthandler 定时栈 dump×pandas/pyarrow 读链）"
            "——请用 portal/run_build_pick_verdicts_safe.py 固定 py311" % ver,
            flush=True,
        )


def pct_between(code, exec_day, d1):
    """可成交口径（九审）：exec_day 开盘买入 → d1 收盘卖出。

    画像日 D 的清单在 D+1 15:10 产出、最早 D+2 开盘可操作——起算价
    用首次可操作日开盘，剔除强势票「次日高开买不进」的虚假收益。
    停牌近似：exec_day 无行时首行（复牌日）开盘即买点。

    复权口径（2026-10-11 消费端升级，ts_adj_factor 入湖）：窗口因子
    齐备时 buy=open_0×adj_0、sel=close_-1×adj_-1——除权窗口自动修正、
    样本保留不再 void；因子跳升替代 -30.5% 缺口猜除权后，首轮实证
    49/277 起「缺口守卫假阳性」（退市整理真暴跌被误 void）自然消除。
    降级路径（因子表未就绪/该票窗口缺行）：保持三十一审 -30.5% 缺口
    守卫返回 "exdiv" 哨兵由调用方 void 剔除（不比升级前差）。
    小额现金分红缺口（约-1~-3%）复权口径下同样被因子修正。
    P2 抽出（2026-10-11）：原 main 闭包无闭包依赖，上移模块级供测试固化。
    """
    try:
        from tdx_read import read_range, table_ready

        df = read_range(
            "ts_daily",
            exec_day,
            d1,
            codes=[code],
            columns=["ts_code", "trade_date", "open", "close"],
        )
        if df is None or len(df) < 1:
            return None
        df = df.sort_values("trade_date")
        opens = df["open"].tolist()
        closes = df["close"].tolist()
        adjs = None
        if table_ready("ts_adj_factor"):
            da = read_range("ts_adj_factor", exec_day, d1, codes=[code])
            if da is not None and len(da):
                am = {str(t)[:8]: a for t, a in zip(da["trade_date"], da["adj_factor"])}
                adjs = [am.get(str(t)[:8]) for t in df["trade_date"]]
        if adjs is not None and all(a is not None for a in adjs):
            buy = float(opens[0]) * float(adjs[0])
            sel = float(closes[-1]) * float(adjs[-1])
            return round((sel / buy - 1) * 100, 2) if buy else None
        # 降级：因子不可用 → 三十一审缺口守卫（-30.5% 只可能是除权，
        # 涨跌幅限制下主板10/创业科创20/北交30；2026 实测 77 起）
        for k in range(1, len(df)):
            prev = float(closes[k - 1])
            if prev and float(opens[k]) / prev - 1 <= -0.305:
                return "exdiv"
        buy = float(opens[0])  # 首次可操作日开盘
        sel = float(closes[-1])  # 窗口终点收盘
        return round((sel / buy - 1) * 100, 2) if buy else None
    except Exception as ex:  # noqa: BLE001
        # A5 分诊：pct_between 是后验收益计算核心——静默 None 会
        # 直接污染 RiskGuard 误杀复算统计（拦截/放行样本收益缺失）——留痕
        print(
            "[verdicts] 个股收益读取失败 code=%s %s~%s：%s"
            % (code, exec_day, d1, str(ex)[:60])
        )
        return None


def main():
    _python_warn()
    sys.path.insert(0, QB)
    from tdx_read import read_day, latest_date  # noqa: E402

    max_archives = _env_int("VERDICTS_MAX_ARCHIVES", 0)
    archive_from = _norm_day(os.environ.get("VERDICTS_ARCHIVE_FROM"))
    archive_to = _norm_day(os.environ.get("VERDICTS_ARCHIVE_TO"))
    progress_every = _env_int("VERDICTS_PROGRESS_EVERY", 25)
    archs = select_archives(
        sorted(glob.glob(os.path.join(HIST_DIR, "todays_*.json"))),
        max_archives=max_archives,
        date_from=archive_from,
        date_to=archive_to,
    )
    if not archs:
        print("[verdicts] 无归档可算（等 build_todays_picks 首份归档；或筛选区间为空）")
        sys.exit(0)
    t_start = time.time()
    print(
        "[verdicts] 待算归档 %d 份 | Python %d.%d | 输出 %s"
        % (len(archs), sys.version_info[0], sys.version_info[1], OUT),
        flush=True,
    )
    today = latest_date("ts_daily")
    if not today:
        print("[verdicts] 行情湖不可达，跳过")
        sys.exit(0)

    # 对照基准（K-STOCK-077 修）：与个股同口径的窗口收益中位数——
    # exec_d 全表开盘 × d1 全表收盘按 ts_code 对齐，中位=(close/open-1)。
    # 旧实现 day_mid(d1) 只取 d1 单日 pct_chg 中位，与个股 w 天累计错配
    # （牛市系统性负超额/熊市正超额），今日审计发现，n=0 未产数前修掉。
    day_cache = {}

    def window_mid(d0, d1):
        # 真窗口中位：全市场 d0 开盘 → d1 收盘（与个股 exec 买入口径一致）
        key = (d0, d1)
        if key not in day_cache:
            try:
                import pandas as pd

                o = read_day("ts_daily", d0, columns=["ts_code", "open"])
                c = read_day("ts_daily", d1, columns=["ts_code", "close"])
                if o is None or c is None or not len(o) or not len(c):
                    day_cache[key] = None
                    return None
                m = pd.merge(
                    o[["ts_code", "open"]], c[["ts_code", "close"]], on="ts_code"
                )
                vals = sorted(
                    (float(cc) / float(oo) - 1) * 100
                    for oo, cc in zip(m["open"], m["close"])
                    if oo and float(oo) > 0 and cc is not None
                )
                day_cache[key] = vals[len(vals) // 2] if vals else None
            except Exception as ex:  # noqa: BLE001
                # A5 分诊：静默 None 会让基准对照整体缺失（裁决静默劣化）——留痕
                print(
                    "[verdicts] 窗口基准中位数读取失败 %s~%s：%s"
                    % (d0, d1, str(ex)[:60])
                )
                day_cache[key] = None
        return day_cache[key]

    # 全市场交易日历：全市场 trade_date 并集。
    # 三十三审修（2026-10-10）：原实现任取湖首行单票全史——首票恰为
    # 000001.SZ（三十年零缺日）纯属 parquet 落盘顺序的运气，与注释声称的
    # 「全市场」语义不符；首票若为中途上市/长期停牌票，日历残缺会让
    # after_days 的 exec_d/d1 静默错位（不崩、不告警、数字看似正常）。
    trade_days = []
    try:
        from tdx_read import read_range

        df_cal = read_range("ts_daily", "20200101", today, columns=["trade_date"])
        trade_days = sorted(
            {
                str(d)[:8]
                for d in (df_cal["trade_date"].tolist() if df_cal is not None else [])
            }
        )
    except Exception as ex:  # noqa: BLE001
        print("[verdicts] 交易日历读取降级：%s" % str(ex)[:60])

    def after_days(sig_day, n):
        """信号日(YYYYMMDD)后第 n 个交易日（严格大于信号日）。"""
        try:
            i = trade_days.index(sig_day)
        except ValueError:
            # 信号日非交易日 → 找首个 >sig 的
            later = [d for d in trade_days if d > sig_day]
            if not later:
                return None
            i = trade_days.index(later[0]) - 1  # 回退一格统一用 index+n
        j = i + n
        return trade_days[j] if j < len(trade_days) else None

    per_code, buckets = {}, {w: [] for w in WINDOWS}
    strat_buckets = {}
    n_arch = 0
    for fp in archs:
        try:
            doc = json.load(open(fp, encoding="utf-8"))
        except Exception as ex:  # noqa: BLE001
            # A5 分诊：坏档静默跳过会让该信号日整批样本消失——留痕
            print(
                "[verdicts] 档案读取失败跳过 %s：%s"
                % (os.path.basename(fp), str(ex)[:60])
            )
            continue
        sig = (doc.get("scan_run_date") or "").replace("-", "")
        if not sig or sig > today:
            continue
        n_arch += 1
        t_arch = time.time()
        n_stock = 0
        for r in doc.get("stocks") or []:
            n_stock += 1
            if progress_every > 0 and n_stock % progress_every == 0:
                # 长跑可观测（P2-1）：档内每 N 票一行，含已耗时/事件数/总进度
                print(
                    "[verdicts] 档 %d/%d %s 进度 %d 票 | 事件 n=%d | 档内 %.0fs | 总 %.0fs"
                    % (
                        n_arch,
                        len(archs),
                        os.path.basename(fp),
                        n_stock,
                        sum(len(v) for v in buckets.values()),
                        time.time() - t_arch,
                        time.time() - t_start,
                    ),
                    flush=True,
                )
            code = _code_to_ts(r.get("code"))
            if not code or "." not in code:
                continue  # 后验口径当前只覆盖 A 股（湖 ts_daily）
            rec = per_code.setdefault(
                code,
                {
                    "name": r.get("name"),
                    "tier": r.get("tier"),
                    "date": sig,
                    "strategies": sorted(
                        {s.get("s") for s in (r.get("sources") or []) if s.get("s")}
                    ),
                    "win": {},
                },
            )
            if sig < rec["date"]:
                continue  # 只保留每票最近一次信号
            if sig > rec["date"]:
                # 九审修：票再次入选（信号日更新）→ 刷新 date/tier/strategies
                # 并重置 win 重算。原实现 win 已有 key 恒 continue，重复
                # 入选的票永远停在首入选的后验（「最近一次信号」语义失效，
                # 汇总事件样本也漏记新入选）。
                # 四十审：name 加 None 守卫——新档缺名（停牌票源 name 空，
                # 实据 603056）时保留旧档名，不倒退清空。
                rec.update(
                    {
                        "name": r.get("name") or rec.get("name"),
                        "tier": r.get("tier"),
                        "date": sig,
                        "win": {},
                        "strategies": sorted(
                            {s.get("s") for s in (r.get("sources") or []) if s.get("s")}
                        ),
                    }
                )
            exec_d = after_days(sig, 2)  # 首次可操作日（画像+1 日产出、+2 开盘可买）
            for w in WINDOWS:
                if w in rec["win"]:
                    continue
                d1 = after_days(exec_d, w) if exec_d else None
                if d1 is None:
                    rec["win"][w] = None  # 窗口未满 → pending
                    continue
                r1 = pct_between(code, exec_d, d1)
                if r1 == "exdiv":
                    # 三十一审：除权窗口 void 样本——不入桶不计 n，
                    # 结构留痕（前端/工具可识别 void 字段），控制台同步留痕。
                    rec["win"][w] = {
                        "r": None,
                        "base": None,
                        "exec": exec_d,
                        "ex": None,
                        "void": "exdiv",
                    }
                    print(
                        "[verdicts] 除权窗口剔除 code=%s win=%s exec=%s"
                        % (code, w, exec_d)
                    )
                    continue
                b1 = window_mid(exec_d, d1)
                rec["win"][w] = {
                    "r": r1,
                    "base": b1,
                    "exec": exec_d,
                    "ex": (
                        round(r1 - b1, 2) if r1 is not None and b1 is not None else None
                    ),
                }
                if r1 is not None:
                    buckets[w].append(r1)
                    # 二十五审（方案A 2026-10-10）：策略分层只收可操作层
                    # （T1/T2）事件，tier 取本归档行 r 自身（rec["tier"] 会被
                    # 后续归档覆写成最新信号，事件级语义才对）。全 tier 分母
                    # 混入卖点票/反向参考/无观点影子（chanlun 桶 49% T3 且
                    # 买41/卖49 混算），读作「策略成绩」必然误导。整体
                    # buckets（summary.windows）保持全池口径不变。
                    if r.get("tier") in ("T1", "T2"):
                        # 三十六审（2026-10-10）：桶源白名单=看多源（strong/
                        # chanlun/qpzm/cross，与 tier_of bull 源+cross 旁路
                        # 对齐）。weak（反向参考）与 ninedim（low/na 影子
                        # 无观点）不是入池原因，混入桶会把「恰好被覆盖的
                        # 共振票成绩」记成该策略成绩——1009 实证 ninedim+
                        # strong 组合 45 只将全数误进 ninedim 桶，且 1 只
                        # weak 标记票混进 weak 桶（负面源卡收看多票成绩）。
                        for s in rec["strategies"]:
                            if s in ("strong", "chanlun", "qpzm", "cross"):
                                strat_buckets.setdefault(s, {}).setdefault(
                                    w, []
                                ).append(r1)
        print(
            "[verdicts] 档 %d/%d %s 完成 | 票 %d | 事件 n=%d | 档内 %.1fs | 总 %.1fs"
            % (
                n_arch,
                len(archs),
                os.path.basename(fp),
                n_stock,
                sum(len(v) for v in buckets.values()),
                time.time() - t_arch,
                time.time() - t_start,
            ),
            flush=True,
        )

    # 汇总
    def blk(vals, w):
        if not vals:
            return {"n": 0, "hit": None, "avg": None}
        vals_s = sorted(vals)
        # 三十三审：删除恒等死字段 win_rt（与 hit 同公式同值，全库无消费
        # 方——picks.html/控制台只用 hit，双字段并列易误读为两个口径）
        return {
            "n": len(vals),
            "hit": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
            "avg": round(sum(vals) / len(vals), 2),
            "med": vals_s[len(vals_s) // 2],
        }

    summary = {
        "asof": today,
        "archives": n_arch,
        # K-STOCK-070 口径自证：可成交口径（九审）自 2026-10-10 起生效，
        # 与此前 D 收盘起算的旧口径数字不可直接比（旧口径对强势票系统性虚高）
        "caliber": {
            "id": "exec-d2-open-20261010",
            "buy": "信号日后第2个交易日开盘（清单 D+1 15:10 产出、D+2 开盘可操作）",
            "sell": "首买日后 1/3/5 个交易日收盘",
            # 复权口径（2026-10-11 消费端升级，ts_adj_factor 入湖）：个股
            # 收益因子齐备时按 open×adj→close×adj（除权自动修正、样本
            # 不 void）；因子不可用降级 -30.5% 缺口守卫 void 剔除。升级
            # 发生在 n=0 未产数前，无新旧数字断裂，caliber.id 不换代。
            "return": "个股复权收益（2026-10-11 起）：open×adj→close×adj，除权窗口修正保留；降级时缺口守卫 void 剔除",
            "benchmark": "同窗口全市场开盘-收盘中位数（d0 开盘×d1 收盘，与个股买入口径对齐；横截面中位对尾部除权缺口免疫，不复权）",
            "supersedes": "close-d0（2026-10-10 前，不可比）",
        },
        "windows": {str(w): blk(buckets[w], w) for w in WINDOWS},
        # 二十五审（方案A）：分层口径自证——by_strategy 只收 T1/T2 事件
        # （可操作层，分母不含 T3/CROWD 影子），windows 为全池口径，勿混比
        # 三十六审：桶源限看多源白名单；多源事件跨桶计入（n 勿跨策略加总，
        # 1009 档 T1/T2 加总放大 1.33x）
        "by_strategy_caliber": "仅 T1/T2 入池事件×看多源白名单（strong/chanlun/"
        "qpzm/cross；weak/ninedim 非入池原因不计桶）；"
        "多源事件跨桶计入，n 勿跨策略加总；windows 为全池口径",
        "by_strategy": {
            s: {str(w): blk(b.get(w) or [], w) for w in WINDOWS}
            for s, b in sorted(strat_buckets.items())
        },
    }
    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "per_code": per_code,
    }
    # 分片/回归产物自证（仅筛选生效时加字段；全量生产输出结构不变）
    if max_archives or archive_from or archive_to:
        out["select"] = {
            "max_archives": max_archives,
            "archive_from": archive_from,
            "archive_to": archive_to,
            "archives": n_arch,
            "archive_files": [os.path.basename(fp) for fp in archs],
        }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # K-STOCK-393（20261015 原子写补齐）：api_misc 请求路径并发读此
    # 文件，直写可被读到半截 JSON（sync_publish 判例：tmp+os.replace）
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    print(
        "[verdicts] 归档 %d 份 | 覆盖 %d 票 | asof %s | 输出 %s"
        % (n_arch, len(per_code), today, OUT),
        flush=True,
    )
    for w in WINDOWS:
        b = summary["windows"][str(w)]
        print(
            "  execT+%d(首买=信号+2开盘): n=%s 命中率=%s%% 均值=%s%%"
            % (w, b["n"], b["hit"], b["avg"]),
            flush=True,
        )


if __name__ == "__main__":
    main()
