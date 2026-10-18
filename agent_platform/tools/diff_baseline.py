# -*- coding: utf-8 -*-
"""diff_baseline：账本基线对比（观察点 FAIL 后取证第一步，只读，fail-visible）。

## 背景

E 路已于 2026-10-18 把五账本快照到仓外
C:\\项目\\backups\\2026-10-18_observation_baseline\\（见其 MANIFEST.md 与
docs/audit/2026-10-18_观察点前账本基线快照记录.md）。2026-10-19 17:10
影子首跑后，若 17:25 观察点 FAIL，第一步取证是「跑前 vs 跑后账本 diff」——
本工具把该 diff 固化为脚本，替代手工 diff。

## 对比口径

对每个账本对（仓内源 vs 仓外 .baseline）输出：
  - 行数变化（旧→新）、新增行数、sha256 是否一致；
  - jsonl 逐行 multiset 对比：
      * 新增行（新有旧无）——正常增长，按 run_id/verdict_key 等幂等键
        列出（每行截断 80 字符）；
      * 缺失行（旧有新无）——账本 append-only 语义下缺失 = 被篡改/回滚，
        最高级别告警，标红列出（非 tty 退化为 [缺失] 前缀）。
  - checkpoints.db（sqlite 二进制）：只比 sha256 与大小，不做行级对比
    （如实声明：二进制仅指纹级，sqlite 页内合法变更与篡改不可区分，
    指纹差异不影响退出码，供人工判读）。

## 退出码

  2 = 存在缺失行（append-only 账本被篡改/回滚嫌疑，最高级别）
  1 = 文件缺失 / 不可读（中性：对比不完整，如实标注，不妄判）
  0 = 无缺失行（完全一致或仅新增——正常增长，列出即可）

优先级：2 > 1 > 0（同一批账本既有缺失行又有文件缺失时取 2）。

## 红线

零写面：不创建目录、不追加账本、不写日志——连 mkdir 都不做（对齐
check_observation_point 只读观测面判例）。fail-visible：任何读取异常
透出为可见条目，不静默吞掉。

用法（两 root 显式；不传则按仓内布局自动定位）：

    python tools/diff_baseline.py --baseline-dir <dir> --data-root <agent_platform/data>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

DISPLAY_TRUNC = 80

# jsonl 行身份键优先级（先取证自真账本 schema）：
#   shadow_ledger.run_id / strategy verdict_key / trace_links.link_id /
#   perf_ledger.perf_key / registrations.submit_id / strategy_id
KEY_CANDIDATES = ("run_id", "verdict_key", "link_id", "perf_key",
                  "submit_id", "strategy_id")
# 身份键之外附带的时序字段（只取第一个命中的，辅助人工判读）
TIME_CANDIDATES = ("ts", "date", "declared_at", "registered_at",
                   "recorded_at")

# (账本名, 源相对路径(相对 data_root), 基线文件名, 类型)
LEDGER_PAIRS = (
    ("shadow_ledger", ("shadow", "shadow_ledger.jsonl"),
     "shadow_ledger.jsonl.baseline", "jsonl"),
    ("trace_links", ("trace", "trace_links.jsonl"),
     "trace_links.jsonl.baseline", "jsonl"),
    ("perf_ledger", ("perf_ledger.jsonl",),
     "perf_ledger.jsonl.baseline", "jsonl"),
    ("registrations", ("codex_bridge", "registrations.jsonl"),
     "registrations.jsonl.baseline", "jsonl"),
    ("checkpoints_db", ("checkpoints.db",),
     "checkpoints.db.baseline", "binary"),
)

MISSING_LINES, GREW, IDENTICAL = "MISSING_LINES", "GREW", "IDENTICAL"
FILE_MISSING, ERROR = "FILE_MISSING", "ERROR"
FINGERPRINT_MATCH, FINGERPRINT_DIFF = "FINGERPRINT_MATCH", "FINGERPRINT_DIFF"


def _sha256_of(path: Path) -> str:
    """流式读源算 sha256（只读）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_jsonl_lines(path: Path):
    """读 jsonl 全文（只读）。返回 (原始行列表, 坏行数, 异常或 None)。

    行为空白行跳过；坏行（非合法 JSON）仍保留在行列表中参与 multiset
    对比（坏行也是账本字节的一部分，缺失/新增都应可见）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001  fail-visible
        return [], 0, str(exc)[:120]
    lines: List[str] = []
    bad = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            json.loads(s)
        except json.JSONDecodeError:
            bad += 1
        lines.append(s)
    return lines, bad, None


def _identify(raw: str) -> str:
    """从原始行提取身份键 + 时序字段，截断 80 字符，供人工判读。"""
    head = raw[:DISPLAY_TRUNC]
    try:
        row = json.loads(raw)
    except Exception:  # noqa: BLE001  坏行原样截断展示
        return head
    if not isinstance(row, dict):
        return head
    parts = []
    for k in KEY_CANDIDATES:
        v = row.get(k)
        if v:
            parts.append("%s=%s" % (k, v))
            break
    for k in TIME_CANDIDATES:
        if k in row:
            parts.append("%s=%s" % (k, row[k]))
            break
    if parts:
        return "%s | %s" % (" ".join(parts), head)
    return head


def _ordered_delta(lines: List[str], delta: Counter) -> List[str]:
    """按文件内出现顺序展开 Counter 差集（保持时序，append-only 语义）。"""
    remain = delta.copy()
    out: List[str] = []
    for ln in lines:
        if remain.get(ln, 0) > 0:
            out.append(ln)
            remain[ln] -= 1
    return out


def _diff_jsonl_pair(name: str, old_path: Path, new_path: Path) -> dict:
    """单个 jsonl 账本对：源(new) vs 基线(old)，multiset 逐行对比。"""
    item = {"name": name, "kind": "jsonl", "added": [], "missing": [],
            "bad_old": 0, "bad_new": 0, "old_sha": None, "new_sha": None,
            "sha_equal": None, "old_lines": None, "new_lines": None,
            "status": None, "evidence": ""}
    old_exists = old_path.is_file()
    new_exists = new_path.is_file()
    if not old_exists or not new_exists:
        item["status"] = FILE_MISSING
        sides = []
        if not old_exists:
            sides.append("基线缺失(%s)" % old_path.name)
        if not new_exists:
            sides.append("源缺失(%s)" % new_path)
        item["evidence"] = "文件缺失（中性，如实标注）：%s" % "；".join(sides)
        return item
    try:
        old_sha = _sha256_of(old_path)
        new_sha = _sha256_of(new_path)
    except Exception as exc:  # noqa: BLE001  fail-visible
        item["status"] = ERROR
        item["evidence"] = "读取失败（%s）" % str(exc)[:100]
        return item
    item["old_sha"], item["new_sha"] = old_sha, new_sha
    item["sha_equal"] = old_sha == new_sha
    old_lines, old_bad, old_err = _read_jsonl_lines(old_path)
    new_lines, new_bad, new_err = _read_jsonl_lines(new_path)
    item["bad_old"], item["bad_new"] = old_bad, new_bad
    if old_err or new_err:
        item["status"] = ERROR
        item["evidence"] = "读取失败（%s）" % (old_err or new_err)
        return item
    item["old_lines"], item["new_lines"] = len(old_lines), len(new_lines)
    old_counter, new_counter = Counter(old_lines), Counter(new_lines)
    added_counter = new_counter - old_counter
    missing_counter = old_counter - new_counter
    item["added"] = _ordered_delta(new_lines, added_counter)
    item["missing"] = _ordered_delta(old_lines, missing_counter)
    if item["missing"]:
        item["status"] = MISSING_LINES
    elif item["added"]:
        item["status"] = GREW
    else:
        item["status"] = IDENTICAL
    item["evidence"] = (
        "行数 %d→%d，新增 %d，缺失 %d，sha256 %s"
        % (item["old_lines"], item["new_lines"], len(item["added"]),
           len(item["missing"]),
           "一致" if item["sha_equal"] else "不一致"))
    return item


def _diff_binary_pair(name: str, old_path: Path, new_path: Path) -> dict:
    """二进制（sqlite）账本对：仅 sha256 + 大小指纹级对比，不做行级。"""
    item = {"name": name, "kind": "binary", "added": [], "missing": [],
            "bad_old": None, "bad_new": None, "old_sha": None,
            "new_sha": None, "sha_equal": None, "old_lines": None,
            "new_lines": None, "old_size": None, "new_size": None,
            "status": None, "evidence": ""}
    old_exists = old_path.is_file()
    new_exists = new_path.is_file()
    if not old_exists or not new_exists:
        item["status"] = FILE_MISSING
        sides = []
        if not old_exists:
            sides.append("基线缺失(%s)" % old_path.name)
        if not new_exists:
            sides.append("源缺失(%s)" % new_path)
        item["evidence"] = "文件缺失（中性，如实标注）：%s" % "；".join(sides)
        return item
    try:
        old_sha, new_sha = _sha256_of(old_path), _sha256_of(new_path)
        old_size, new_size = old_path.stat().st_size, new_path.stat().st_size
    except Exception as exc:  # noqa: BLE001  fail-visible
        item["status"] = ERROR
        item["evidence"] = "读取失败（%s）" % str(exc)[:100]
        return item
    item["old_sha"], item["new_sha"] = old_sha, new_sha
    item["old_size"], item["new_size"] = old_size, new_size
    item["sha_equal"] = old_sha == new_sha
    if item["sha_equal"]:
        item["status"] = FINGERPRINT_MATCH
        sha_txt = "一致"
    else:
        item["status"] = FINGERPRINT_DIFF
        sha_txt = ("不一致（指纹差异不影响退出码，sqlite 合法变更与篡改"
                   "不可区分，供人工判读）")
    item["evidence"] = (
        "二进制指纹级对比（sqlite 不做行级，如实声明）：大小 %d→%d 字节，"
        "sha256 %s" % (old_size, new_size, sha_txt))
    return item


def run_diff(baseline_dir, data_root) -> dict:
    """对比主入口（只读）。返回含逐账本状态与汇总退出码的报告。"""
    base, root = Path(baseline_dir), Path(data_root)
    items = []
    for name, rel, baseline_name, kind in LEDGER_PAIRS:
        src = root.joinpath(*rel)
        baseline_path = base / baseline_name
        if kind == "jsonl":
            items.append(_diff_jsonl_pair(name, baseline_path, src))
        else:
            items.append(_diff_binary_pair(name, baseline_path, src))
    n_missing_lines = sum(1 for it in items if it["status"] == MISSING_LINES)
    n_neutral = sum(1 for it in items if it["status"] in (FILE_MISSING, ERROR))
    if n_missing_lines:  # 篡改嫌疑最高级别，压过中性
        exit_code = 2
    elif n_neutral:
        exit_code = 1
    else:
        exit_code = 0
    return {"baseline_dir": str(base), "data_root": str(root),
            "items": items,
            "identical": sum(1 for it in items if it["status"] == IDENTICAL),
            "grew": sum(1 for it in items if it["status"] == GREW),
            "fingerprint_match": sum(1 for it in items
                                     if it["status"] == FINGERPRINT_MATCH),
            "fingerprint_diff": sum(1 for it in items
                                    if it["status"] == FINGERPRINT_DIFF),
            "missing_lines": n_missing_lines, "neutral": n_neutral,
            "exit_code": exit_code}


def _red(s: str) -> str:
    """tty 下标红缺失行告警；非 tty（重定向/管道）退化为纯文本。"""
    if sys.stdout.isatty():
        return "\033[31m%s\033[0m" % s
    return s


def _print_report(rep: dict) -> None:
    print("=" * 66)
    print("账本基线对比  基线: %s" % rep["baseline_dir"])
    print("              数据: %s" % rep["data_root"])
    print("=" * 66)
    for it in rep["items"]:
        print("  [%s] %s —— %s" % (it["status"], it["name"], it["evidence"]))
        for ln in it["added"]:
            print("      [新增] %s" % _identify(ln))
        for ln in it["missing"]:
            print("      %s" % _red("[缺失] %s（append-only 账本缺失=篡改/回滚嫌疑）"
                                    % _identify(ln)))
    print("汇总: 一致 %d / 正常增长 %d / 缺失行 %d / 中性(文件缺失或不可读) %d"
          % (rep["identical"] + rep["fingerprint_match"], rep["grew"],
             rep["missing_lines"], rep["neutral"]))
    if rep["fingerprint_diff"]:
        print("附注: checkpoints.db 指纹差异 %d 个（二进制仅指纹级，"
              "不影响退出码，供人工判读）" % rep["fingerprint_diff"])
    verdict = {0: "无缺失行——完全一致或仅正常增长（退出码 0）",
               1: "对比不完整——文件缺失/不可读，中性如实标注（退出码 1）",
               2: "存在缺失行——append-only 账本被篡改/回滚嫌疑，"
                  "最高级别告警，先查清（退出码 2）"}[rep["exit_code"]]
    print("结论: %s" % verdict)


def _auto_paths():
    """按仓内布局自动定位：agent_platform/data 与仓外基线目录。"""
    agent_platform = Path(__file__).resolve().parent.parent
    data_root = agent_platform / "data"
    baseline_dir = (agent_platform.parents[1] / "backups"
                    / "2026-10-18_observation_baseline")
    return baseline_dir, data_root


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="账本基线对比（只读，零写面，fail-visible）——"
                    "观察点 FAIL 后取证第一步")
    ap.add_argument("--baseline-dir", default=None,
                    help="基线目录（含 *.baseline 副本；缺省自动定位 "
                         "C:/项目/backups/2026-10-18_observation_baseline）")
    ap.add_argument("--data-root", default=None,
                    help="数据根（=agent_platform/data；缺省自动定位仓内）")
    args = ap.parse_args(argv)
    baseline_dir = args.baseline_dir
    data_root = args.data_root
    if baseline_dir is None or data_root is None:
        auto_b, auto_d = _auto_paths()
        baseline_dir = baseline_dir or str(auto_b)
        data_root = data_root or str(auto_d)
    # root 纪律：目录不存在时显式报错退出，不让全部 FILE_MISSING 掩盖传参失误
    if not Path(baseline_dir).is_dir():
        print("[error] 基线目录不存在：%s（请显式传 --baseline-dir）"
              % baseline_dir, file=sys.stderr)
        return 1
    if not Path(data_root).is_dir():
        print("[error] 数据根不存在：%s（请显式传 --data-root）"
              % data_root, file=sys.stderr)
        return 1
    rep = run_diff(baseline_dir, data_root)
    _print_report(rep)
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
