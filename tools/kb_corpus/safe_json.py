# -*- coding: utf-8 -*-
"""静态产物 JSON 安全写入工具（中风险批次 · wave28 CX）。

统一规范（见 docs/plans/2026-11-01_静态产物安全写入_v1.md）：
  1) 写前备份：目标已存在时 copy2 到 ``research/reports/backups/<name>.<ts>.bak.json``
     （时间戳含微秒防撞），并校验备份 sha256 == 写前目标 sha256；
  2) 原子写：同目录 ``.<name>.<pid>.<rand>.tmp`` 写入 + flush/fsync + ``os.replace``；
  3) 写后校验：tmp 落盘前后都校验 JSON 可解析、可选 validator；失败抛 SafeJsonError 且不清空/不覆盖目标；
  4) 返回报告 ``safe_write.v1``：path/backup/sha_before/sha_after/bytes/atomic/tmp_removed/validated。

用法::

    from safe_json import backup_then_atomic_write
    report = backup_then_atomic_write(OUT, payload)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BACKUP_DIR = REPO / "research" / "reports" / "backups"
REPORT_SCHEMA = "safe_write.v1"


class SafeJsonError(RuntimeError):
    """安全写入失败（序列化/校验/备份/原子写任一环节）。"""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _backup(path: Path, backup_dir: Path | None) -> dict | None:
    """目标存在时备份；返回备份报告（不存在则 None）。"""
    if not path.is_file():
        return None
    before_sha = _sha256_file(path)
    bdir = Path(backup_dir) if backup_dir else DEFAULT_BACKUP_DIR
    bdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = bdir / f"{path.stem}.{ts}.bak.json"
    n = 1
    while dest.exists():  # 极端同微秒撞名
        dest = bdir / f"{path.stem}.{ts}_{n}.bak.json"
        n += 1
    shutil.copy2(path, dest)
    backup_sha = _sha256_file(dest)
    if before_sha != backup_sha:
        raise SafeJsonError(f"备份校验失败：写前目标 sha={before_sha} 备份 sha={backup_sha}")
    return {"path": str(dest), "sha256": backup_sha,
            "bytes": dest.stat().st_size, "source_sha256": before_sha}


def backup_then_atomic_write(path: str | Path, payload: Any, *,
                             backup_dir: str | Path | None = None,
                             indent: int = 1, ensure_ascii: bool = False,
                             validator: Callable[[Any], Any] | None = None,
                             backup: bool = True) -> dict:
    """备份 + JSON 序列化校验 + 同目录 tmp + fsync + os.replace + 写后校验。

    ``validator``：可选的业务校验函数（返回 falsy/空表示通过，抛异常或返回非空错误视为失败）；
    失败时目标文件保持原样（若已有备份则保留备份），抛 ``SafeJsonError``。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    before_sha = _sha256_file(target)

    # 1) 序列化 + 预校验（不触碰目标文件）
    try:
        text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)
        text += "\n" if not text.endswith("\n") else ""
        parsed = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SafeJsonError(f"JSON 序列化失败（目标未改动）：{exc}") from exc
    if validator is not None:
        try:
            verdict = validator(parsed)
            if verdict:
                raise SafeJsonError(f"业务校验失败（目标未改动）：{verdict}")
        except SafeJsonError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SafeJsonError(f"业务校验异常（目标未改动）：{exc}") from exc

    # 2) 写前备份（目标已存在时）
    backup_report = _backup(target, backup_dir) if backup else None

    # 3) 原子写
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # 4) 落盘前校验 tmp 可解析
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SafeJsonError(f"原子写失败（目标未覆盖）：{exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # 5) 写后校验：目标可解析 + sha/字节记录
    try:
        after_text = target.read_text(encoding="utf-8")
        json.loads(after_text)
    except Exception as exc:  # noqa: BLE001
        # 尽量回滚到备份（若存在）
        if backup_report:
            try:
                shutil.copy2(backup_report["path"], target)
                rolled_back = True
            except OSError:
                rolled_back = False
        else:
            rolled_back = False
        raise SafeJsonError(f"写后校验失败（已回滚={rolled_back}）：{exc}") from exc

    after_sha = _sha256_bytes(after_text.encode("utf-8"))
    return {
        "schema": REPORT_SCHEMA,
        "ok": True,
        "path": str(target),
        "backup": backup_report,
        "sha_before": before_sha,
        "sha_after": after_sha,
        "bytes": target.stat().st_size,
        "atomic": True,
        "tmp_removed": not tmp.exists(),
        "validated": True,
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _ensure_utf8_stdout() -> None:  # pragma: no cover - 兼容 GBK 控制台
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


if __name__ == "__main__":  # 自检：python tools/kb_corpus/safe_json.py
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "demo.json"
        r1 = backup_then_atomic_write(p, {"a": 1, "中文": "值"})
        r2 = backup_then_atomic_write(p, {"a": 2})
        print(json.dumps({"first": r1, "second": r2}, ensure_ascii=False, indent=2))
