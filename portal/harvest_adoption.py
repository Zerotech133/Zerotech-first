"""harvest_adoption.py — 诸葛亮建议采纳率归集器（C1 · 服务器侧方案，2026-10-23）。

方案演进（判例记录）：
  v1 localStorage 暂存 → 违反页面契约「禁 storage」撤销；
  v2 sendBeacon POST → 违反契约「只读页面禁 POST/第二 fetch」撤销；
  v3（本版）服务器侧归集：建议卡点击后跳转 /dev /intel /notify-command 等领域路径，
     这些 GET 天然落在 nginx access log——从日志提取即采纳信号，零页面改动、
     零隐私痕迹、契约零触碰。**约束驱动的更优解。**

口径：
- 采纳 = 建议下钻点击后发生的领域路径访问（/dev /intel /notify-command /quant-drill）；
- 归集窗口按 5 分钟去重（同 IP 同路径窗口内算一次）；
- 日志缺失/无记录 → 如实 0 条，不造样本。
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import urllib.parse
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "zhuge_adoption.jsonl")
DECISION_RE = re.compile(r"^DR-\d{8}-\d{2,3}$")
CARD_RE = re.compile(r"^ZQ-\d{8}-\d{2,3}$")

# 建议卡 linkFor 映射的目标路径 → 领域名（与页面 linkFor 一致，改页面须同步）
PATH_DOMAIN = {
    "/dev": "研发",
    "/intel": "情报",
    "/notify-command": "通知",
    "/quant-drill": "量化",
}

LINE_PAT = re.compile(
    r'^(\S+) \S+ \S+ \[([^\]]+)\] "GET (\S+)[^"]*" (\d{3}) \S+ "([^"]*)"'
)


def read_log_lines() -> list[str]:
    """读 access log 行：优先 docker logs ai-portal（Windows 侧无宿主路径）。"""
    import subprocess

    try:
        r = subprocess.run(
            # 有界读取：只取近 24h / 最多 5000 行，避免全量 docker logs 打爆内存。
            ["docker", "logs", "--since", "24h", "--tail", "5000", "ai-portal"],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if r.returncode == 0:
            return r.stdout.decode("utf-8", errors="replace").splitlines()
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/harvest_adoption.py", exc)
    # 兜底：宿主 LogPath 在 Windows 不可用（原 Linux 探测函数已移除）
    return []


def parse_entries(lines: list[str], since_hours: float = 24.0) -> list[dict]:
    """从 access log 提取「自 OS 页下钻」的建议领域访问（referer 含 os-entry/os_council）。

    C3DID：建议卡链接可带 `?decision_id=DR-...&card_id=ZQ-...`（只传 ID，
    零页面 POST/契约不变）；这里从 GET query 解析，非法 ID 忽略不臆造。
    """
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(hours=since_hours)
    out = []
    for line in lines:
        m = LINE_PAT.match(line)
        if not m:
            continue
        ip, ts_raw, raw_path, status, referer = m.groups()
        path, _, query = raw_path.partition("?")
        if path not in PATH_DOMAIN or status != "200":
            continue
        ref = referer or ""
        if "/os-entry" not in ref and "/os_council" not in ref:
            continue  # 采纳信号=从 OS 页下钻，直访不算
        try:
            ts = dt.datetime.strptime(ts_raw, "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            continue
        if ts < cutoff:
            continue
        q = urllib.parse.parse_qs(query)
        decision_id = (q.get("decision_id") or [""])[0].strip()[:40]
        card_id = (q.get("card_id") or [""])[0].strip()[:40]
        row = {
            "at": ts.isoformat(timespec="seconds"),
            "domain": PATH_DOMAIN[path],
            "ip": ip,
            "path": path,
        }
        if decision_id and DECISION_RE.fullmatch(decision_id):
            row["decision_id"] = decision_id
        if card_id and CARD_RE.fullmatch(card_id):
            row["card_id"] = card_id
        out.append(row)
    return out


def dedup_window(entries: list[dict], minutes: int = 5) -> list[dict]:
    """同 IP 同域（有 decision_id 时含 ID）5 分钟窗口去重。"""
    out, seen = [], {}
    for e in sorted(entries, key=lambda x: x["at"]):
        key = (e["ip"], e["domain"], e.get("decision_id", ""))
        last = seen.get(key)
        if last:
            t0 = dt.datetime.fromisoformat(last)
            t1 = dt.datetime.fromisoformat(e["at"])
            if (t1 - t0).total_seconds() < minutes * 60:
                continue
        seen[key] = e["at"]
        out.append(e)
    return out


def append_ledger(rows: list[dict]) -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    seen = set()
    if os.path.isfile(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    seen.add(
                        (rec.get("at", ""), rec.get("ip", ""), rec.get("domain", ""))
                    )
                except json.JSONDecodeError:
                    continue
    added = 0
    with open(OUT, "a", encoding="utf-8") as f:
        for r in rows:
            k = (r["at"], r["ip"], r["domain"])
            if k in seen:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            seen.add(k)
            added += 1
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="诸葛亮建议采纳率归集器（服务器侧日志方案）"
    )
    ap.add_argument("--since-hours", type=float, default=24.0)
    ap.add_argument("--stat", action="store_true")
    args = ap.parse_args(argv)
    if args.stat:
        if not os.path.isfile(OUT):
            print("账本为空（0 条）——尚无采纳记录，不造样本")
            return 0
        rows = [json.loads(x) for x in open(OUT, encoding="utf-8") if x.strip()]
        by_day = collections.Counter(r["at"][:10] for r in rows)
        by_domain = collections.Counter(r.get("domain", "?") for r in rows)
        print(f"总计 {len(rows)} 条；按日 {dict(by_day)}；按域 {dict(by_domain)}")
        return 0
    log_lines = read_log_lines()
    if not log_lines:
        print(
            "[FAIL] 读不到 ai-portal access log（docker logs 失败且无宿主路径）——"
            "如实报：无法归集，不造样本"
        )
        return 1
    entries = parse_entries(log_lines, args.since_hours)
    rows = dedup_window(entries)
    added = append_ledger(rows)
    print(
        f"归集完成：日志命中 {len(entries)} / 去重后 {len(rows)} / 新增 {added} → "
        f"{os.path.relpath(OUT, HERE)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
