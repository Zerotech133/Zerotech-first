# -*- coding: utf-8 -*-
r"""ops/backup_format_gate.py — 备份产物格式硬闸门（纯函数 / 只读）。

2026-11-05 建立，对应治理方案阶段 2 第 1 条与 R3「manifest 信 rc 不信魔数」。

背景：2026-10-29 三个 .sql.gz 实为纯 SQL 文本（魔数 2d 2d），manifest 却标
ok=true；根因是 Windows 下 gzip.open + subprocess.run(stdout=f) 时子进程写原 fd、
gzip 头在 close 才追加。K-STOCK-414 已修写入侧，本模块是「防止再次静默标绿」
的第二道锁，不是修历史文件。

纪律：
- 只读产物；不删除、不改名、不重压缩、不解密、不整文件解压。
- 所有 I/O 异常收敛为 ok=false，绝不抛穿 main。
- write_quarantine_flag 默认 apply=False，只返回「将要写入」的内容。
- sha256 口径（T-03 裁决）= **产物文件压缩字节**，非解压后 SQL 明文。
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import sys
import tempfile

GZIP_MAGIC = b"\x1f\x8b"
PROBE_BYTES = 8 << 20          # 8 MiB，与 b4_restore_drill.gzip_stream_ok 一致
SHA256_CHUNK = 1 << 20
LEGACY_KEYS = ("key", "ok", "size", "file", "must")

VALID_FORMATS = ("gzip", "plain-sql", "encrypted-aes", "empty", "unknown")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def read_magic(path, n=2):
    with open(path, "rb") as f:
        return f.read(n)


def stream_probe(path, probe=PROBE_BYTES):
    """只解压前 probe 字节，不落临时文件、不全量解压。"""
    try:
        with gzip.open(path, "rb") as g:
            g.read(probe)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - 所有异常收敛为失败判据
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:200])


def sha256_of(path, chunk=SHA256_CHUNK):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def detect_format(magic, size_bytes):
    if size_bytes == 0:
        return "empty"
    if magic.startswith(GZIP_MAGIC):
        return "gzip"
    if magic.startswith(b"--"):
        return "plain-sql"
    if magic.startswith(b"Salted__"):
        return "encrypted-aes"
    return "unknown"


def verify_artifact(path, *, compute_sha256=True, probe=PROBE_BYTES,
                    expect_magic=GZIP_MAGIC):
    """返回 FormatVerdict（九字段）。任何异常都收敛为 ok=false。"""
    v = {
        "ok": False, "format": "unknown", "magic": "", "reason": "",
        "recoverable": False, "size_bytes": 0, "sha256": "",
        "probe_bytes": 0, "checked_at": _now(),
    }
    try:
        if not os.path.isfile(path):
            v["reason"] = "not a regular file"
            return v
        size = os.path.getsize(path)
        v["size_bytes"] = size
        if size == 0:
            v.update(format="empty", reason="0-byte artifact")
            return v
        # 对外 magic 契约保持 2 字节（1f8b / 2d2d / 5361 ...），但格式判定
        # 需要更长的头：detect_format 的 "Salted__" 是 8 字节，若只喂 2 字节
        # 会把合法 .enc 误判成 unknown（VALID_FORMATS 里的 encrypted-aes
        # 分支永远不可达）。ADR-30：两处读头，互不干扰，不改变 v["magic"]。
        magic = read_magic(path)
        head = read_magic(path, 8)
        v["magic"] = magic.hex()
        v["format"] = detect_format(head, size)
        if not magic.startswith(expect_magic):
            # 10-29 三个 .sql.gz 正是 2d2d（"--" 纯 SQL 文本）：只定性，
            # 绝不删除、绝不改写、绝不尝试重压缩。
            v["reason"] = ("magic mismatch: expected 1f8b got %s "
                           "(non-gzip payload)" % magic.hex())
            return v
        ok, why = stream_probe(path, probe)
        v["probe_bytes"] = min(probe, size)
        if not ok:
            v["reason"] = "gzip stream probe failed: " + why
            return v
        if compute_sha256:
            v["sha256"] = sha256_of(path)
        v.update(ok=True, recoverable=True, format="gzip",
                 reason="magic+stream ok")
        return v
    except Exception as exc:  # noqa: BLE001
        v.update(ok=False, recoverable=False,
                 reason="gate exception: %s" % str(exc)[:200])
        return v


def gate_entry(item, out_dir):
    """保留既有 manifest 五键，只增六键；ok = 旧ok AND verdict.ok。

    契约：不得出现「rc=0 但 ok=false」被翻回 ok=true 的路径。
    """
    path = os.path.join(out_dir, item.get("file", ""))
    v = verify_artifact(path)
    out = dict(item)                      # 保留 key/ok/size/file/must
    for k in LEGACY_KEYS:
        out.setdefault(k, item.get(k))
    out.update({
        "format": v["format"], "magic": v["magic"], "sha256": v["sha256"],
        "recoverable": v["recoverable"], "checked_at": v["checked_at"],
        "reason": v["reason"],
    })
    out["ok"] = bool(item.get("ok")) and v["ok"]
    return out


def should_select(verdict):
    """恢复侧选档唯一判据。"""
    return bool(verdict.get("ok")
                and verdict.get("format") == "gzip"
                and verdict.get("recoverable"))


def write_quarantine_flag(day_dir, filename, verdict, *, apply=False):
    """默认 apply=False：只返回 flag 内容 + 目标路径，不落盘、不改 manifest。

    设计完成 != 运行态生效（ADR-03/05/11、FF06 接入点 A9）：本 flag 只是
    隔离留痕 + 显式 --date 时的人工拦截凭据，**不改变
    b4_restore_drill.py::list_backups() 的行为、不是运行态过滤闸门**。
    10-29 不被默认演练选中，仅由 list_backups 按日期倒序且 main() 取
    backups[0]（=11-05）保证，与 flag 无因果关系。自动过滤待 D4 裁定后
    实施期接入 should_select(verify_artifact(p))，本轮不改既有代码。
    """
    flag_path = os.path.join(day_dir, "INVALID_GZIP.flag")
    content = {
        "type": "invalid_gzip_quarantine",
        "date": os.path.basename(os.path.normpath(day_dir)),
        "files": [filename],
        "format": verdict.get("format", "unknown"),
        "recoverable": False,
        "detected_magic": verdict.get("magic", ""),
        "policy": "mark-only, do-not-delete",
        "decision_ref": "D4",
    }
    result = {
        "apply": bool(apply),
        "flag_path": flag_path,
        "flag_content": content,
        "manifest_patch": {"format": verdict.get("format"),
                           "recoverable": False},
    }
    if apply:
        os.makedirs(day_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".flag_", suffix=".tmp", dir=day_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(content, f, ensure_ascii=False, indent=2,
                          sort_keys=True)
                f.write("\n")
            os.replace(tmp, flag_path)
        except Exception:
            print("[warn] flag 写入失败，临时文件保留：%s" % tmp,
                  file=sys.stderr)
            raise
        result["written"] = flag_path
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="备份产物格式闸门（只读）")
    ap.add_argument("cmd", choices=("verify",))
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-sha256", action="store_true")
    a = ap.parse_args(argv)
    v = verify_artifact(a.path, compute_sha256=not a.no_sha256)
    if a.json or True:
        print(json.dumps(v, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
