# -*- coding: utf-8 -*-
"""本地 TDX 日线源盘后下载转正（雪球行情替代源第三层兜底）。

背景：tushare 主源 > akshare 备援 > 本地 TDX。vipdoc/.day 当前全空，
本脚本用 pytdx 从公共行情服务器拉日线，落盘为与
tools/xq_quote_alt_probe.parse_tdx_day 完全兼容的 32B .day 文件，
写 REPO/data/tdx/{sh,sz}/（不走 TDX 安装目录，避免污染）。

32B 记录布局（小端，与 parse_tdx_day 的 <IIIIIfII 一致）：
  date(u32 YYYYMMDD) open/high/low/close(i32, 分=价*100)
  amount(f32 元) vol(u32 股) 预留(u32)

vol 口径：pytdx get_security_bars 返回的 vol 单位为手，×100 换算为股
（茅台日量 ~3 万手 ≈ 300 万股量级，落盘前已在验证步骤对照确认）。

用法：
  python tools/xq_tdx_fetch.py                       # 默认三样标的 250 根
  python tools/xq_tdx_fetch.py --symbols SH600519,SZ000001 --days 250
  python tools/xq_tdx_fetch.py --out data/tdx        # 显式输出根

fail-visible：全部行情服务器连接失败 → stderr 明确报错 + 退出码 1，
绝不静默产出空文件。代理变量在启动时清空（判例：代理造成假 403/假 502）。
"""

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path
import logging

HERE = Path(__file__).resolve().parent  # tools/
REPO = HERE.parent  # 仓库根
DEFAULT_OUT = REPO / "data" / "tdx"

# 与 tools/xq_quote_alt_probe.py 的 _TDX_DAY 完全一致（32 字节小端）
_TDX_DAY = struct.Struct("<IIIIIfII")
DAY_SIZE = _TDX_DAY.size  # 32

# pytdx 日K category：9 = 日线
KLINE_DAY = 9

# 公共行情服务器标准池（连接失败自动换下一个）
HQ_SERVERS = [
    ("119.147.212.81", 7709),
    ("115.238.90.165", 7709),
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("202.108.25.136", 7709),
    ("114.80.63.12", 7709),
    ("114.80.63.35", 7709),
    ("218.108.98.244", 7709),
    ("218.108.47.69", 7709),
    ("180.153.39.51", 7709),
]

DEFAULT_SYMBOLS = "SH600519,SZ000001,SH000001"

RUN_DIR = Path(r"C:/aios/automation_runs/xq_tdx")


# ── 符号解析 ─────────────────────────────────────────────────


def parse_symbol(sym):
    """'SH600519' → ('sh600519', market=1, '600519')；SZ → market=0。

    指数同理：'SH000001' → market=1 code='000001'（上证指数）。
    """
    s = sym.strip().lower()
    if len(s) != 8 or s[:2] not in ("sh", "sz") or not s[2:].isdigit():
        raise ValueError("非法符号 %r，应为 SH/SZ + 6 位数字（如 SH600519）" % sym)
    return s, (1 if s.startswith("sh") else 0), s[2:]


# ── 32B 编码（与 parse_tdx_day 严格 roundtrip 兼容）──────────


def encode_day(date_int, o, h, l, c, amount, vol):
    """一条 dict bar → 32B。date_int 为 YYYYMMDD；价格为元，×100 存分。"""
    return _TDX_DAY.pack(
        int(date_int),
        int(round(float(o) * 100)),
        int(round(float(h) * 100)),
        int(round(float(l) * 100)),
        int(round(float(c) * 100)),
        float(amount),
        int(vol),
        0,
    )


def write_day_file(path, recs):
    """recs（已升序）逐条编码追加落盘；先写临时文件再原子替换。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".day.tmp")
    with open(tmp, "wb") as f:
        for r in recs:
            f.write(encode_day(*r))
    os.replace(tmp, path)
    return path


# ── 网络层（可注入 mock）─────────────────────────────────────


def fetch_bars(api, market, code, days, page=100, category=KLINE_DAY):
    """分页拉日线，返回最近 days 根（date 升序、去重）。

    api 只需实现 get_security_bars(category, market, code, start, count)
    （pytdx TdxHq_API 或测试 mock）。start=0 为最新一页，逐页取够为止。
    返回 [(date_int, open, high, low, close, amount, vol_hand), ...]，
    vol 为 pytdx 原始口径（手），×100 换股在 encode/write 侧做。
    """
    raw = []
    start = 0
    while len(raw) < days:
        chunk = api.get_security_bars(category, market, code, start, page)
        if not chunk:
            break
        raw.extend(chunk)
        if len(chunk) < page:  # 末页取尽
            break
        start += page
    return normalize_bars(raw)[-int(days) :]


def normalize_bars(raw):
    """pytdx bar dict → 升序去重 [(date, o, h, l, c, amount, vol)]。"""
    seen = {}
    for b in raw or []:
        di = _bar_date(b)
        if di is None:
            continue
        seen[di] = (
            di,
            b["open"],
            b["high"],
            b["low"],
            b["close"],
            float(b.get("amount") or 0.0),
            b.get("vol") or 0,
        )
    return [seen[k] for k in sorted(seen)]


def _bar_date(b):
    """pytdx bar 的日期：优先 year/month/day int 字段，退回 datetime 串。"""
    if b.get("year"):
        return b["year"] * 10000 + b["month"] * 100 + b["day"]
    dt = str(b.get("datetime", ""))[:10].replace("-", "")
    return int(dt) if dt.isdigit() else None


def connect_any(api_cls, servers=None, timeout=5):
    """遍历服务器池，返回 (api, (ip, port))；全失败抛 ConnectionError。"""
    last = []
    for ip, port in servers or HQ_SERVERS:
        api = api_cls(heartbeat=False)
        try:
            try:
                ok = api.connect(ip, port, time_out=timeout)
            except TypeError:  # 老版本签名无 time_out
                ok = api.connect(ip, port)
            if ok:
                return api, (ip, port)
            last.append("%s:%s 连接返回失败" % (ip, port))
        except Exception as e:  # noqa: BLE001
            last.append("%s:%s %s" % (ip, port, str(e)[:80]))
        try:
            api.disconnect()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_tdx_fetch.py", exc)
    raise ConnectionError(
        "全部 %d 个行情服务器连接失败：%s"
        % (len(servers or HQ_SERVERS), "；".join(last))
    )


# ── 主流程 ───────────────────────────────────────────────────


def run(symbols, days, out_root, page=100, timeout=5, verbose=True):
    """拉全部符号落盘。返回 (written, errors)；written 为空时调用方退出 1。"""
    written, errors = {}, []
    api = None
    peer = None
    for sym in symbols:
        try:
            name, market, code = parse_symbol(sym)
            if api is None:
                from pytdx.hq import TdxHq_API  # noqa: 延迟导入

                api, peer = connect_any(TdxHq_API, timeout=timeout)
                _log("已连接行情服务器 %s:%s" % peer, verbose)
            bars = fetch_bars(api, market, code, days, page=page)
            if not bars:
                raise ValueError("日线拉取为空")
            recs = [
                (d, o, h, l, c, amt, int(round(v * 100)))  # 手→股
                for (d, o, h, l, c, amt, v) in bars
            ]
            f = write_day_file(
                Path(out_root) / name[:2] / "lday" / (name + ".day"), recs
            )
            written[name] = str(f)
            _log(
                "%s: %d 根 → %s（最新 %s）"
                % (name, len(recs), f, _fmt_date(recs[-1][0])),
                verbose,
            )
        except Exception as e:  # noqa: BLE001
            errors.append("%s: %s" % (sym.strip().upper(), str(e)[:120]))
            _log("%s: 失败 %s" % (sym.strip().upper(), str(e)[:120]), verbose)
    if api is not None:
        try:
            api.disconnect()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_tdx_fetch.py", exc)
    return written, errors


def _fmt_date(d):
    return "%08d" % d


def _log(msg, verbose=True):
    if verbose:
        print(msg, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="TDX 日线盘后下载（pytdx → data/tdx）")
    ap.add_argument(
        "--symbols",
        default=DEFAULT_SYMBOLS,
        help="逗号分隔，如 SH600519,SZ000001,SH000001",
    )
    ap.add_argument("--days", type=int, default=250, help="每标的拉最近 N 根")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=".day 输出根目录")
    ap.add_argument("--page", type=int, default=100, help="单页 bar 数")
    ap.add_argument("--timeout", type=int, default=5, help="连接超时秒")
    args = ap.parse_args(argv)

    # 判例：清代理变量（代理对部分源返假 403/假 502）
    for k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        os.environ.pop(k, None)

    symbols = [s for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("错误：--symbols 为空", file=sys.stderr)
        return 1
    t0 = time.time()
    written, errors = run(
        symbols, args.days, args.out, page=args.page, timeout=args.timeout
    )
    summary = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 2),
        "written": written,
        "errors": errors,
    }
    try:  # 长跑输出归档（尽力而为）
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        (RUN_DIR / "fetch_last.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_tdx_fetch.py", exc)
    if not written:
        print(
            "错误：零符号落盘（fail-visible）→ %s" % "；".join(errors), file=sys.stderr
        )
        return 1
    if errors:
        print("警告：部分符号失败 → %s" % "；".join(errors), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
