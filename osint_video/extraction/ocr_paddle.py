"""PaddleOCR（PP-OCRv4）OCR 通道 + 时间轴相邻合并（架构 §2.4 步骤 4）。

- 重依赖（paddleocr/paddlepaddle）**延迟导入**：仅 :func:`recognize` 执行时 import；
- 模型缺失/加载失败抛 :class:`OCREngineUnavailableError`，上层降级为仅 ASR；
- PaddleOCR 2.x/3.x API 已做兼容（``ocr()`` 与 ``predict()`` 双路径 + 结果形状自适应）；
- 输出 ``ocr_items``：``[{"start": 秒, "end": 秒, "text": str, "conf": float}]``——
  同屏字幕在时间轴上相邻且文本一致时合并为一条（去重抖动/重复条目）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

#: PaddleOCR 安装指引（OCR 通道降级时提示）
PADDLE_INSTALL_HINT = (
    "PaddleOCR 未安装或引擎初始化失败。OCR 通道需要：\n"
    "  pip install 'paddlepaddle>=2.6' 'paddleocr>=2.7'\n"
    "  （NPU/GPU 版按 PaddlePaddle 官网指引替换 paddlepaddle 包）\n"
    "  首次运行需联网下载 PP-OCRv4 模型（缓存 ~/.paddleocr）。\n"
    "未安装 OCR 时管线自动降级为仅 ASR 通道，本错误仅供排障参考。"
)

#: 合并判定：同文本相邻条目的最大时间间隙（秒）。1fps 抽帧下 2s ≈ 漏检 1 帧仍可合并
MERGE_GAP_SEC = 2.0

#: 过滤：OCR 行规范化后长度 <2 的碎屑（单字符噪声/装饰符号）
MIN_TEXT_LEN = 2


class OCREngineUnavailableError(RuntimeError):
    """OCR 引擎不可用（未安装 paddle / 引擎初始化失败）。"""


class OCRItem(TypedDict):
    """OCR 合并后条目（对齐管线的 ocr 通道输入单元）。"""

    start: float
    end: float
    text: str
    conf: float


# ---------------------------------------------------------------------------
# 文本规范化（合并判定的基准）
# ---------------------------------------------------------------------------
_JUNK_RE = re.compile(r"[\s\u3000·、，。,.!！?？:：;；'\"“”‘’()（）\[\]【】|\-_/\\]+")


def normalize_text(text: str) -> str:
    """去除空白与常见标点后的小写串（用于合并/对齐比较，不改变原文）。"""
    return _JUNK_RE.sub("", text).lower()


def _create_engine():
    """延迟导入 PaddleOCR 并构造引擎（2.x/3.x 参数兼容）。"""
    try:
        from paddleocr import PaddleOCR  # 延迟导入：重依赖
    except ImportError as exc:
        raise OCREngineUnavailableError(PADDLE_INSTALL_HINT) from exc
    # 2.x 需要 show_log=False 关闭刷屏；3.x 移除该参数 → TypeError 降级重试
    for kwargs in (
        {"use_angle_cls": True, "lang": "ch", "show_log": False},
        {"use_angle_cls": True, "lang": "ch"},
        {"lang": "ch"},
    ):
        try:
            return PaddleOCR(**kwargs)
        except (TypeError, ValueError):
            continue
    try:
        return PaddleOCR()
    except Exception as exc:  # noqa: BLE001
        raise OCREngineUnavailableError(
            f"PaddleOCR 引擎初始化失败：{exc}\n{PADDLE_INSTALL_HINT}"
        ) from exc


def _run_ocr(engine: Any, image_path: Path) -> Any:
    """调用引擎识别单帧：优先 2.x ``ocr()``，回退 3.x ``predict()``。"""
    if hasattr(engine, "ocr"):
        try:
            return engine.ocr(str(image_path), cls=True)
        except TypeError:  # 3.x ocr() 移除 cls 参数
            return engine.ocr(str(image_path))
    if hasattr(engine, "predict"):
        return engine.predict(str(image_path))
    raise OCREngineUnavailableError(f"无法识别的 PaddleOCR 引擎对象：{type(engine)!r}")


def _parse_ocr_output(raw: Any) -> list[tuple[str, float]]:
    """自适应解析 2.x/3.x 两种结果形状 → [(text, conf), ...]。

    - 2.x ``ocr()``：``[[box, (text, conf)], ...]``（外层按图分组）
    - 3.x ``predict()``：``[{'rec_texts': [...], 'rec_scores': [...]}]``
    """
    out: list[tuple[str, float]] = []
    if not raw:
        return out
    for block in raw:
        if block is None:
            continue
        if isinstance(block, dict):
            texts = block.get("rec_texts") or []
            scores = block.get("rec_scores") or []
            out.extend((str(t).strip(), float(s)) for t, s in zip(texts, scores))
        elif isinstance(block, (list, tuple)):
            for pair in block:
                if (
                    isinstance(pair, (list, tuple))
                    and len(pair) >= 2
                    and isinstance(pair[1], (list, tuple))
                    and len(pair[1]) >= 2
                ):
                    text, conf = pair[1][0], pair[1][1]
                    out.append((str(text).strip(), float(conf)))
    return [(t, c) for t, c in out if t]


# ---------------------------------------------------------------------------
# 时间轴相邻合并
# ---------------------------------------------------------------------------
def _merge_adjacent(
    raw_items: list[OCRItem], gap_sec: float = MERGE_GAP_SEC
) -> list[OCRItem]:
    """同文本（规范化后）且时间相邻的条目合并为一条。

    - 时间窗：``start2 - end1 <= gap_sec`` 视为相邻（容忍帧去重造成的空洞）；
    - 置信度：按文本长度加权平均（长文本权重高）；
    - 文本：保留首个出现的原文（同屏字幕通常逐字相同）。
    """
    merged: list[OCRItem] = []
    for item in sorted(raw_items, key=lambda x: x["start"]):
        if merged:
            last = merged[-1]
            same_text = normalize_text(last["text"]) == normalize_text(item["text"])
            adjacent = item["start"] - last["end"] <= gap_sec
            if same_text and adjacent and item["start"] >= last["start"]:
                w_last, w_cur = len(last["text"]), len(item["text"])
                total_w = w_last + w_cur
                last["end"] = max(last["end"], item["end"])
                last["conf"] = round(
                    (last["conf"] * w_last + item["conf"] * w_cur) / max(total_w, 1), 4
                )
                continue
        merged.append(
            OCRItem(
                start=item["start"],
                end=item["end"],
                text=item["text"],
                conf=item["conf"],
            )
        )
    return merged


def recognize(
    frames: list[dict[str, Any]],
    engine: Any | None = None,
    frame_lifetime: float = 1.0,
) -> list[OCRItem]:
    """OCR 识别帧序列 → 时间轴合并后的 ocr_items。

    Args:
        frames: 抽帧索引（extraction.frame_extract.extract_frames 的返回值，
                每项含 ``ts_sec``/``path``）。
        engine: 已构造的 PaddleOCR 引擎（批量场景复用）；None 时现场构造。
        frame_lifetime: 单帧文本的存在时长（秒）——帧 t 识别的文本视为
            覆盖 ``[t, t+frame_lifetime)``，供合并与后续对齐使用。

    Returns:
        ``[{start, end, text, conf}]``（按 start 升序）；无字幕返回 ``[]``。

    Raises:
        OCREngineUnavailableError: paddle 未安装 / 引擎初始化失败（上层降级为仅 ASR）。
    """
    if not frames:
        return []
    active_engine = engine if engine is not None else _create_engine()

    raw_items: list[OCRItem] = []
    for frame in frames:
        ts = float(frame.get("ts_sec", 0.0))
        path = Path(frame["path"])
        if not path.exists():
            logger.warning("帧文件缺失，跳过 OCR：%s", path)
            continue
        try:
            lines = _parse_ocr_output(_run_ocr(active_engine, path))
        except OCREngineUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("单帧 OCR 失败（%s）：%s", path.name, exc)
            continue
        for text, conf in lines:
            if len(normalize_text(text)) < MIN_TEXT_LEN:
                continue  # 过滤碎屑噪声
            raw_items.append(
                OCRItem(
                    start=ts,
                    end=round(ts + frame_lifetime, 3),
                    text=text,
                    conf=round(conf, 4),
                )
            )

    items = _merge_adjacent(raw_items)
    logger.info(
        "OCR 完成：%d 帧 → 原始 %d 条 → 合并后 %d 条",
        len(frames),
        len(raw_items),
        len(items),
    )
    return items
