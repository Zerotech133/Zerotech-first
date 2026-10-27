"""K-MDC-E3 (阚泽·信驿司): feishu intel export — windowed, read-only, receipted.

老田 D2 裁决四件套: 仅本人 + 每日两窗口 + 只读 + 许可字段.
- 窗口: 盘前 08:30-09:15 / 盘后 15:10-16:00 (Asia/Shanghai), 窗外拒绝 (失败关闭)
- 内容: 案卷候选摘要 (top N 候选边+调查问题); 无原文全文, 无权利受限字段
- 通道: 复用 news_collector/send_feishu.py (本人 open_id 直发)
- 回执: candidate/shown/attempted/accepted 四分账; HTTP OK ≠ 送达, 以 SEND_OK 为准
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
WINDOWS = (
    (dtime(8, 30), dtime(9, 15)),  # 盘前
    (dtime(15, 10), dtime(16, 0)),
)  # 盘后
RECIPIENT_SCOPE = "laotian_only"  # 仅本人
MAX_EDGES = 5  # 许可字段范围: top N 候选边


class ExportRejected(RuntimeError):
    """Fail-closed export rejection."""


def in_window(now: datetime) -> bool:
    if now.tzinfo is None:
        raise ExportRejected("TIME_INVALID: timezone-aware now required")
    local = now.astimezone(TZ)
    t = local.time()
    # 周末不开窗(情报面盘面休市; 周末窗口属未来裁决)
    if local.weekday() >= 5:
        return False
    return any(a <= t <= b for a, b in WINDOWS)


def render_digest(dossier: dict) -> str:
    """案卷候选摘要 → 文本. 只含候选与调查问题, 不含原文."""
    if not isinstance(dossier, dict) or "total_edges" not in dossier:
        raise ExportRejected("DOSSIER_INVALID: dossier payload required")
    code = dossier.get("stock_code", "?")
    total = dossier["total_edges"]
    if total == 0:
        return f"[情报候选·{code}] 该票暂无候选关系边（空态=无候选，不是无情报）。"
    lines = [
        f"[情报候选·{code}] 共 {total} 条候选边，显示 {dossier.get('shown', 0)} 条（全部 candidate 调查方向，非事实）："
    ]
    for e in dossier.get("edges", [])[:MAX_EDGES]:
        lines.append(
            f"· [{e.get('rel_type')}] {e.get('pair')} — {e.get('question', '')}"
        )
    if dossier.get("truncated"):
        lines.append("（已截断，完整见 /intel-dossier 第六屏）")
    lines.append("— 零界情报中枢·通信部出口（每日两窗口·只读）")
    return "\n".join(lines)


@dataclass
class ExportReceipt:
    at: str
    stock_code: str
    candidate: int  # 总候选数
    shown: int  # 摘要显示数
    attempted: bool  # 是否发起发送
    accepted: bool  # SEND_OK 才为 True
    window_state: str
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def export(dossier: dict, *, now: datetime, sender=None) -> ExportReceipt:
    """主入口: 窗口判定 → 渲染 → 发送 → 回执."""
    code = dossier.get("stock_code", "?")
    candidate = int(dossier.get("total_edges", 0))
    if not in_window(now):
        return ExportReceipt(
            now.isoformat(),
            code,
            candidate,
            min(candidate, MAX_EDGES),
            attempted=False,
            accepted=False,
            window_state="closed",
            note="窗口外拒发（盘前08:30-09:15/盘后15:10-16:00，工作日）",
        )
    text = render_digest(dossier)
    attempted = True
    if sender is None:
        here = Path(__file__).resolve()
        for base in [here.parents[1]] + list(here.parents[1].parents)[:3]:
            send = base / "news_collector" / "send_feishu.py"
            if send.exists():
                sender = str(send)
                break
        else:
            raise ExportRejected("CHANNEL_MISSING: send_feishu.py not found")
    import subprocess
    import sys
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        tmp = f.name
    try:
        r = subprocess.run(
            [sys.executable, sender, tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    finally:
        os.unlink(tmp)
    accepted = (r.stdout or "").startswith("SEND_OK")
    return ExportReceipt(
        now.isoformat(),
        code,
        candidate,
        min(candidate, MAX_EDGES),
        attempted=attempted,
        accepted=accepted,
        window_state="open",
        note=(r.stdout or r.stderr or "").strip()[:120],
    )


def append_ledger(root: Path, receipt: ExportReceipt) -> None:
    """四分账落盘: jsonl 追加, 不覆盖."""
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "intel_export_ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(__import__("json").dumps(receipt.as_dict(), ensure_ascii=False) + "\n")
