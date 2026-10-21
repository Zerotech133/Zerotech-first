# -*- coding: utf-8 -*-
"""deliverables 双根一致性只读护栏（漂移门禁）。

背景（2026-10-22 实测）：
  平台级交付物 C:\\项目\\deliverables（A，214 篇 md）与本仓
  deliverables（B，138 篇 md + INDEX.md）长期并行。2026-10-22 人工
  回填两篇分歧（software-company/kg-graph-storage-design-2026-10-16.md、
  zeroq-server-deploy-20261017.md）后，两份根的同名文件 48/48 完全
  一致（44 根目录 + 3 page-audit + 1 software-company）。但没有任何
  机制阻止以后再漂移——本工具就是那把锁：按相对路径求交集，逐对比
  (size, sha256)，把「谁偷偷改了哪篇」变成一条可跑的命令。

只读保证：不写任何文件、不建目录、不调用 git、不联网，纯读盘。

用法：
  python tools/check_deliverables_parity.py              # 默认双根
  python tools/check_deliverables_parity.py --json       # 机器格式
  python tools/check_deliverables_parity.py --a X --b Y --max-report 50

退出码：0=交集全部一致；1=有 diverged / size_only / 读取失败（error）；
        2=A/B 根路径不存在或不是目录。

局限（本工具不能防什么）：
  1. 只比同名文件内容。A 侧独有 166 篇、B 侧独有 91 篇只计数，不检查
     独有集内部是否互相重复、是否是同一篇文章的两个版本。
  2. 不查引用/链接：md 里的相对链接、图片、@路径指向是否存在，一概不看。
  3. 不看语义：改一个字即 diverged，整篇同义重写也是 diverged；反过来，
     两边都错成一样时是 same，工具无感。
  4. 不看 mtime 漂移：内容一致即 same，即使一边时间戳新得多（只对
     diverged/size_only 报哪边新，用于人工判断该信谁）。
  5. 非 .md 一律跳过（只计数），所以同名的 .json/.html 漂移不在视野内；
     运行态缓存目录（.pytest_cache/node_modules 等）按名单跳过。
  6. 根路径默认硬编码；目录搬家后旧默认值会直接退出 2，不会静默空过。
  7. 空交集只告警不判失败（退出码仍 0）：若 --a/--b 指到"存在但指错"的
     目录，工具给红灯是给不出来的，CI 侧必须同时看交集数。
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

TOOL = "check_deliverables_parity"
DEFAULT_A = r"C:\项目\deliverables"
DEFAULT_B = r"C:\项目\老田的AI助手\deliverables"
MD_SUFFIX = ".md"
CHUNK = 1 << 20          # 1MiB；几万字的 md 一次读完，避免小 read 循环
MTIME_EPS = 1.0          # mtime 差 ≤1s 视为同刻，不判"哪边更新"
STATUS_ORDER = ("same", "diverged", "size_only", "error")
# 运行态缓存/依赖目录：不可能是交付物，且可能权限受限或含巨量文件
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache",
             ".ruff_cache", "node_modules", ".venv", "venv", ".tox"}


def sha256_file(path):
    """流式 sha256；几十 KB 的文件实际只走一轮。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_delta(seconds):
    """mtime 差 → 人读时长（s/m/h/d）。"""
    value = abs(seconds)
    if value < 90:
        return "%.0fs" % value
    if value < 5400:
        return "%.1fm" % (value / 60.0)
    if value < 172800:
        return "%.1fh" % (value / 3600.0)
    return "%.1fd" % (value / 86400.0)


def iso_time(mtime):
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def scan(root, label, warnings):
    """递归收集 root 下 .md：{相对路径(posix): {path,size,mtime}}。

    非 .md 只计数不 stat 不读，避免误扫大文件拖慢；不可读目录记一条
    warning 后继续（A 侧 legion-admin-prototype/.pytest_cache 有 ACL
    拒绝，2026-10-22 实证）。返回 (files, nonmd 数, 跳过目录数)。
    """
    files = {}
    nonmd = 0
    dirs_skipped = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=warnings.append):
        keep = []
        for name in dirnames:
            if name in SKIP_DIRS:
                dirs_skipped += 1
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            if not name.lower().endswith(MD_SUFFIX):
                nonmd += 1
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                stat = os.stat(full)
            except OSError as exc:
                warnings.append("[%s] stat 失败 %s: %s" % (label, rel, exc))
                continue
            files[rel] = {"path": full, "size": stat.st_size,
                          "mtime": stat.st_mtime}
    return files, nonmd, dirs_skipped


def compare(a_files, b_files):
    """交集逐对比较 → (pairs, a_only, b_only)。"""
    pairs = []
    for rel in sorted(set(a_files) & set(b_files)):
        left, right = a_files[rel], b_files[rel]
        errors = []
        a_sha = b_sha = None
        try:
            a_sha = sha256_file(left["path"])
        except OSError as exc:
            errors.append("A 读取失败: %s" % exc)
        try:
            b_sha = sha256_file(right["path"])
        except OSError as exc:
            errors.append("B 读取失败: %s" % exc)

        if errors:
            status = "error"
        elif left["size"] == right["size"] and a_sha == b_sha:
            status = "same"
        elif left["size"] == right["size"]:
            status = "size_only"     # 同长不同文，极少见但绝不能当 same
        else:
            status = "diverged"

        delta = right["mtime"] - left["mtime"]
        pair = {
            "rel": rel,
            "status": status,
            "a": {"size": left["size"], "mtime": round(left["mtime"], 3),
                  "mtime_iso": iso_time(left["mtime"]), "sha256": a_sha},
            "b": {"size": right["size"], "mtime": round(right["mtime"], 3),
                  "mtime_iso": iso_time(right["mtime"]), "sha256": b_sha},
            "size_delta": right["size"] - left["size"],
            "mtime_delta_sec": round(delta, 3),
        }
        if status != "same":
            if abs(delta) <= MTIME_EPS:
                pair["newer"] = "equal"
            else:
                pair["newer"] = "b" if delta > 0 else "a"
        if errors:
            pair["error"] = " | ".join(errors)
        pairs.append(pair)
    return pairs, sorted(set(a_files) - set(b_files)), \
        sorted(set(b_files) - set(a_files))


def build_report(a_root, b_root, a_files, b_files, a_nonmd, b_nonmd,
                 a_dirs, b_dirs, pairs, a_only, b_only, warnings):
    counts = {key: 0 for key in STATUS_ORDER}
    for pair in pairs:
        counts[pair["status"]] += 1
    bad = counts["diverged"] + counts["size_only"] + counts["error"]
    return {
        "tool": TOOL,
        "version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "a_root": a_root,
        "b_root": b_root,
        "md_only": True,
        "counts": {
            "a_md": len(a_files),
            "b_md": len(b_files),
            "a_skipped_nonmd": a_nonmd,
            "b_skipped_nonmd": b_nonmd,
            "a_skipped_dirs": a_dirs,
            "b_skipped_dirs": b_dirs,
            "intersection": len(pairs),
            "same": counts["same"],
            "diverged": counts["diverged"],
            "size_only": counts["size_only"],
            "error": counts["error"],
            "a_only": len(a_only),
            "b_only": len(b_only),
        },
        "pairs": pairs,
        "a_only_paths": a_only,
        "b_only_paths": b_only,
        "warnings": [str(item) for item in warnings],
        "verdict": "consistent" if bad == 0 else "drifted",
        "exit_code": 0 if bad == 0 else 1,
    }


def print_human(report, max_report):
    counts = report["counts"]
    print("=== deliverables 双根一致性检查（只读） ===")
    print("A 根: %s" % report["a_root"])
    print("      md=%d  跳过非md=%d  跳过缓存目录=%d"
          % (counts["a_md"], counts["a_skipped_nonmd"],
             counts["a_skipped_dirs"]))
    print("B 根: %s" % report["b_root"])
    print("      md=%d  跳过非md=%d  跳过缓存目录=%d"
          % (counts["b_md"], counts["b_skipped_nonmd"],
             counts["b_skipped_dirs"]))
    print("交集: %d 对  same=%d  diverged=%d  size_only=%d  error=%d"
          % (counts["intersection"], counts["same"], counts["diverged"],
             counts["size_only"], counts["error"]))
    print("独有集（只计数，不逐条列）: A=%d  B=%d"
          % (counts["a_only"], counts["b_only"]))
    if counts["intersection"] == 0:
        print("警告: 交集为 0 对——确认 --a/--b 指对了 deliverables 根"
              "（空交集不判失败，退出码仍 0）")

    bad_pairs = [pair for pair in report["pairs"]
                 if pair["status"] != "same"]
    if bad_pairs:
        print("")
        print("--- 不一致明细（共 %d 条，最多显示 %d 条）---"
              % (len(bad_pairs), max_report))
        for pair in bad_pairs[:max_report]:
            left, right = pair["a"], pair["b"]
            print("[%s] %s" % (pair["status"], pair["rel"]))
            print("    A size=%d  mtime=%s  sha=%s"
                  % (left["size"], left["mtime_iso"],
                     (left["sha256"] or "-")[:16]))
            print("    B size=%d  mtime=%s  sha=%s"
                  % (right["size"], right["mtime_iso"],
                     (right["sha256"] or "-")[:16]))
            if pair["status"] == "error":
                print("    错误: %s" % pair.get("error", ""))
            elif pair.get("newer") == "equal":
                print("    更新侧: 同刻（mtime 差 ≤1s，无法判别）")
            elif pair.get("newer") == "b":
                print("    更新侧: B 新（+%s）" % human_delta(
                    pair["mtime_delta_sec"]))
            else:
                print("    更新侧: A 新（+%s）" % human_delta(
                    pair["mtime_delta_sec"]))
        hidden = len(bad_pairs) - min(len(bad_pairs), max_report)
        if hidden > 0:
            print("... 另有 %d 条未显示（--max-report 调整；--json 出全量）"
                  % hidden)

    if report["warnings"]:
        print("")
        print("--- 扫描告警（%d 条）---" % len(report["warnings"]))
        for line in report["warnings"]:
            print("  %s" % line)

    print("")
    if report["verdict"] == "consistent":
        print("结论: [OK] 交集 %d/%d 完全一致"
              % (counts["same"], counts["intersection"]))
    else:
        print("结论: [FAIL] 交集 %d 对中 %d 对不一致"
              "（diverged=%d size_only=%d error=%d）"
              % (counts["intersection"],
                 counts["diverged"] + counts["size_only"] + counts["error"],
                 counts["diverged"], counts["size_only"], counts["error"]))


def resolve_root(raw, label):
    """参数路径 → 绝对目录；不存在/非目录抛 ValueError（由 main 映射到 2）。"""
    path = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(path):
        raise ValueError("%s 根不是目录或不存在: %s" % (label, path))
    return path


def nonneg_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("需要整数: %r" % text)
    if value < 0:
        raise argparse.ArgumentTypeError("不能为负数: %d" % value)
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        description="对比两份 deliverables 根的同名 .md（size+sha256）")
    parser.add_argument("--a", default=DEFAULT_A,
                        help="A 根目录（默认 %s）" % DEFAULT_A)
    parser.add_argument("--b", default=DEFAULT_B,
                        help="B 根目录（默认 %s）" % DEFAULT_B)
    parser.add_argument("--json", action="store_true",
                        help="输出机器可读 JSON（含全部对与独有集路径）")
    parser.add_argument("--max-report", type=nonneg_int, default=20,
                        metavar="N",
                        help="人读明细最多显示 N 条不一致（默认 20）")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    args = build_parser().parse_args(argv)
    try:
        a_root = resolve_root(args.a, "A")
        b_root = resolve_root(args.b, "B")
    except ValueError as exc:
        if args.json:
            print(json.dumps({"tool": TOOL, "ok": False, "error": str(exc),
                              "exit_code": 2}, ensure_ascii=False,
                             indent=2))
        else:
            print("[ERROR] %s" % exc, file=sys.stderr)
        return 2

    warnings = []
    a_files, a_nonmd, a_dirs = scan(a_root, "A", warnings)
    b_files, b_nonmd, b_dirs = scan(b_root, "B", warnings)
    pairs, a_only, b_only = compare(a_files, b_files)
    report = build_report(a_root, b_root, a_files, b_files, a_nonmd, b_nonmd,
                          a_dirs, b_dirs, pairs, a_only, b_only, warnings)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report, args.max_report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
