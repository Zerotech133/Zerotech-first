"""MemorySink — cowbridge 记忆沉淀 + 检索注入（学习闭环步⑥，绝不阻塞主链）。

write_lesson：异步队列（后台线程 flush）→ cowbridge daily 层生产写接口
（write_memory_to_cow 模式：markdown 小节化追加）；失败降级 JSONL 留痕
learning/memory_fallback/*.jsonl（影子纪律同款）。
search_hints：本地 JSONL 关键词匹配（v1 不强依赖 cow FTS5），注入
WatchContext.memory_hints。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path

from zhice_os_v2.agent_platform.config import utc_now

logger = logging.getLogger(__name__)

_DEFAULT_BASE = Path(__file__).resolve().parent


def default_fallback_dir() -> Path:
    return _DEFAULT_BASE / "memory_fallback"


def default_memory_store() -> Path:
    return _DEFAULT_BASE / "memory_store.jsonl"


def lesson_to_markdown(case) -> str:
    """LearningCase/决策 dict → cow daily 层 markdown 小节（## 标题 + 正文）。"""
    ts = getattr(case, "ts_code", None) or case.get("ts_code", "?") if isinstance(case, dict) else getattr(case, "ts_code", "?")
    market = case.get("market", "?") if isinstance(case, dict) else getattr(case, "market", "?")
    insight = case.get("insight", "?") if isinstance(case, dict) else getattr(case, "insight", "?")
    summary = (case.get("lesson_summary", "") if isinstance(case, dict)
               else getattr(case, "lesson_summary", "")) or ""
    pnl = (case.get("fwd_ret_5d") if isinstance(case, dict)
           else getattr(case, "pnl_pct", None))
    pnl_str = f"{float(pnl):+.2%}" if pnl is not None else "待回填"
    header = f"盯盘教训 {ts}({market}) {insight}"
    body = f"{summary}\n- 前向收益(T+5): {pnl_str}\n- 沉淀时间: {utc_now().isoformat(timespec='seconds')}"
    return f"## {header}\n{body}"


class MemorySink:
    """记忆沉淀（异步）+ 检索（本地 JSONL 关键词匹配）。

    cow_write：可注入的写函数（生产 = cowbridge.write_memory_to_cow；测试/降级
    = None → 直接落 JSONL）。注入后仍失败 → JSONL 留痕，主链零阻塞。
    """

    def __init__(self, cow_write=None, fallback_dir: str | Path | None = None,
                 store_path: str | Path | None = None, flush_interval_s: float = 1.0,
                 start_worker: bool = True) -> None:
        self._cow_write = cow_write
        self.fallback_dir = Path(fallback_dir) if fallback_dir else default_fallback_dir()
        self.store_path = Path(store_path) if store_path else default_memory_store()
        self._queue: "queue.Queue[dict | None]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.flush_interval_s = flush_interval_s
        self.written_cow = 0
        self.written_fallback = 0
        if start_worker:
            self._start_worker()

    # ── 写入（主链调用点，永不阻塞） ────────────────────────────────────────
    def write_lesson(self, case) -> bool:
        """案例 → 异步队列（立即返回 True=已入队）。后台线程 flush。"""
        payload = {
            "ts_code": getattr(case, "ts_code", None) or (case.get("ts_code") if isinstance(case, dict) else None),
            "market": getattr(case, "market", None) or (case.get("market") if isinstance(case, dict) else None),
            "insight": getattr(case, "insight", None) or (case.get("insight") if isinstance(case, dict) else None),
            "lesson_summary": (case.get("lesson_summary") if isinstance(case, dict)
                               else getattr(case, "lesson_summary", None)),
            "fwd_ret_5d": (case.get("fwd_ret_5d") if isinstance(case, dict)
                           else getattr(case, "fwd_ret_5d", None)),
            "decision_id": (case.get("replay_id") or case.get("decision_id")
                            if isinstance(case, dict) else getattr(case, "replay_id", None)),
            "md": lesson_to_markdown(case),
            "queued_at": utc_now().isoformat(),
        }
        self._queue.put(payload)
        return True

    def _start_worker(self) -> None:
        self._worker = threading.Thread(target=self._drain_loop, daemon=True,
                                        name="aw-memory-sink")
        self._worker.start()

    def _drain_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=self.flush_interval_s)
            except queue.Empty:
                continue
            if item is None:
                break
            self._flush_one(item)

    def _flush_one(self, payload: dict) -> None:
        """单条沉淀：cow 写（成功/跳过均记 JSONL 本地索引）→ 失败转 fallback。"""
        try:
            if self._cow_write is not None:
                result = self._cow_write(payload["md"])
                self.written_cow += int(result.get("written", 0)) if isinstance(result, dict) else 1
            self._append_store(payload, "cow_ok" if self._cow_write is not None else "local")
        except Exception as exc:
            logger.warning("cow 写入失败(%s: %s) → JSONL 降级留痕", type(exc).__name__, exc)
            self._append_store(payload, "fallback", error=f"{type(exc).__name__}: {exc}")
            self.written_fallback += 1

    def _append_store(self, payload: dict, route: str, error: str = "") -> None:
        rec = dict(payload)
        rec["route"] = route
        if error:
            rec["error"] = error
        line = json.dumps(rec, ensure_ascii=False, default=str)
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with self.store_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:  # 双保险：JSONL 都写不动只留日志
            logger.error("memory JSONL 写入失败: %s", exc)

    def flush(self, timeout_s: float = 5.0) -> None:
        """排空队列（测试/关停用）。"""
        deadline = utc_now().timestamp() + timeout_s
        while not self._queue.empty() and utc_now().timestamp() < deadline:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                self._flush_one(item)

    def close(self) -> None:
        self.flush()
        self._stop.set()
        self._queue.put(None)
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=3.0)

    # ── 检索（v1：本地 JSONL 关键词匹配） ───────────────────────────────────
    def search_hints(self, symbol: str, market: str, regime: str = "",
                     limit: int = 3) -> list[str]:
        """按 symbol/market 匹配历史教训，注入 WatchContext.memory_hints。

        匹配优先级：同标的 > 同市场；regime（risk_off/on）加分。返回人话 hint 列表。
        """
        hints: list[tuple[int, str]] = []
        try:
            if not self.store_path.exists():
                return []
            for line in self.store_path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                score = 0
                if rec.get("ts_code") == symbol:
                    score += 4
                if rec.get("market") == market:
                    score += 2
                if regime and str(regime).lower() in str(rec.get("lesson_summary", "")).lower():
                    score += 1
                if score <= 0 or not rec.get("lesson_summary"):
                    continue
                pnl = rec.get("fwd_ret_5d")
                pnl_str = f"{float(pnl):+.2%}" if pnl is not None else ""
                hint = (f"[历史教训 {rec.get('ts_code')}/{rec.get('insight', '')} "
                        f"{pnl_str}] {str(rec['lesson_summary'])[:120]}")
                hints.append((score, hint))
        except Exception as exc:
            logger.warning("search_hints 检索失败: %s", exc)
            return []
        hints.sort(key=lambda x: -x[0])
        return [h for _, h in hints[:limit]]
