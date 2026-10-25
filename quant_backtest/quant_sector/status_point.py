# -*- coding: utf-8 -*-
"""OSLINK-W2 量化板块 StatusPoint 落盘契约（2026-10-23，隔离树原型·待签）。

背景（C1/C3 共同根因）：
  QS1（司马懿）在 org.json 登记为 QUANT_SECTOR 接收身份，但 llm_binding=none、
  无运行时写入者；其上游 ACK 台账（upstream_inbox.UpstreamInbox）为进程内内存，
  跨进程不可见。后果：唯一主 OS 取不到 QS1 的真实状态，「状态上屏」只能停留在
  演示值或空态——不落盘，则 OS 上屏永远是演示值。

本模块定义落盘契约的**最小可实现子集**（G0-G2 隔离范围）：
  - 单一写入者：QS1 运行时（当前不存在 → 诚实 no_writer 态，不造数）。
  - 原子写：tmp + os.replace，读者永不会读到半条记录。
  - 读者只读归集，不造第二本账：state 词表仅
    no_source / no_writer / active / stale，绝不回填零值或演示值。
  - 缺源负例：文件缺失、损坏、过期都如实报状态与原因。

字段语义（与 OSResultContract 对齐但不混用——那是"结果证据"词表，
这是"席位状态"词表，两本账不合并）：
  schema          契约版本标识
  writer          写入席位 ID（唯一写者；QS1 运行时）
  state           no_writer | active（由写入者声明，读者可降级为 stale）
  as_of           状态观测时点（带时区 ISO8601，不得晚于写时点）
  source_ref      证据定位（active 态必填；如上游台账路径/报告 ID）
  trace_id        关联编号（可选，供跨链关联）
  detail          人类可读说明（可选）
  receipt         复核回执（可选；None 表示未经独立复核）

诚实边界：
  - 本模块不启动任何服务、不发送任何消息、不读取市场数据。
  - emit_current() 只写"当前诚实状态"（无运行时写入者），不虚构 QS1 活动。
  - 读者对任何异常（缺失/损坏/半条）都显式报 state+reason，不静默回落空态。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA = "quant-sector-status-point/v1"

# 读者状态词表（全量，不得扩展出"通过/正常"之类结论性取值）
STATE_NO_SOURCE = "no_source"    # 文件缺失或不可读（不是"没有事"，是"没有证据"）
STATE_NO_WRITER = "no_writer"    # 文件在，但写入者声明当前无运行时
STATE_ACTIVE = "active"          # 有新近状态点
STATE_STALE = "stale"            # 状态点过期（读者降级，不修改原文件）

# active 态的新鲜度阈值（秒）；过期由读者判 stale，写入者不因此改数
STALE_AFTER_SEC = int(os.environ.get("OSLINK_QUANT_STATUS_STALE_SEC", "900"))

# 默认落点（运行时状态文件，不入 VCS；测试经 OSLINK_QUANT_STATUS_PATH 注入）
DEFAULT_PATH = Path(
    os.environ.get(
        "OSLINK_QUANT_STATUS_PATH",
        str(Path(__file__).resolve().parents[2]
            / "agent_platform" / "data" / "quant_sector" / "status_point.json"),
    )
)

_REQUIRED_WRITE = ("schema", "writer", "state", "as_of")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(text: Any) -> Optional[datetime]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def read_status_point(path: Optional[Path] = None) -> Dict[str, Any]:
    """只读归集量化板块状态点。任何异常都显式报 state+reason，不静默回落。"""
    target = Path(path) if path else DEFAULT_PATH
    if not target.exists():
        return {"state": STATE_NO_SOURCE, "point": None,
                "reason": "status_point 文件缺失（%s）：无证据，不得推断为无事或已通" % target,
                "path": str(target)}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return {"state": STATE_NO_SOURCE, "point": None,
                "reason": "status_point 不可读（%s：%s）：按无证据处理，不猜测内容"
                          % (type(exc).__name__, exc),
                "path": str(target)}
    if not isinstance(raw, dict):
        return {"state": STATE_NO_SOURCE, "point": None,
                "reason": "status_point 顶层不是对象：结构不符契约，按无证据处理",
                "path": str(target)}
    if raw.get("state") == STATE_NO_WRITER:
        return {"state": STATE_NO_WRITER, "point": raw,
                "reason": raw.get("detail") or "写入者声明当前无运行时",
                "path": str(target)}
    if raw.get("state") != STATE_ACTIVE:
        return {"state": STATE_NO_SOURCE, "point": raw,
                "reason": "state 取值不在契约词表内（%r）：按无证据处理" % raw.get("state"),
                "path": str(target)}
    as_of = _parse_iso(raw.get("as_of"))
    if as_of is None:
        return {"state": STATE_NO_SOURCE, "point": raw,
                "reason": "as_of 缺失或不可解析：active 态必须带可观测时点",
                "path": str(target)}
    if not raw.get("source_ref"):
        return {"state": STATE_NO_SOURCE, "point": raw,
                "reason": "active 态缺 source_ref：无证据定位，不得视为有源状态",
                "path": str(target)}
    age = (_utc_now() - as_of).total_seconds()
    if age > STALE_AFTER_SEC:
        return {"state": STATE_STALE, "point": raw,
                "reason": "状态点已 %d 秒未更新（阈值 %d）：读者降级为 stale，不改原文件"
                          % (int(age), STALE_AFTER_SEC),
                "path": str(target), "age_sec": int(age)}
    return {"state": STATE_ACTIVE, "point": raw,
            "reason": "状态点新近（%d 秒内）" % int(max(age, 0)),
            "path": str(target), "age_sec": int(max(age, 0))}


def write_status_point(point: Dict[str, Any],
                       path: Optional[Path] = None) -> Path:
    """原子写入状态点（tmp + os.replace）。校验必填字段，缺项即拒写。"""
    target = Path(path) if path else DEFAULT_PATH
    payload = dict(point)
    payload.setdefault("schema", SCHEMA)
    missing = [k for k in _REQUIRED_WRITE if not payload.get(k)]
    if missing:
        raise ValueError("status_point 缺必填字段：%s" % "/".join(missing))
    if payload["state"] not in (STATE_NO_WRITER, STATE_ACTIVE):
        raise ValueError("status_point.state 必须是 %s 或 %s"
                         % (STATE_NO_WRITER, STATE_ACTIVE))
    as_of = _parse_iso(payload.get("as_of"))
    if as_of is None:
        raise ValueError("status_point.as_of 必须是带时区 ISO8601")
    if as_of > _utc_now():
        raise ValueError("status_point.as_of 晚于当前时刻：拒绝预填未来时点")
    if payload["state"] == STATE_ACTIVE and not payload.get("source_ref"):
        raise ValueError("active 态必须带 source_ref（证据定位）")
    payload["generated_at"] = _iso(_utc_now())
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix=".status_point.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def emit_current(path: Optional[Path] = None) -> Path:
    """写"当前诚实状态"：QS1 无运行时写入者。

    不虚构 QS1 活动、不读取市场数据、不冒充任何席位签字——只把
    "没有运行时写入者"这一事实落成可被 OS 只读归集的显式状态。
    """
    point = {
        "schema": SCHEMA,
        "writer": "QS1",
        "state": STATE_NO_WRITER,
        "as_of": _iso(_utc_now()),
        "source_ref": "agent_platform/registry/org.json#QS1.llm_binding=none",
        "trace_id": None,
        "receipt": None,
        "detail": ("QS1 当前无运行时写入者：org.json 登记 llm_binding=none，"
                   "上游 ACK 台账为进程内内存（upstream_inbox.UpstreamInbox），"
                   "跨进程不可见。本状态点由隔离树原型 emit_current 生成，"
                   "待 QS1 运行时或经签认的代理写者接管；未签认前不得据此"
                   "声称 QS1 有任何活动。"),
    }
    return write_status_point(point, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="量化板块 StatusPoint 落盘契约（隔离树原型·待签）")
    parser.add_argument("--emit", action="store_true",
                        help="写当前诚实状态（no_writer）")
    parser.add_argument("--check", action="store_true",
                        help="只读打印当前归集状态（JSON）")
    parser.add_argument("--path", default=None, help="状态点路径（测试注入用）")
    args = parser.parse_args(argv)

    path = Path(args.path) if args.path else None
    if args.emit:
        target = emit_current(path)
        print("已写入诚实状态点：%s（state=no_writer）" % target)
        return 0
    if args.check:
        print(json.dumps(read_status_point(path), ensure_ascii=False, indent=1))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
