# -*- coding: utf-8 -*-
"""纯离线审计近期线批处理结果；不导入采集器，不连接数据库或网络。"""

import argparse
import json
import re
from pathlib import Path


def audit(
    pool, reports, reported_processed=None, reported_remaining=None, planned_result=None
):
    issues = []
    entries = pool.get("pool")
    if not isinstance(entries, list):
        return {"ok": False, "issues": ["候选池缺少 pool 列表"]}
    raw_ids = [
        entry.get("user_id") if isinstance(entry, dict) else None for entry in entries
    ]
    ids = [uid if isinstance(uid, str) else "" for uid in raw_ids]
    invalid = [
        {"idx": idx, "uid": uid}
        for idx, uid in enumerate(raw_ids)
        if not isinstance(uid, str)
        or not uid.isascii()
        or not uid.isdecimal()
        or int(uid) == 0
    ]
    if invalid:
        issues.append("候选池含无效 UID（包括 -1），不得进入采集队列")
    if len(ids) != len(set(ids)):
        issues.append("候选池 UID 重复，索引映射不唯一")

    if (reported_processed is None) != (reported_remaining is None):
        issues.append("已处理数与剩余数必须同时提供")
    elif reported_processed is not None:
        if (
            reported_processed < 0
            or reported_remaining < 0
            or reported_processed + reported_remaining != len(ids)
        ):
            issues.append("已处理数+剩余数与候选池长度不符，不能据摘要推定断点")

    seen_windows = {}
    seen_indices = {}
    seen_paths = set()
    attempted = set()
    completed = set()
    result_checks = []
    blocked = []
    for path, report in reports:
        path = Path(path)
        errors = []
        if path in seen_paths:
            errors.append("同一结果路径重复提供，不能重复计数")
        seen_paths.add(path)
        if not isinstance(report, dict):
            errors.append("结果内容不是对象")
            report = {}
        start, end = report.get("start_idx"), report.get("end_idx")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(ids)
        ):
            errors.append("索引范围无效或越过候选池")
            expected = []
        else:
            expected = ids[start:end]
            window = (start, end)
            if window in seen_windows:
                errors.append(
                    "范围重复，与 %s 冲突；同名 batch_result 文件可能覆盖证据"
                    % seen_windows[window]
                )
            else:
                seen_windows[window] = str(path)
            for idx in range(start, end):
                if idx in seen_indices and seen_indices[idx] != window:
                    errors.append(
                        "索引 %d 与先前范围 %s 重叠，不能双计"
                        % (idx, seen_indices[idx])
                    )
                seen_indices[idx] = window
            name_match = re.fullmatch(
                r"batch_result_(\d+)_(\d+)(?:_[A-Za-z0-9-]+)?\.json", path.name
            )
            if not name_match or tuple(map(int, name_match.groups()[:2])) != window:
                errors.append("结果文件名与索引范围不一致，可能覆盖或串批")
        users = report.get("users")
        if not isinstance(users, dict):
            users = {}
            errors.append("users 不是 UID 映射")
        if expected:
            actual = list(users)
            if actual != expected[: len(actual)]:
                errors.append("UID/idx 映射不符或处理中途跳号")
            if len(actual) > len(expected):
                errors.append("结果 UID 超出本批索引范围")
            for uid, entry in users.items():
                if uid not in expected:
                    continue
                attempted.add(uid)
                if not isinstance(entry, dict) or entry.get("outcome") != "a":
                    errors.append("UID %s 非成功状态，不得计入已完成" % uid)
                else:
                    completed.add(uid)
        outcome = report.get("outcome")
        stop = report.get("stop_reason")
        next_idx = report.get("next_start_idx")
        if outcome != "a":
            blocked.append(
                {
                    "path": str(path),
                    "outcome": outcome,
                    "stop_reason": stop,
                    "next_start_idx": next_idx,
                }
            )
            errors.append(
                "批次 outcome=%r，b/c 不得判成功（即使进程退出码为 0）" % outcome
            )
            if not stop:
                errors.append("停批缺少 stop_reason")
            if type(next_idx) is not int or not expected or not start <= next_idx < end:
                errors.append("停批 next_start_idx 不在本批范围")
            elif next_idx > start + len(users):
                errors.append("停批断点越过已尝试 UID")
            elif next_idx < start + len(users) - 1:
                errors.append("停批断点跳回到早于末位的 UID")
            elif next_idx == start + len(users) and users:
                last = users[list(users)[-1]]
                if not isinstance(last, dict) or last.get("outcome") != "a":
                    errors.append("停批断点越过失败 UID")
        else:
            if stop is not None or next_idx is not None:
                errors.append("成功批次不得包含停止原因或未完成断点")
            if expected and len(users) != len(expected):
                errors.append("成功批次并未完成整个 idx 范围")
        if errors:
            issues.extend("%s: %s" % (path, error) for error in errors)
        result_checks.append({"path": str(path), "ok": not errors, "issues": errors})

    planned = str(planned_result) if planned_result is not None else None
    if planned:
        planned_path = Path(planned)
        if planned_path.exists():
            issues.append("拟写结果路径已存在，禁止按范围同名覆盖：%s" % planned)
        if not re.fullmatch(
            r"batch_result_\d+_\d+_[A-Za-z0-9-]+\.json", planned_path.name
        ):
            issues.append("拟写结果文件名须携带唯一 run-id，不能仅按范围命名")
    return {
        "ok": not issues,
        "pool_count": len(ids),
        "invalid_uids": invalid,
        "summary_gap": (
            len(ids) - reported_processed - reported_remaining
            if reported_processed is not None and reported_remaining is not None
            else None
        ),
        "attempted_uids": len(attempted),
        "completed_uids": len(completed),
        "completed_uids_meaning": "逐 UID outcome=a 的人数，停批前已完成者也计入；不是成功批次数",
        "blocked": blocked,
        "unattempted": [
            {"idx": idx, "uid": uid}
            for idx, uid in enumerate(ids)
            if uid not in attempted
        ],
        "results": result_checks,
        "issues": issues,
        "overwrite_protection": "采集器写文件须使用独立 run-id 文件名与排他创建模式 x；本只读检查器只拒绝已存在的拟写路径",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读检查近期线候选池与批次结果")
    parser.add_argument("--pool", required=True, type=Path)
    parser.add_argument("--result", action="append", default=[], type=Path)
    parser.add_argument("--reported-processed", type=int)
    parser.add_argument("--reported-remaining", type=int)
    parser.add_argument("--planned-result", type=Path)
    args = parser.parse_args(argv)
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    reports = [
        (path, json.loads(path.read_text(encoding="utf-8"))) for path in args.result
    ]
    result = audit(
        pool,
        reports,
        args.reported_processed,
        args.reported_remaining,
        args.planned_result,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
