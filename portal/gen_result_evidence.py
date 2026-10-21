# -*- coding: utf-8 -*-
"""WO-OS-27 首个真实 ResultEvidence 供数器（2026-10-21）。

背景：WO-OS-26 逐域核查确认六个业务域**都没有 completed_at 字段**，故「今日结果」
长期空态。但有一类真实完成事项一直被忽略——**本仓今日的交付物本身**：
每次 commit 都有精确作者时间与 hash，每份 deliverables 文件都有落盘时点与路径。
它们满足 ResultEvidence 的六关键字段，且每个字段都可回溯核查，不是造数。

诚实边界（关键）：
- receipt_uri 一律置 null。commit hash 是"自证"不是"复核回执"——
  本仓的单子大多尚未走田丰独立验收，因此状态应为 unverified
  （页面显示"证据在 · 缺回执，不称已验收"），不得冒充 verified。
- 只读：只跑 `git log`、只读文件元数据，不写仓库、不改任何账本。
- 产物落 portal/data/result_evidence/today.json（git-ignored），由页面以静态
  JSON 拉取——不依赖 8091 重启，nginx 静态根直接可服务。

用法：
  python gen_result_evidence.py                 # 写今日证据（默认近 20 条）
  python gen_result_evidence.py --dry-run       # 只打印不写盘
  python gen_result_evidence.py --limit 50 --day 2026-10-21
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BEIJING = timezone(timedelta(hours=8))
PORTAL = Path(__file__).resolve().parent
STATION = PORTAL.parent
OUT_DIR = PORTAL / "data" / "result_evidence"
DELIVERABLES = STATION / "deliverables"

# 研发域交付物 → domain 标注；无法判定的一律归"研发"（本线交付）
DOMAIN_BY_PREFIX = (
    ("2026-", "研发"),
)


def _beijing_day(moment: datetime) -> str:
    return moment.astimezone(BEIJING).strftime("%Y-%m-%d")


def _iso_beijing(moment: datetime) -> str:
    return moment.astimezone(BEIJING).isoformat(timespec="seconds")


def _parse_git_iso(text: str) -> Optional[datetime]:
    """git %aI 产出 ISO8601（带时区），解析失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _collect_commits_all(day: str, runner=None) -> List[Dict[str, Any]]:
    """读取指定北京日内**全部**提交（不截断），供计数与截断共用。

    WO-OS-28：必须知道当日真实总量，否则页面无法诚实说明"显示了多少/共多少"——
    实测真实规模 179 项而产物只 40 条，若页面不提示截断，读者会当成全天结果。

    runner 为测试注入点：接收 argv 列表返回 (rc, stdout)。
    """
    if runner is None:
        def runner(argv):  # noqa: D401 - 默认实现
            proc = subprocess.run(argv, cwd=str(STATION), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
            return proc.returncode, proc.stdout

    # 用 --since/--until 收窄到目标日（北京日 → 本地时间字符串，git 按本地时区解释）
    since = "%s 00:00:00" % day
    until = "%s 23:59:59" % day
    # 不设 -n：需要真实总量
    argv = ["git", "log", "--since", since, "--until", until,
            "--pretty=format:%H%x1f%aI%x1f%s"]
    rc, out = runner(argv)
    if rc != 0 or not out:
        return []

    rows: List[Dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        sha, iso, subject = parts[0].strip(), parts[1].strip(), parts[2].strip()
        moment = _parse_git_iso(iso)
        if moment is None:
            continue
        if _beijing_day(moment) != day:
            continue  # 时区跨界，收窄到目标日
        rows.append({
            "id": "commit:%s" % sha[:8],
            "domain": "研发",
            "business_day": _beijing_day(moment),
            "producer_time": _iso_beijing(moment),
            "outcome": "done",
            "evidence_uri": "commit:%s" % sha,
            # 自证不是复核：留空，页面据此显示 unverified，不冒充已验收
            "receipt_uri": None,
            "title": subject[:120],
        })
    return rows


def collect_commits(day: str, limit: int = 20,
                    runner=None) -> List[Dict[str, Any]]:
    """读取指定北京日内的提交（截断到 limit），映射为 ResultEvidence。"""
    return _collect_commits_all(day, runner=runner)[:limit]


def _collect_deliverables_all(day: str,
                              root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读取当日落盘的全部交付文档（mtime 落在目标北京日，不截断）。"""
    base = Path(root) if root is not None else DELIVERABLES
    if not base.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        files = sorted(base.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for path in files:
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=BEIJING)
        except OSError:
            continue
        if _beijing_day(stamp) != day:
            continue
        rel = "deliverables/%s" % path.name
        rows.append({
            "id": "doc:%s" % path.name,
            "domain": "研发",
            "business_day": _beijing_day(stamp),
            "producer_time": _iso_beijing(stamp),
            "outcome": "delivered",
            "evidence_uri": rel,
            "receipt_uri": None,
            "title": path.name,
        })
    return rows


def collect_deliverables(day: str, limit: int = 20,
                         root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读取当日落盘的交付文档（截断到 limit）。"""
    return _collect_deliverables_all(day, root=root)[:limit]


def build_payload(day: str, limit: int = 20, runner=None,
                  deliverables_root: Optional[Path] = None) -> Dict[str, Any]:
    """合并两源并给出诚实标注。

    WO-OS-28：payload 必带 total_available 与 limit，页面据此明说
    "共 N 项，本产物含 M 项"——截断必须可见，否则读者会把 40 条当全天结果。
    """
    commits_all = _collect_commits_all(day, runner=runner)
    docs_all = _collect_deliverables_all(day, root=deliverables_root)
    # WO-OS-29：两源按生产者时点倒序交错后再截断。原写法 commits[:limit] +
    # docs[:limit] 会让忙碌日（实测 145 提交）的窗口全被提交占满，交付物证据在
    # 页面上永不出现，读者会误以为交付物为零。
    merged = commits_all + docs_all
    merged.sort(key=lambda x: str(x.get("producer_time") or ""), reverse=True)
    items = merged[:limit]
    total_available = len(commits_all) + len(docs_all)
    note = ("研发交付物与当日提交作为证据源；receipt 一律为空，"
            "因未走独立复核，故状态为 unverified，不计入已验收。"
            "本产物为截断视图（两源合并按时间倒序取前 %d 条），非当日全部工作。" % limit)
    return {
        "ok": True,
        "business_day": day,
        "generated_at": _iso_beijing(datetime.now(BEIJING)),
        "source": "git-commits+deliverables",
        "note": note,
        "total_available": total_available,
        "limit": limit,
        "items": items,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成今日 ResultEvidence（只读聚合）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--day", default=None, help="北京业务日 YYYY-MM-DD")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    day = args.day or _beijing_day(datetime.now(BEIJING))
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        print("坏日期：%s（需 YYYY-MM-DD）" % day, file=sys.stderr)
        return 2
    if args.limit <= 0:
        print("limit 必须为正整数", file=sys.stderr)
        return 2

    payload = build_payload(day, args.limit)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    target = out_dir / "today.json"

    if args.dry_run:
        print("[dry-run] 未写盘，目标路径: %s" % target)
        print("证据条数: %d（提交 %d / 交付物 %d）" % (
            len(payload["items"]),
            sum(1 for x in payload["items"] if x["id"].startswith("commit:")),
            sum(1 for x in payload["items"] if x["id"].startswith("doc:"))))
        print("回执: 全部为空（unverified，不冒充已验收）")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print("已写入 %s（%d 条，%d 字节）" % (target, len(payload["items"]),
                                       len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
