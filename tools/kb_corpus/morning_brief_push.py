# -*- coding: utf-8 -*-
"""零界知识早报：学习卡(5张) + H18 预告修订观察(前8条) 合并为一条早报（默认 dry-run）。

数据：portal/static/kb_learning_cards.json + portal/static/h18_watch.json
用法：python tools/kb_corpus/morning_brief_push.py [--send] [--refresh]
边界：默认 dry-run；--send 复用 news_collector/send_feishu；只提醒不构成投资建议。

安全写入（wave27 CT）
--------------------
refresh() 写 h18_watch.json 时使用「写前备份 + 读-合并-写 + 原子写 + sha/条数校验」：
- 备份：research/reports/backups/h18_watch.json.<ts>.bak.json；
- 合并：items 按 (ts_code, ann_date) 主键合并（新数据优先、旧独有项保留、保留旧顶层未知字段）；
- 原子：同目录 .tmp → os.replace；校验失败不覆盖目标（临时文件清理）；
- selection_daily.json 当前只读；若未来写入，复用 `_merge_selection_daily`（按 date/picks 合并）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
STATIC = REPO / "portal" / "static"
CARDS = STATIC / "kb_learning_cards.json"
WATCH = STATIC / "h18_watch.json"
H14 = STATIC / "h14_shadow.json"
BACKUP_DIR = REPO / "research" / "reports" / "backups"
KIND = "kb_morning_brief"
SAFE_SCHEMA = "safe_write.v1"


class SafeWriteError(RuntimeError):
    """安全写入失败（不覆盖目标文件）。"""


# ---------------------------------------------------------------------------
# 安全写入三件套（备份 / 读-合并-写 / 原子写 + sha/条数校验）
# ---------------------------------------------------------------------------
def _now_tag() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path | str) -> str | None:
    try:
        return _sha256_bytes(Path(path).read_bytes())
    except OSError:
        return None


def _read_json_tolerant(path: Path | str) -> tuple[dict | None, list[str]]:
    """读旧文件：不存在 → (None, [])；损坏 → (None, [错误])（仍会先备份原文件）。"""
    p = Path(path)
    if not p.exists():
        return None, []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"旧文件读取/解析失败：{exc}"]
    if not isinstance(data, dict):
        return None, ["旧文件顶层不是 JSON 对象"]
    return data, []


def _merge_watch(old: dict | None, new: dict) -> dict:
    """h18_watch.json：items 按 (ts_code, ann_date) 合并；新数据优先、旧独有项保留、旧未知字段保留。"""
    old = old if isinstance(old, dict) else {}
    new = dict(new or {})
    merged = dict(old)          # 保留旧顶层未知字段
    merged.update(new)          # 已知字段以新为准
    def key(item: dict) -> tuple:
        return (str(item.get("ts_code") or ""), str(item.get("ann_date") or ""))
    new_items = [i for i in (new.get("items") or []) if isinstance(i, dict)]
    old_items = [i for i in (old.get("items") or []) if isinstance(i, dict)]
    out = list(new_items)
    seen = {key(i) for i in new_items}
    for item in old_items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    merged["items"] = out
    return merged


def merge_watch_payload(old: dict | None, new: dict) -> dict:
    """公开入口（测试/其他脚本复用）。"""
    return _merge_watch(old, new)


def _merge_selection_daily(old: dict | None, new: dict) -> dict:
    """selection_daily.json（当前只读；如未来写入）：按 date 合并、picks 按主键合并。"""
    old = old if isinstance(old, dict) else {}
    new = dict(new or {})
    merged = dict(old)
    merged.update(new)
    picks_key = lambda it: (str(it.get("ts_code") or it.get("code") or ""),
                            str(it.get("date") or ""))
    new_picks = [p for p in (new.get("picks") or []) if isinstance(p, dict)]
    old_picks = [p for p in (old.get("picks") or []) if isinstance(p, dict)]
    out, seen = list(new_picks), {picks_key(p) for p in new_picks}
    for p in old_picks:
        k = picks_key(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    merged["picks"] = out
    return merged


def _watch_counts(payload: dict | None) -> dict:
    p = payload if isinstance(payload, dict) else {}
    return {"items": len(p.get("items") or []), "date": p.get("date")}


def safe_write_json(path: Path | str, payload: dict, *, merge_fn, validator=None,
                    counts_fn=None, backup_dir: Path | str | None = None) -> dict:
    """安全写 JSON：备份 → 合并 → 原子写 → sha/条数校验；失败不覆盖目标。

    返回报告：{schema,path,backup,sha_before,sha_after,counts_before,counts_after,
              merged_added,merged_kept,read_errors,atomic,tmp_removed}
    """
    path = Path(path)
    backup_dir = Path(backup_dir) if backup_dir is not None else BACKUP_DIR
    old, read_errors = _read_json_tolerant(path)
    merged = merge_fn(old, payload)
    if validator is not None:
        errors = validator(merged)
        if errors:
            raise SafeWriteError("合并后校验失败：" + "；".join(errors))
    expected_counts = counts_fn(merged) if counts_fn else None
    before_counts = counts_fn(old) if counts_fn else None
    data = (json.dumps(merged, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    sha_expected = _sha256_bytes(data)
    sha_before = _sha256_file(path)

    def _pk_count(p):
        if not isinstance(p, dict):
            return 0
        return len(p.get("items") or p.get("reports") or [])

    merged_added = _pk_count(merged) - _pk_count(old)
    merged_kept = _pk_count(merged) - _pk_count(payload)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    backup_path: Path | None = None
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if _sha256_file(tmp) != sha_expected:
            raise SafeWriteError("临时文件 sha 校验失败")
        json.loads(tmp.read_text(encoding="utf-8"))     # 临时文件必须是合法 JSON
        if path.exists():                               # 写前备份（仅目标已存在时）
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"{path.name}.{_now_tag()}.bak.json"
            shutil.copy2(path, backup_path)
            if _sha256_file(backup_path) != sha_before:
                raise SafeWriteError("备份 sha 校验失败，已中止写入")
        os.replace(tmp, path)                           # 原子替换
        if _sha256_file(path) != sha_expected:
            if backup_path is not None and backup_path.exists():
                shutil.copy2(backup_path, path)         # 极端情况回滚备份
            raise SafeWriteError("落盘后 sha 校验失败，已回滚备份")
        final = json.loads(path.read_text(encoding="utf-8"))
        if counts_fn is not None:
            final_counts = counts_fn(final)
            if final_counts != expected_counts:
                if backup_path is not None and backup_path.exists():
                    shutil.copy2(backup_path, path)
                raise SafeWriteError(f"落盘后条数校验失败：{final_counts} != {expected_counts}")
        return {"schema": SAFE_SCHEMA, "path": str(path),
                "backup": str(backup_path) if backup_path else None,
                "sha_before": sha_before, "sha_after": _sha256_file(path),
                "counts_before": before_counts, "counts_after": expected_counts,
                "merged_added": merged_added, "merged_kept": merged_kept,
                "read_errors": read_errors, "atomic": True,
                "tmp_removed": not tmp.exists()}
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def refresh() -> None:
    subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools/kb_corpus/learning_cards.py"), "--n", "20"],
                   capture_output=True, text=True, timeout=300)
    subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools/research/selection_daily.py")],
                   capture_output=True, text=True, timeout=300)
    d = json.loads((STATIC / "selection_daily.json").read_text(encoding="utf-8"))
    w = d.get("forecast_watch") or {}
    payload = {"generated_at": d.get("generated_at"), "date": d.get("date"),
               "items": w.get("items") or []}
    # 安全写入：备份 + 读-合并-写（按 ts_code+ann_date）+ 原子写 + sha/条数校验
    safe_write_json(WATCH, payload, merge_fn=_merge_watch, counts_fn=_watch_counts)


def build_text(cards: dict, watch: dict, n_cards: int = 5, n_watch: int = 8) -> str:
    lines = ["【零界知识早报】学习 + 观察", ""]
    lines.append("📚 今日学习卡")
    for i, c in enumerate((cards.get("cards") or [])[:n_cards], 1):
        lines.append(f"{i}. Q：{c.get('q')}")
        lines.append(f"   A：{c.get('a')}")
        lines.append(f"   [{c.get('topic', '')}] 出处：{c.get('source')}")
    lines.append("")
    items = watch.get("items") or []
    lines.append(f"📡 H18 预告修订观察（{watch.get('date', '')}，共 {len(items)} 条）")
    if items:
        for w in items[:n_watch]:
            lines.append(f"- {w.get('ts_code')} {w.get('type', '')} | {w.get('direction')} "
                         f"{w.get('delta_pp')}pp | {w.get('ann_date')}")
        if len(items) > n_watch:
            lines.append(f"  … 其余 {len(items) - n_watch} 条见 /selection.html")
    else:
        lines.append("- 今日无修订事件")
    # H14 影子盘摘要（若已生成）
    try:
        h = json.loads(H14.read_text(encoding="utf-8"))
        health = h.get("health") or {}
        lines += ["", f"🛡 H14 影子盘（{h.get('period', '')} 期，信号日 {h.get('signal_date', '')}）",
                  f"- 持仓 {h.get('n_holdings')} 只；陷阱权重 {h.get('trap_weight_unconstrained_pct')}% → "
                  f"{h.get('trap_weight_constrained_pct')}%（上限 {h.get('trap_cap_pct')}%，"
                  f"{'达标' if health.get('trap_within_cap') else '超限'}）",
                  f"- 下次验证：{h.get('next_check', '')}；看板 /h14_shadow.html"]
    except Exception:  # noqa: BLE001
        pass
    lines += ["", "——", "属性：学习材料/事件提醒，不构成投资建议；引用请带出处。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真实发送（默认 dry-run）")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    if args.refresh or not CARDS.exists() or not WATCH.exists():
        refresh()
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    watch = json.loads(WATCH.read_text(encoding="utf-8"))
    text = build_text(cards, watch)
    if not args.send:
        print(json.dumps({"ok": True, "mode": "dry-run", "cards": min(5, len(cards.get("cards") or [])),
                          "watch_items": len(watch.get("items") or []), "chars": len(text), "sent": False,
                          "preview": text[:900]}, ensure_ascii=False, indent=1))
        return 0
    news = REPO / "news_collector"
    sys.path.insert(0, str(news))
    try:
        from send_feishu import send_text
    except ImportError as exc:
        print(json.dumps({"ok": False, "reason": f"发送器不可用：{exc}"}, ensure_ascii=False))
        return 2
    ok = bool(send_text(text, kind=KIND))
    print(json.dumps({"ok": ok, "mode": "send", "chars": len(text), "sent": ok}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
