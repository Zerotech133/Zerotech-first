# -*- coding: utf-8 -*-
"""2026-11-11 ST 源 4 个失败修复：测试夹具补齐 A4 收口后的新依赖。

根因（不是实现 BUG，是夹具滞后）：
  2026-11-10 A4 收口把 calc_chanlun_signals_market 的 buy_filter 解析改为
  强制经 _resolve_buy_filter（禁止静默回落，非法值 fail-fast）。测试的
  AST 隔离提取只注入 BUY_FILTERS/BUY_FILTER_LABEL 等旧名，没注入新函数，
  NameError 直接炸在第一行——测试从此没有真正测到 hist_st 逻辑。

  另一个方向相反：test_a3_stop_parameter_boundary 的 1 个失败是**引擎行为
  有意变更**——2026-11-10 缠论分支接入硬止损（stop_pct 只在双刀分支被读取
  属于「参数传了没生效」第 4 例）。该测试 10-21 拍的「两符号都忽略止损」
  契约已过期，测试期望需要跟上新契约（负值=止损生效）。

修复原则：
  * 夹具注入修复 = 恢复测试本来的覆盖面，不改产线代码；
  * 断言更新 = 按新契约重写期望（有引擎提交 e4fe3431b/5a28d6fa2 佐证），
    并在测试里留契约变更出处，防止下次误当回归。
锚点 count==1 前置断言 + 替换后读回断言；文件字节换行格式按原样（LF）。
"""
from pathlib import Path

CRLF = chr(13) + chr(10)


def rep(fname, old_lines, new_lines, label):
    p = Path(fname)
    b = p.read_bytes()
    # 该测试文件为 CRLF：按字节拼接（列表项之间一个换行，末尾不带）
    def _join(lines):
        parts = [
            (x if isinstance(x, bytes) else str(x).encode("utf-8")) for x in lines
        ]
        return CRLF.encode("utf-8").join(parts)

    old = _join(old_lines)
    new = _join(new_lines)
    if new in b:
        print(label, "SKIP (already applied)")
        return
    cnt = b.count(old)
    assert cnt == 1, label + " anchor count=" + str(cnt)
    p.write_bytes(b.replace(old, new, 1))
    back = p.read_bytes()
    assert new in back, label + " verify failed"
    print(label, "OK")




# ── 修复 ①：st_source_visibility 夹具注入 _resolve_buy_filter ──
rep(
    "tests/test_a3_st_source_visibility_readonly.py",
    [
        '    calc = _function_from_source("backtest_engine_v2.py", "calc_chanlun_signals_market", {',
        '        "os": os,',
        '        "json": json,',
        '        "BASE": str(tmp_path),',
        '        "cal_idx_all": ["20260105", "20260110", "20260113"],',
        '        "BUY_FILTERS": {"3": {"3买"}},',
        '        "BUY_FILTER_LABEL": {"3": "仅三买"},',
        '        "SELL_TYPES": {"1卖", "2卖", "3卖"},',
        '    })',
    ],
    [
        '    buy_filters = {"3": {"3买"}}',
        '    calc = _function_from_source("backtest_engine_v2.py", "calc_chanlun_signals_market", {',
        '        "os": os,',
        '        "json": json,',
        '        "BASE": str(tmp_path),',
        '        "cal_idx_all": ["20260105", "20260110", "20260113"],',
        '        "BUY_FILTERS": buy_filters,',
        '        "BUY_FILTER_LABEL": {"3": "仅三买"},',
        '        "SELL_TYPES": {"1卖", "2卖", "3卖"},',
        '        # 2026-11-10 A4 收口：buy_filter 改经 _resolve_buy_filter 强解析',
        '        # （禁止静默回落）。夹具缺它 → NameError，hist_st 分支从未被测到',
        '        # （2026-11-11 修复）。测试只用合法值 "3"，永不触发 ValueError。',
        '        "_resolve_buy_filter": buy_filters.get,',
        '    })',
    ],
    "st-visibility-inject-resolver",
)


# ── 修复 ②：hist_st_lookahead 同样注入（该文件也是 CRLF）──
import re as _re

p = Path("tests/test_a3_hist_st_lookahead_readonly.py")
src = p.read_bytes()

pat = '"BUY_FILTERS": {"3": {"3买"}},'.encode("utf-8")
m = list(_re.finditer(_re.escape(pat), src))

assert len(m) == 1, "hist BUY_FILTERS anchor count=" + str(len(m))
if b'"_resolve_buy_filter"' in src:
    print("hist-lookahead-inject SKIP (already applied)")
else:
    old_line = m[0].group(0)
    nl = CRLF.encode("utf-8")

    new_line = (old_line + nl
                + '        "_resolve_buy_filter": {"3": {"3买"}}.get,'.encode("utf-8") + nl
                + '        # 2026-11-11：A4 收口后 buy_filter 走 _resolve_buy_filter，'
                  '夹具缺它 → NameError（hist_st 分支从未被测到）'.encode("utf-8"))
    p.write_bytes(src.replace(old_line, new_line, 1))
    assert b'"_resolve_buy_filter"' in p.read_bytes()
    print("hist-lookahead-inject OK")


