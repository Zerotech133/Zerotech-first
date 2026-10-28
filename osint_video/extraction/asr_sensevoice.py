"""SenseVoice-Small ASR 通道（FunASR + fsmn-vad，架构 §2.4 步骤 2）。

- 重依赖（funasr/torch）**延迟导入**：仅 :func:`transcribe` 执行时才 import，
  队列等模块在无模型环境可独立运行；
- 模型缺失/加载失败抛 :class:`ASRModelUnavailableError`（信息含安装指引），
  由上层管线捕获后降级为"仅 OCR 通道"；
- 输出 ``asr_segments``：``[{"start": 秒, "end": 秒, "text": str, "conf": float}]``。

已知限制：SenseVoice-Small 官方实现不提供 token 级置信度，``conf`` 使用
默认常量 :data:`DEFAULT_ASR_CONF` 占位（0.80）。质量评估以覆盖率 +
人工抽听 CER 为准（PoC 验收线 CER≤8%），后续可换带置信度的模型。
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

#: 默认模型与 VAD（ModelScope 仓库 id，首次运行自动下载）
MODEL_ID = "iic/SenseVoiceSmall"
VAD_MODEL_ID = "fsmn-vad"

#: ASR 段默认置信度占位值（SenseVoice 无 token 置信度输出）
DEFAULT_ASR_CONF = 0.80

#: funasr 安装指引（ASR 通道降级时提示）
FUNASR_INSTALL_HINT = (
    "FunASR 未安装或模型加载失败。ASR 通道需要：\n"
    "  pip install 'funasr>=1.1'\n"
    "  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu\n"
    "  首次运行需联网从 ModelScope 下载模型（约 1GB，缓存 ~/.cache/modelscope）。\n"
    "未安装 ASR 时管线自动降级为仅 OCR 通道，本错误仅供排障参考。"
)


class ASRModelUnavailableError(RuntimeError):
    """ASR 模型不可用（未安装 funasr / torch 缺失 / 模型加载失败）。"""


class ASRSegment(TypedDict):
    """ASR 识别分段（对齐管线的 asr 通道输入单元）。"""

    start: float
    end: float
    text: str
    conf: float


def _wav_duration_sec(wav_path: Path) -> float:
    """用标准库 wave 读取 wav 时长（秒）；异常返回 0。"""
    try:
        with wave.open(str(wav_path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate() or 16000)
    except (wave.Error, OSError) as exc:
        logger.warning("读取 wav 时长失败（%s），按 0 处理", exc)
        return 0.0


def _load_model(model_dir: str | None = None):
    """延迟导入 funasr 并加载 SenseVoice-Small + VAD 模型（失败抛清晰错误）。"""
    try:
        from funasr import AutoModel  # 延迟导入：重依赖
    except ImportError as exc:
        raise ASRModelUnavailableError(FUNASR_INSTALL_HINT) from exc
    try:
        return AutoModel(
            model=model_dir or MODEL_ID,
            vad_model=VAD_MODEL_ID,
            vad_kwargs={"max_single_segment_time": 30000},  # VAD 单段上限 30s
            device="cpu",
            disable_update=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise ASRModelUnavailableError(
            f"SenseVoice 模型加载失败：{exc}\n{FUNASR_INSTALL_HINT}"
        ) from exc


def _ms_to_sec(value: Any) -> float:
    """毫秒值 → 秒（容错 None/异常类型，返回 0.0）。"""
    try:
        return round(float(value) / 1000.0, 3)
    except (TypeError, ValueError):
        return 0.0


def _parse_result(item: dict[str, Any], fallback_duration: float) -> list[ASRSegment]:
    """解析 funasr generate() 单条结果 → 分段列表。

    优先取 ``sentence_info``（VAD 分段 + 时间戳）；缺失时退化为整段。
    """
    segments: list[ASRSegment] = []
    for si in item.get("sentence_info") or []:
        text = str(si.get("text", "")).strip()
        if not text:
            continue
        start = _ms_to_sec(si.get("start"))
        end = _ms_to_sec(si.get("end"))
        if end <= start:  # 时间戳异常时退化为紧邻上一段
            start, end = 0.0, fallback_duration
        segments.append(
            ASRSegment(start=start, end=end, text=text, conf=DEFAULT_ASR_CONF)
        )
    if not segments:
        text = str(item.get("text", "")).strip()
        if text:
            segments.append(
                ASRSegment(
                    start=0.0, end=fallback_duration, text=text, conf=DEFAULT_ASR_CONF
                )
            )
    return segments


def transcribe(
    wav_path: str | Path,
    model: Any | None = None,
    model_dir: str | None = None,
) -> list[ASRSegment]:
    """转录 16k wav → asr_segments（按 start 升序）。

    Args:
        wav_path: 16kHz 单声道 wav（extraction.audio_extract.extract_wav16k 产出）。
        model: 已加载的 funasr 模型（批量场景复用，避免反复加载）；None 时现场加载。
        model_dir: 本地模型目录（离线部署用；缺省用 ModelScope id 在线拉取）。

    Returns:
        ``[{start, end, text, conf}]``；空音频/无语音返回 ``[]``。

    Raises:
        ASRModelUnavailableError: funasr 未安装或模型加载失败（上层降级为仅 OCR）。
        FileNotFoundError: wav 不存在。
    """
    path = Path(wav_path)
    if not path.exists():
        raise FileNotFoundError(f"wav 文件不存在：{path}")
    active_model = model if model is not None else _load_model(model_dir)
    try:
        results = active_model.generate(
            input=str(path),
            cache={},
            language="auto",  # 视频号内容中英混合常见
            use_itn=True,  # 逆文本正则：数字/日期规范化
            batch_size_s=60,  # VAD 分批：每批 60 秒音频
            merge_vad=True,  # 合并相邻 VAD 段 → sentence_info 带时间戳
        )
    except ASRModelUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ASRModelUnavailableError(
            f"ASR 推理失败：{exc}\n{FUNASR_INSTALL_HINT}"
        ) from exc

    duration = _wav_duration_sec(path)
    segments: list[ASRSegment] = []
    for item in results or []:
        segments.extend(_parse_result(item or {}, duration))
    segments.sort(key=lambda seg: seg["start"])
    logger.info(
        "ASR 完成：%s → %d 段（音频 %.1fs）", path.name, len(segments), duration
    )
    return segments
