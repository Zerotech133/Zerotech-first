"""只读绩效账本投影（K-STOCK-132）。

账本由 ``tools/build_performance_ledger.py`` 生成；本模块只读取并做
结构校验，不计算收益、不写文件，也不提供下单或雇佣接口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_LEDGER = Path(__file__).with_name("data") / "performance_ledger.json"
REQUIRED_FIELDS = (
    "generated_at",
    "asof",
    "caliber",
    "totals",
    "per_tier",
    "per_strategy",
)


def _read_ledger(path: str | Path) -> dict[str, Any]:
    ledger_path = Path(path)
    if not ledger_path.is_file():
        raise FileNotFoundError(str(ledger_path))
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("绩效账本不是有效 UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("绩效账本根节点必须是对象")
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError("绩效账本缺少字段：" + ",".join(missing))
    for field in ("caliber", "totals", "per_tier", "per_strategy"):
        if not isinstance(data[field], dict):
            raise ValueError("绩效账本字段必须是对象：" + field)
    return data


def project(path: str | Path = DEFAULT_LEDGER) -> dict[str, Any]:
    """返回门户使用的只读投影。"""
    data = _read_ledger(path)
    return {
        "ok": True,
        "schema_version": "market-performance-v0.1",
        "read_only": True,
        "financial_execution_enabled": False,
        "generated_at": data["generated_at"],
        "asof": data["asof"],
        "caliber": data["caliber"],
        "totals": data["totals"],
        "per_tier": data["per_tier"],
        "per_strategy": data["per_strategy"],
        "note": data.get("_note", "绩效账本仅供研究复盘，不构成交易指令。"),
    }


if __name__ == "__main__":
    print(json.dumps(project(), ensure_ascii=False, indent=2))
