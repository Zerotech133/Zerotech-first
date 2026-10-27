"""抽取管线编排：decrypted 视频 → StructuredDoc（架构 §2.4 / §3）。

流程（``run(video_path, video_id)``）::

    1. probe_duration_sec(mp4)                          # ffprobe 探时长
    2. [并行] extract_wav16k → SenseVoice ASR           # 音频通道
             extract_frames(1fps) → PaddleOCR           # 视频通道
    3. AlignMerger.merge(asr, ocr)                      # 双通道时间戳对齐
    4. 组装 StructuredDoc（§3 schema，schema_version="1.0"）

断点重跑（架构 §7.9）：各阶段中间产物落盘 ``data/work/{video_id}/``
（audio.wav / frames/ / asr_segments.json / frame_index.json /
ocr_items.json / structured_doc.json），已存在且非 ``force`` 的检查点
直接复用；``structured_doc.json`` 存在即整条完成。

降级（架构 §7.7）：ASR 挂 → 仅 OCR；OCR 挂 → 仅 ASR；双挂则抛
:class:`ExtractionError`（无文字可提取，视为硬失败）。
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.logging_setup import setup_logging
from common.paths import COLLECTOR_VERSION, work_dir_for
from extraction.align_merge import merge_channels
from extraction.audio_extract import extract_wav16k, probe_duration_sec
from extraction.frame_extract import extract_frames
from extraction.ocr_paddle import OCRItem  # noqa: F401 —— 类型再导出便于调用方注解

logger = logging.getLogger(__name__)

#: StructuredDoc schema 版本（§3）
SCHEMA_VERSION = "1.0"

#: 视频文件扩展名白名单
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".flv", ".ts"}


class ExtractionError(RuntimeError):
    """抽取管线硬失败（视频损坏 / ffmpeg 缺失 / 双通道皆不可用）。"""


# ---------------------------------------------------------------------------
# 检查点读写
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> Any | None:
    """读 JSON 检查点；不存在/损坏返回 None（损坏视为无检查点，重跑该阶段）。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("检查点损坏，将重跑该阶段：%s（%s）", path.name, exc)
        return None


def _write_json(path: Path, payload: Any) -> None:
    """写 JSON 检查点（ensure_ascii=False，缩进 2，便于人工检视）。"""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 双通道执行（并行）
# ---------------------------------------------------------------------------
def _run_audio_channel(
    video_path: Path, work_dir: Path, force: bool
) -> tuple[list[dict[str, Any]], str | None]:
    """音频通道：wav → ASR。返回 (asr_segments, 降级原因或 None)。"""
    wav_path = work_dir / "audio.wav"
    seg_path = work_dir / "asr_segments.json"

    if not force and wav_path.exists() and _read_json(seg_path) is not None:
        return _read_json(seg_path) or [], None  # 检查点齐备，跳过

    try:
        if force or not wav_path.exists():
            extract_wav16k(video_path, wav_path)
    except Exception as exc:  # noqa: BLE001 —— ffmpeg 属硬错误，但仅音频通道先降级
        raise ExtractionError(f"音频抽取失败：{exc}") from exc

    # ASR：重依赖延迟导入（asr_sensevoice 内部处理）
    from extraction.asr_sensevoice import (
        ASRModelUnavailableError,
        transcribe,
    )  # 延迟导入

    try:
        segments = transcribe(wav_path)
        _write_json(seg_path, segments)
        return segments, None
    except ASRModelUnavailableError as exc:
        logger.warning("ASR 通道降级（仅 OCR）：%s", exc)
        return [], "asr_unavailable"


def _run_video_channel(
    video_path: Path, work_dir: Path, force: bool
) -> tuple[list[dict[str, Any]], str | None]:
    """视频通道：抽帧 → OCR。返回 (ocr_items, 降级原因或 None)。"""
    frames_dir = work_dir / "frames"
    idx_path = work_dir / "frame_index.json"
    ocr_path = work_dir / "ocr_items.json"

    if not force and _read_json(ocr_path) is not None:
        return _read_json(ocr_path) or [], None  # OCR 检查点齐备，跳过

    # 抽帧（硬错误不降级：帧坏了 OCR 无从谈起；已增强帧存在则复用）
    frames: list[dict[str, Any]]
    if not force and _read_json(idx_path) is not None:
        frames = _read_json(idx_path) or []
    else:
        try:
            frames = extract_frames(video_path, frames_dir)  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"抽帧失败：{exc}") from exc
        _write_json(idx_path, frames)

    from extraction.ocr_paddle import OCREngineUnavailableError, recognize  # 延迟导入

    try:
        items = recognize(frames)
        _write_json(ocr_path, items)
        return items, None
    except OCREngineUnavailableError as exc:
        logger.warning("OCR 通道降级（仅 ASR）：%s", exc)
        return [], "ocr_unavailable"


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------
def _coverage(segments: list[dict[str, Any]], duration: float) -> float:
    """通道覆盖率：有效文本时长 / 视频时长（上限 1.0；时长未知返回 0）。"""
    if duration <= 0 or not segments:
        return 0.0
    covered = sum(
        max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segments
    )
    return round(min(covered / duration, 1.0), 4)


def _avg_conf(segments: list[dict[str, Any]]) -> float:
    """平均置信度（空列表返回 0）。"""
    if not segments:
        return 0.0
    return round(sum(float(s.get("conf", 0.0)) for s in segments) / len(segments), 4)


def build_structured_doc(
    video_id: str,
    duration: float,
    asr_segments: list[dict[str, Any]],
    ocr_items: list[dict[str, Any]],
    aligned: list[dict[str, Any]],
    *,
    source_url: str | None = None,
    route: str = "MANUAL",
    file_md5: str | None = None,
    video_path: str | None = None,
) -> dict[str, Any]:
    """组装 StructuredDoc（架构 §3 schema）。

    说明：
    - ``author``/``publish_time``/``entities``/``topics``/``compliance`` 为
      占位结构，由采集解析链（T02）与合规层（T05）回填；
    - PoC 期 route 固定 ``MANUAL``（官方 exe 手工下载），源字段如实记录。
    """
    asr_text = "\n".join(str(s.get("text", "")) for s in asr_segments)
    ocr_text = "\n".join(str(o.get("text", "")) for o in ocr_items)
    return {
        "video_id": video_id,
        "schema_version": SCHEMA_VERSION,
        "source": {
            "route": route,
            "sph_url": source_url,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "collector_version": COLLECTOR_VERSION,
            "file_md5": file_md5,
            "media_retention": "temporary",
        },
        "author": {"name": None, "author_id": None, "verified": None},
        "publish_time": None,
        "duration": round(duration, 3),
        "file_md5": file_md5,
        "asr_text": asr_text,
        "ocr_text": ocr_text,
        "aligned_segments": aligned,
        "entities": [],  # T05：规则/LLM 实体抽取回填
        "topics": [],  # T05：主题聚类回填
        "compliance": {  # T05：ComplianceFilter 回填（当前为占位默认值）
            "whitelisted": None,
            "whitelist_rule": None,
            "pii_masked": False,
            "pii_items": 0,
            "minhash": None,
            "duplicate_of": None,
            "quarantine": None,
            "internal_use_only": True,
        },
        "metrics": {
            "asr_segments_count": len(asr_segments),
            "ocr_items_count": len(ocr_items),
            "aligned_count": len(aligned),
            "asr_avg_conf": _avg_conf(asr_segments),
            "ocr_avg_conf": _avg_conf(ocr_items),
            "asr_coverage": _coverage(asr_segments, duration),
            "ocr_coverage": _coverage(ocr_items, duration),
        },
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def run(
    video_path: str | Path,
    video_id: str,
    *,
    source_url: str | None = None,
    file_md5: str | None = None,
    force: bool = False,
    enable_asr: bool = True,
    enable_ocr: bool = True,
) -> dict[str, Any]:
    """抽取管线主入口（编排 §2.4 六步）。

    Args:
        video_path: 明文视频文件路径（mp4 等）。
        video_id: 全系统唯一视频键（优先 sph exportId；PoC 用文件名去扩展名）。
        source_url: 溯源 sph 链接（PoC 手工样本通常为 None）。
        file_md5: 视频 MD5（调用方已算好则传入；缺省不计算——避免大文件二次 IO）。
        force: True 时忽略全部检查点强制重跑。
        enable_asr / enable_ocr: 通道开关（PoC 单通道验证 / 显式降级用）。

    Returns:
        StructuredDoc dict（同时落盘 ``data/work/{video_id}/structured_doc.json``）。

    Raises:
        ExtractionError: 视频不存在/损坏/ffmpeg 缺失/双通道皆不可用。
    """
    setup_logging()
    src = Path(video_path)
    if not src.exists():
        raise ExtractionError(f"视频文件不存在：{src}")
    if src.suffix.lower() not in VIDEO_EXTS:
        logger.warning(
            "非常见视频扩展名 %s，仍按 ffmpeg 可解容器尝试：%s", src.suffix, src.name
        )

    work_dir = work_dir_for(video_id)
    doc_path = work_dir / "structured_doc.json"
    if not force:
        cached = _read_json(doc_path)
        if cached is not None:
            logger.info("命中最终检查点，跳过重跑：%s", doc_path)
            return cached

    duration = probe_duration_sec(src)  # ffprobe 缺失时此处抛错（硬错误，含安装指引）

    # 双通道并行（wav 抽取与抽帧互不依赖；模型加载后各自占用线程）
    degraded: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        audio_future = (
            pool.submit(_run_audio_channel, src, work_dir, force)
            if enable_asr
            else None
        )
        video_future = (
            pool.submit(_run_video_channel, src, work_dir, force)
            if enable_ocr
            else None
        )
        asr_segments, asr_err = (
            audio_future.result() if audio_future else ([], "asr_disabled")
        )
        ocr_items, ocr_err = (
            video_future.result() if video_future else ([], "ocr_disabled")
        )
    if asr_err:
        degraded.append(asr_err)
    if ocr_err:
        degraded.append(ocr_err)

    if not asr_segments and not ocr_items:
        raise ExtractionError(
            f"双通道均无文字产出（degraded={degraded}）：ASR 段数=0 且 OCR 条数=0，"
            f"请检查视频内容/模型安装：{src.name}"
        )

    aligned = merge_channels(asr_segments, ocr_items)
    doc = build_structured_doc(
        video_id=video_id,
        duration=duration,
        asr_segments=asr_segments,
        ocr_items=ocr_items,
        aligned=aligned,
        source_url=source_url,
        route="MANUAL",
        file_md5=file_md5,
        video_path=str(src),
    )
    if degraded:
        doc["metrics"]["degraded"] = degraded  # 降级痕迹随文档留存
    _write_json(doc_path, doc)
    logger.info(
        "StructuredDoc 完成：%s（ASR %d 段 / OCR %d 条 / 对齐 %d 段%s）→ %s",
        video_id,
        len(asr_segments),
        len(ocr_items),
        len(aligned),
        f"，降级={degraded}" if degraded else "",
        doc_path.name,
    )
    return doc
