#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tools/e2e_report.py -- E2E 轨迹只读巡检报告
# 定位：只读工具，零网络，不写任何文件；stdout 仅输出一个合法 JSON 对象；退出码恒为 0。
# 契约：{"runs": <int>, "items": [<item>, ...]}
#       item 字段：file / date / time / ok / fail / mtime（mtime 为 K-ORG-068 新增）。
# K-ORG-066 首版交付：扫描 data/e2e 轨迹文件，输出 runs/items 概览。
# K-ORG-068 迭代增量（QA1 observation 两项转工单）：
#   P-1 items 每条新增 "mtime" 字段：取轨迹文件 os.path.getmtime，
#       格式化为 "%Y-%m-%d %H:%M:%S" 跨日可读时间戳字符串。
#   P-2 修复排序缺陷：原按文件名 time（仅 HHMMSS）排序，跨日时顺序错误；
#       改为按 mtime 实际时间值倒序。datetime 对象仅在排序键中构造，
#       不保留进 JSON 结构，防非可序列化对象破坏 stdout 契约。
#   P-3 runs=0 守卫固化：data/e2e 目录不存在或无匹配文件时，
#       输出 {"runs": 0, "items": []} 且退出码 0，不抛异常。
#   P-4 既有函数结构与 --json 契约保持不变。
# 用法：python tools/e2e_report.py [--json]

import argparse
import datetime
import glob
import json
import os
import re
import sys

DATA_DIR = os.path.join("data", "e2e")
TRACE_GLOB = os.path.join(DATA_DIR, "e2e_*_*.json")
NAME_RE = re.compile(r"^e2e_(\d{8})_(\d{6})\.json$")
FMT_FULL = "%Y-%m-%d %H:%M:%S"


def parse_name(path):
    """从轨迹文件名解析 (date_part, time_part)，命名不匹配时返回 (None, None)。"""
    m = NAME_RE.match(os.path.basename(path))
    if not m:
        return None, None
    return m.group(1), m.group(2)


def collect_traces():
    """扫描轨迹文件列表；P-3 守卫：目录不存在或无匹配文件一律返回空列表，不上抛。"""
    if not os.path.isdir(DATA_DIR):
        return []
    return sorted(
        p for p in glob.glob(TRACE_GLOB)
        if os.path.isfile(p) and NAME_RE.match(os.path.basename(p))
    )


def read_payload(path):
    """只读加载轨迹 JSON；损坏或非 dict 内容降级为空 dict，不中断巡检。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def get_mtime_ts(path):
    """取文件 mtime 时间戳；竞态删除等取不到时降级 0.0，保证 runs 统计稳定。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def build_item(path):
    """组装单条 item：既有字段口径不变，新增 mtime 可读时间戳（P-1）。"""
    date_part, time_part = parse_name(path)
    payload = read_payload(path)
    mtime_ts = get_mtime_ts(path)
    return {
        "file": os.path.basename(path),
        "date": date_part or "",
        "time": time_part or "",
        "ok": int(payload.get("ok", 0) or 0),
        "fail": int(payload.get("fail", 0) or 0),
        "mtime": datetime.datetime.fromtimestamp(mtime_ts).strftime(FMT_FULL),
    }


def build_report():
    """组装报告：P-2 排序改为按 mtime 实际时间值倒序，datetime 仅作排序键不入 JSON。"""
    items = [build_item(p) for p in collect_traces()]
    items.sort(
        key=lambda it: datetime.datetime.strptime(it["mtime"], FMT_FULL),
        reverse=True,
    )
    return {"runs": len(items), "items": items}


def main():
    parser = argparse.ArgumentParser(description="E2E trace read-only report tool")
    parser.add_argument("--json", action="store_true",
                        help="emit a single JSON object to stdout")
    parser.parse_args()
    report = build_report()
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())