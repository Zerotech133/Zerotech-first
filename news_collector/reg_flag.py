# -*- coding: utf-8 -*-
"""桥文件开关的唯一取值入口：一律从权威注册表读，生成器里不写死。

判例（2026-11-13 复核 R27 G3）
-----------------------------
`run_cohorts_intel.py` / `run_houfeng_intel.py` 每日重生成 `target_queue_*.json`
时把 `xq_include_cohorts` / `xq_include_candidates` 写回 `false`，而
`selection_universe.json` 早在 11-11 R22 就改成了 `true`。于是桥文件长期与权威相反，
`tools/registry_guard.py` 每天报 DRIFT。

后果不是功能问题（`xq_stock_timeline.load_queue` 只读注册表），而是**误导**：
后来的人或另一条线看到桥文件里的 `false`，会以为扩源没开，审计时反复对账。

修法取守卫给出的建议①：**写入 registry 值**。这样桥文件与注册表恒同源；
某天若真要把开关关掉，桥文件会立刻跟着变，不会再出现第二种说法。

注意
----
本模块**只读不写**注册表；注册表本身仍由人工/裁定流程维护。
任何异常（缺文件、坏 JSON、缺字段）一律回退 `default`，绝不阻断桥生成——
桥生成器是日更主链，不能因为一个说明字段把自己搞挂。
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REG_F = os.path.join(HERE, "selection_universe.json")


def xq_include(source_key, default=False, reg_path=None):
    """读取 `sources[source_key].xq_include`。缺失/异常 → default。"""
    try:
        with io.open(reg_path or REG_F, "r", encoding="utf-8") as f:
            reg = json.load(f)
        src = (reg.get("sources") or {}).get(source_key) or {}
        if "xq_include" in src:
            return bool(src["xq_include"])
    except Exception:  # noqa: BLE001 - 说明字段不值得阻断主链
        pass
    return bool(default)


def xq_note(source_key, reg_path=None):
    """给桥文件的说明文本用：把「当前开/关」说清楚，避免又一处硬编码。"""
    on = xq_include(source_key, reg_path=reg_path)
    return ("已并入 07:00 有头管线" if on else "仅登记未并入 07:00 有头管线")
