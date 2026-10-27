# -*- coding: utf-8 -*-
"""N7：冻结时点与业务日消费时点对齐自动化（WO-QT-20261020-056 机制复用）。

把 r0_m3_real_trial 段 1「点时冻结」提取为独立 CLI（取消人工驱动）：
唯一一次读取源字节 → 强制业务日与**全部源行 detect_date** 一致
（不一致 → REJECTED + 非零退出 + 不写出任何 manifest）→ 调
freeze_pool_point_in_time 完成点时冻结（manifest 含 freeze_basis）。

边界（必须原样保留）：
- RESEARCH_ONLY_NOT_PRODUCTION：本脚本只冻结；不签名、不跑试运行其余段、
  不产生任何 READY/生产就绪语义。
- 源文件只读；不读任何其他数据源（WO-085：量化零直连）。
- 跨日一致性守护在此显式 surfaced（fail-visible），与验签层
  r0_source_attestation 的全行 detect_date 校验同口径（空白行/重复
  JSON 字段/非法常量一律拒绝；detect_date 须逐行严格等于业务日 ISO 形式）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from .r0_m3_real_trial import freeze_pool_point_in_time

_DAY = re.compile(r"[0-9]{8}\Z")


def _reject(message: str) -> None:
    print(f"REJECTED: {message}", file=sys.stderr)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("重复 JSON 字段")
        obj[key] = value
    return obj


def _reject_constant(_value):
    raise ValueError("非法 JSON 数值")


def _check_alignment(raw: bytes, business_day: str) -> str | None:
    """业务日与全部源行 detect_date 一致性校验（与验签层同口径）。

    返回 None 表示一致；否则返回指明首个不一致行的拒绝理由。
    空源（零行）无不一致行，空虚真通过——与 WO-056 空批合法终态口径一致，
    manifest 如实记 event_count=0，不伪造事件。
    """
    if not isinstance(business_day, str) or not _DAY.fullmatch(business_day):
        return f"业务日格式错误（须 YYYYMMDD）: {business_day!r}"
    try:
        expected = date.fromisoformat(
            f"{business_day[:4]}-{business_day[4:6]}-{business_day[6:]}"
        ).isoformat()
    except ValueError:
        return f"业务日日期无效: {business_day!r}"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"源不是合法 UTF-8: {exc}"
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for number, line in enumerate(lines, 1):
        try:
            if not line.strip():
                raise ValueError("空白行")
            row = json.loads(
                line, object_pairs_hook=_unique_object, parse_constant=_reject_constant
            )
            if type(row) is not dict:
                raise ValueError("事件行必须为 JSON 对象")
            actual = row.get("detect_date")
            if actual != expected:
                raise ValueError(f"detect_date={actual!r} 与业务日 {expected} 不一致")
        except (ValueError, TypeError) as exc:
            return f"第 {number} 行: {exc}"
    return None


def main(argv=None, *, now=None) -> int:
    """N7 独立冻结入口：对齐校验通过才冻结；拒绝路径零写入。"""
    parser = argparse.ArgumentParser(
        prog="freeze_aligned_pool",
        description="N7：信号池点时冻结 + 业务日对齐（RESEARCH_ONLY）",
    )
    parser.add_argument("--source", required=True, help="真实信号池 JSONL 路径（只读）")
    parser.add_argument(
        "--business-day",
        required=True,
        help="业务日 YYYYMMDD（须与全部源行 detect_date 一致）",
    )
    parser.add_argument("--work-dir", required=True, help="冻结产物目录")
    args = parser.parse_args(argv)

    source = Path(args.source)
    try:
        raw = source.read_bytes()  # 唯一一次读取源字节
    except OSError as exc:
        _reject(f"源不可读: {type(exc).__name__}: {exc}")
        return 1

    rejection = _check_alignment(raw, args.business_day)
    if rejection is not None:
        # 拒绝路径零写入：work_dir 不创建、不落任何 manifest
        _reject(rejection)
        return 1

    manifest = freeze_pool_point_in_time(
        source, args.work_dir, args.business_day, now=now, source_bytes=raw
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
