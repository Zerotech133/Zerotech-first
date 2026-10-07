# -*- coding: utf-8 -*-
"""A 股证券代码归一（单一实现，P1-2）。

基准：chanlun_data._norm 的 CN 6 位分支后缀判定：
  已带前缀/后缀（600519.SH / SH600519 等）→ 规范为大写「600519.SH」形式；
  92 / 8 / 4 开头 → .BJ（北交所；旧散落实现错标 .SZ，本模块为修复点）；
  0 / 3 开头 → .SZ（深市股票/创业板）；
  其余 6 位（6/5/9 等：沪市股票/基金/B股）→ .SH。

替代 weak_track_check / badnews_lookup / fund_lookup / nd_lookup /
sector_rank / style_lookup / turn_lookup / weak_track_close /
weak_track_newcase 各自重复的 _norm 副本（P1-2）。
"""
import re

_PREFIX = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_SUFFIX = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")


def normalize_code(raw):
    """多形态 A 股代码 → '600519.SH' 形式；无法识别返回 None。"""
    u = str(raw or "").strip().upper()
    if not u:
        return None
    m = _PREFIX.match(u)
    if m:
        return m.group(2) + "." + m.group(1)
    if _SUFFIX.match(u):
        return u
    if re.match(r"^\d{6}$", u):
        if u.startswith("92") or u[0] in "48":
            suf = "BJ"
        elif u[0] in "03":
            suf = "SZ"
        else:
            suf = "SH"
        return u + "." + suf
    return None
