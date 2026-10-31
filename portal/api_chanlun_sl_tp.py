# -*- coding: utf-8 -*-
"""K78/K78b 缠论止损止盈预测与自动叠加线（Handler Mixin 拆出）。

2026-10-31 WO-A5FROZEN（K-ORG-20261031-A5FROZEN）从 api_chanlun_pages.py 原样
搬出，仅做位置迁移：方法名/签名/行为不变（Handler 多继承并入同一实例）。
"""
import logging


class _ChanlunSlTpMixin:
    @staticmethod
    def _chanlun_sl_tp(code):
        """缠论多级别联动预测口径（K78）：
        止损看日线结构（现价>日线上枢 zg → 3买结构，跌回即失效；
        否则取最近笔低点=笔破坏），止盈看 30 分钟结构（30m 中枢
        等幅测量：tp1=zg+高、tp2=zg+2×高；现价在 30m 中枢内时
        目标=上沿 zg）。返回两级别组合，体现"大级别定性、
        小级别定量"。"""
        try:
            import sys as _s
            if r'C:\项目\老田的AI助手\quant_backtest' not in _s.path:
                _s.path.insert(0, r'C:\项目\老田的AI助手\quant_backtest')
            # 前缀归一：北交所 92 开头码强制 bj（load_kline 不认 sz920950）
            import re as _re6
            _m6 = _re6.fullmatch(r"(sh|sz|bj)(\d{6})", str(code or ""),
                                 _re6.I)
            if _m6:
                code = ("bj" if _m6.group(2).startswith("92")
                        else _m6.group(1).lower()) + _m6.group(2)
            import importlib as _il
            _cp = _il.import_module("chanlun_plot")
            _cb = _il.import_module("chanlun_bi")

            def _struct(period):
                r = _cp.CD.load_kline(code, period, 500)
                if not r.get("ok") or len(r.get("bars", [])) < 120:
                    return None
                bars = r["bars"]
                st = _cb.analyze(bars)
                piv = st.get("pivots") or []
                if not piv:
                    return None
                return bars, piv, st.get("bis") or []

            out = {"px": None}
            d = _struct("1d")
            if not d:
                return None
            bars, piv, bis = d
            last_px = float(bars[-1]["c"])
            out["px"] = round(last_px, 2)
            # 止损：日线级别（优先取现价参与的中枢：zd < px）
            dc = [p for p in piv if float(p["zd"]) < last_px] or piv
            dpv = dc[-1]
            out["dzg"], out["dzd"] = round(float(dpv["zg"]), 2), \
                round(float(dpv["zd"]), 2)
            if last_px > out["dzg"]:
                out["stop"] = out["dzg"]
                out["basis"] = "日线3买结构：跌回中枢上沿即失效"
            else:
                lows = [float(b["s_price"]) if b["dir"] == 1
                        else float(b["e_price"]) for b in bis[-5:]]
                under = [x for x in lows if x < last_px]
                out["stop"] = round(max(under) if under else min(lows), 2)
                out["basis"] = "中枢内：最近笔低点（笔破坏止损）"
            # 止盈：30 分钟级别（小级别定量）
            m = _struct("30m")
            if m:
                _mb, mpv, _ = m[0], m[1][-1], m[2]
                mzg, mzd = float(mpv["zg"]), float(mpv["zd"])
                mh = mzg - mzd
                out["mzg"], out["mzd"] = round(mzg, 2), round(mzd, 2)
                if last_px > mzg:            # 30m 3买：中枢等幅外推
                    out["tp1"] = round(mzg + mh, 2)
                    out["tp2"] = round(mzg + 2 * mh, 2)
                    out["tpb"] = "30m中枢 %.2f-%.2f 等幅外推" % (mzd, mzg)
                else:                        # 30m 中枢内：目标=上沿突破位
                    out["tp1"] = round(mzg, 2)
                    out["tp2"] = round(max(mzg + mh, mzg * 1.03), 2)
                    out["tpb"] = "30m中枢 %.2f-%.2f：站稳上沿后看等幅" \
                        % (mzd, mzg)
            else:
                # 30m 缺数据退化：日线中枢等幅
                dh = out["dzg"] - out["dzd"]
                out["tp1"] = round(out["dzg"] + dh, 2)
                out["tp2"] = round(out["dzg"] + 2 * dh, 2)
                out["tpb"] = "日线中枢等幅外推"
            return out
        except Exception:
            return None

    def _chanlun_auto_lines(self, code):
        """K78b: 信号票自动叠加线（入场/止损/目标）——无手工标注时用。

        code 为归一前缀格式（sz301373），池内为 ts_code（301373.SZ）。"""
        try:
            import re as _re5
            m5 = _re5.fullmatch(r"(sh|sz|bj)(\d{6})", str(code or ""),
                                _re5.I)
            ts = ("%s.%s" % (m5.group(2), m5.group(1).upper())) if m5 \
                else str(code or "")
            import sys as _sys
            if r'C:\项目\老田的AI助手\quant_backtest' not in _sys.path:
                _sys.path.insert(0, r'C:\项目\老田的AI助手\quant_backtest')
            import importlib as _il
            S = _il.import_module("chanlun_scan")
            row = None
            for r in S._load_pool():
                if str(r.get("code") or "") == ts:
                    row = r
                    break
            if not row:
                return []
            ovs = []
            try:
                ev = float(row.get("entry_px"))
                if ev > 0:
                    ovs.append((ev, "入场", "#3fb950"))
            except (TypeError, ValueError) as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
            stp = self._chanlun_sl_tp(code)
            if stp:
                ovs.append((stp["stop"], "止损", "#f85149"))
                ovs.append((stp["tp1"], "目标1", "#58a6ff"))
                ovs.append((stp["tp2"], "目标2", "#58a6ff"))
            return ovs
        except Exception:
            return []
