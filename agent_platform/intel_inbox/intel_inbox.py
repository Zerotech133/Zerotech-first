# -*- coding: utf-8 -*-
"""满宠每日情报收件箱（方案B 文件收件箱上行·受理席）。

拍板（2026-10-19 工单）：方案B 文件收件箱受理侧。满宠侧生成器
（zerobound-kg/ops/manchong_daily_brief.py --outbox）把 md+json 复制到
本目录 inbox/ 并附 <同名>.meta.json（delivered_utc/两文件 sha256/源路径）；
本脚本扫描 inbox/ 校验后出具回执并入账。

语义（对齐先例：bridge_inbox/ 与 quant_backtest/quant_sector/
upstream_inbox.py L8-21，实现从简——方案B 无 agent 身份，凭内容哈希幂等）：
  · 成对受理：manchong_brief_*.md 与同名 .json 成对，缺对即拒
  · 哈希校验：meta 中 sha256 与文件实际内容一致，篡改即拒
  · ACK 回执：processed/<stem>.ack.json（received_utc/哈希/文件清单）
  · 入账：intel_inbox_ledger.jsonl append-only，幂等键=文件内容哈希，
    同哈希重复投递记 duplicate 不重复入账（受理照常出 ACK 并标记）
  · 拒绝 fail-visible：processed/rejected/<stem>.reject.json 说明原因，
    问题文件移入 processed/rejected/，绝不静默吞掉

纪律：零网络；只写 inbox 之外的 processed/ 与 ledger；ledger 只追加
永不改写。红线：不触碰 registry/ 与 onboard_agent.py（方案A 注册另批）。

用法：
  python intel_inbox.py                       # 默认本目录 inbox/
  python intel_inbox.py --inbox <dir> --processed <dir> --ledger <file>
退出码：0=全部受理/重复无拒绝；1=存在拒绝条目（调度侧可见）。
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INBOX = os.path.join(HERE, "inbox")
DEFAULT_PROCESSED = os.path.join(HERE, "processed")
DEFAULT_LEDGER = os.path.join(HERE, "intel_inbox_ledger.jsonl")
NAME_PREFIX = "manchong_brief_"
META_SUFFIX = ".meta.json"
SOURCE_SUFFIXES = (".md", ".json")          # 需成对的原始双格式
ACCEPT = "accept"
DUPLICATE = "duplicate"
REJECT = "reject"

# 拒绝原因（fail-visible，全量枚举便于调度侧判别）
R_MISSING_PAIR = "missing_pair"        # md/json 未成对
R_META_MISSING = "meta_missing"        # <同名>.meta.json 缺失
R_HASH_MISMATCH = "hash_mismatch"      # meta sha256 与实际内容不符
R_META_INVALID = "meta_invalid"        # meta 结构不合法（缺字段/非 json）


def utc_stamp():
    """UTC ISO 时间戳（回执/入账统一口径）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path):
    """文件内容 sha256（幂等键原料，与满宠侧 meta 口径一致）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_json(path, payload):
    """临时文件 + os.replace 原子写 JSON（判例同源）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


def scan_inbox(inbox_dir):
    """扫描 inbox/，按 stem 归组返回 {stem: {"md":..,"json":..,"meta":..}}。

    只认 manchong_brief_ 前缀的三类后缀；其余文件不动（非本席管辖）。
    """
    groups = {}
    for name in sorted(os.listdir(inbox_dir)):
        path = os.path.join(inbox_dir, name)
        if not os.path.isfile(path) or not name.startswith(NAME_PREFIX):
            continue
        if name.endswith(META_SUFFIX):
            stem, role = name[:-len(META_SUFFIX)], "meta"
        elif name.endswith(".md"):
            stem, role = name[:-len(".md")], "md"
        elif name.endswith(".json"):
            stem, role = name[:-len(".json")], "json"
        else:
            continue
        groups.setdefault(stem, {})[role] = name
    return groups


def load_ledger_index(ledger_path):
    """只读加载已入账的 accept 记录 → {(md_sha, json_sha): stem}。

    损坏行如实跳过并返回告警列表（append-only 台账不因坏行而停摆）。
    """
    index, warnings = {}, []
    if not os.path.isfile(ledger_path):
        return index, warnings
    with open(ledger_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                warnings.append("ledger line %d: 不可解析" % lineno)
                continue
            if rec.get("kind") == ACCEPT:
                key = (rec.get("md_sha256"), rec.get("json_sha256"))
                index[key] = rec.get("stem")
    return index, warnings


def append_ledger(ledger_path, record):
    """append-only 追加一条记录（只追加，永不改写既有行）。"""
    with open(ledger_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _reject(outcome, reason, detail):
    outcome.update({"kind": REJECT, "reason": reason, "detail": detail})
    return outcome


def accept_one(stem, files, inbox_dir, processed_dir, ledger_path,
               ledger_index):
    """受理一个 stem：校验 → 出回执 → 入账 → 移文件。返回结果 dict。

    校验失败走拒绝路径（reject.json + 移入 processed/rejected/ +
    ledger 记 reject），绝不静默吞掉。校验通过但内容哈希与既往
    accept 记录重复时按 duplicate 处理（出 ACK 标记、ledger 记
    duplicate、不重复入账）。
    """
    now = utc_stamp()
    present = [files[r] for r in ("md", "json", "meta") if r in files]
    outcome = {"stem": stem, "files": list(present), "at": now}

    def move_all(dst_dir):
        for name in present:
            os.replace(os.path.join(inbox_dir, name),
                       os.path.join(dst_dir, name))

    # ① 成对校验：md + json 必须齐
    if "md" not in files or "json" not in files:
        _reject(outcome, R_MISSING_PAIR,
                "md/json 未成对：present=%s" % (present,))
        return outcome
    # ② meta 存在性
    if "meta" not in files:
        _reject(outcome, R_META_MISSING,
                "缺 %s.meta.json（满宠侧 --outbox 应附投递清单）" % stem)
        return outcome
    # ③ meta 结构校验
    meta_path = os.path.join(inbox_dir, files["meta"])
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        sha_map = meta["sha256"]
        if not isinstance(sha_map, dict):
            raise ValueError("sha256 字段非字典")
    except (ValueError, OSError, KeyError, TypeError) as e:
        _reject(outcome, R_META_INVALID, "meta 解析失败：%s" % e)
        return outcome
    # ④ 哈希校验：meta 记载 vs 实际内容
    md_path = os.path.join(inbox_dir, files["md"])
    json_path = os.path.join(inbox_dir, files["json"])
    actual = {files["md"]: sha256_file(md_path),
              files["json"]: sha256_file(json_path)}
    for name, digest in actual.items():
        if sha_map.get(name) != digest:
            _reject(outcome, R_HASH_MISMATCH,
                    "%s 实际 sha256=%s 与 meta 记载不符" % (name, digest))
            return outcome

    # ⑤ 幂等判定（键=两文件内容哈希）
    key = (actual[files["md"]], actual[files["json"]])
    duplicate = key in ledger_index
    ack = {
        "ack": "received",
        "stem": stem,
        "received_utc": now,
        "duplicate": duplicate,
        "sha256": {"md": actual[files["md"]],
                   "json": actual[files["json"]]},
        "files": [files["md"], files["json"]],
    }
    if duplicate:
        ack["prior_stem"] = ledger_index[key]
    _atomic_write_json(os.path.join(processed_dir, stem + ".ack.json"), ack)
    # 原文件（md/json/meta）移入 processed/
    move_all(processed_dir)
    # 入账：accept 记全账；duplicate 只记重复事件不重复入账
    append_ledger(ledger_path, {
        "ts": now,
        "kind": DUPLICATE if duplicate else ACCEPT,
        "stem": stem,
        "md_sha256": actual[files["md"]],
        "json_sha256": actual[files["json"]],
        "files": [files["md"], files["json"]],
    })
    if not duplicate:
        ledger_index[key] = stem
    outcome.update({"kind": DUPLICATE if duplicate else ACCEPT,
                    "ack": ack})
    return outcome


def process_inbox(inbox_dir=DEFAULT_INBOX, processed_dir=DEFAULT_PROCESSED,
                  ledger_path=DEFAULT_LEDGER):
    """扫描并处理 inbox/ 全部投递，返回 (results, ledger_warnings)。"""
    os.makedirs(inbox_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)
    os.makedirs(os.path.join(processed_dir, "rejected"), exist_ok=True)
    ledger_index, warnings = load_ledger_index(ledger_path)
    results = []
    for stem, files in sorted(scan_inbox(inbox_dir).items()):
        outcome = accept_one(stem, files, inbox_dir, processed_dir,
                             ledger_path, ledger_index)
        if outcome["kind"] == REJECT:
            rejected_dir = os.path.join(processed_dir, "rejected")
            reject_doc = {
                "reject": "rejected",
                "stem": outcome["stem"],
                "rejected_utc": outcome["at"],
                "reason": outcome["reason"],
                "detail": outcome["detail"],
                "files": outcome["files"],
            }
            _atomic_write_json(
                os.path.join(rejected_dir, stem + ".reject.json"),
                reject_doc)
            for name in outcome["files"]:
                os.replace(os.path.join(inbox_dir, name),
                           os.path.join(rejected_dir, name))
            append_ledger(ledger_path, {
                "ts": outcome["at"],
                "kind": REJECT,
                "stem": outcome["stem"],
                "reason": outcome["reason"],
                "detail": outcome["detail"],
                "files": outcome["files"],
            })
        results.append(outcome)
    return results, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="满宠每日情报收件箱受理席（方案B 文件收件箱）")
    parser.add_argument("--inbox", default=DEFAULT_INBOX)
    parser.add_argument("--processed", default=DEFAULT_PROCESSED)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    args = parser.parse_args(argv)
    results, warnings = process_inbox(args.inbox, args.processed,
                                      args.ledger)
    accepted = sum(1 for r in results if r["kind"] == ACCEPT)
    dup = sum(1 for r in results if r["kind"] == DUPLICATE)
    rej = sum(1 for r in results if r["kind"] == REJECT)
    print("intel_inbox: accept=%d duplicate=%d reject=%d"
          % (accepted, dup, rej))
    for r in results:
        if r["kind"] == REJECT:
            print("REJECT %s: %s (%s)" % (r["stem"], r["reason"],
                                          r["detail"]))
    for w in warnings:
        print("LEDGER-WARNING: %s" % w)
    return 1 if rej else 0


if __name__ == "__main__":
    sys.exit(main())
