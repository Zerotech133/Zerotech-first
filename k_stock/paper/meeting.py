# -*- coding: utf-8 -*-
"""K-STOCK-145 · 经理会议线运行层（pre_open/post_close 纪要生成与投递）

数据流：hold_meeting(mid, source, kind, context)
  → manager 账本校验 active 雇佣（复用 manager 查询，不重复实现账本读取）
  → 组装 LLM 消息（system=经理档案 role_prompt+会议指令，user=当日行情摘要）
  → gaorta_client.chat_for_hire 生成纪要正文（自然语言文档，不做 JSON 解析）
  → minutes.send 投递（channel=None 时按经理档案 delivery_channels 解析默认通道）
  → 返回 (record, provider)

fail-closed：kind 非法 / 无 active 雇佣 / LLM 不可达 / 投递未成功 → MeetingError，
不静默、不落半截纪要（LLM 失败时 minutes.send 绝不被调用）。

智谱兜底通道（K-STOCK-153，默认关闭待拍板）：
gaorta 通道因 GAORTA_API_KEY 未配置而失败时，若门控开启，改经 kstock/llm.py
LLMClient（智谱官方 coding 端点，GLM_API_KEY）生成同款纪要，provider 记
"zhipu-fallback"，纪要后续投递/审计流程完全不变；兜底失败仍 MeetingError。
通道兜底不改变模型目录约束：GLM-5.2 仍属 gaorta 目录模型，仅计费通道经
智谱官方，启用须老田批准。门控读 meeting_fallback.json 的
{"allow_zhipu_fallback": true/false}，文件缺失一律视为 false（默认关）。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gaorta_client  # noqa: E402
import manager  # noqa: E402
import minutes  # noqa: E402

KINDS = ("pre_open", "post_close")

# 智谱兜底通道门控文件（K-STOCK-153；默认缺失=false，启用须老田批准）
FALLBACK_CONFIG_FP = HERE / "meeting_fallback.json"

# kstock/llm.py LLMClient（智谱官方主链）；导入失败置 None（兜底自然不可用）
try:
    _kstock_dir = str(HERE.parent)
    if _kstock_dir not in sys.path:
        sys.path.insert(0, _kstock_dir)
    from kstock.llm import LLMClient as _LLMClient  # noqa: E402
except ImportError:
    _LLMClient = None

_KIND_LABELS = {"pre_open": "盘前会", "post_close": "收盘会"}

_MEETING_INSTRUCTIONS = {
    "pre_open": (
        "现在召开盘前会议。请以会议纪要形式输出：① 今日策略要点（关注方向、"
        "拟操作思路与仓位纪律）；② 风险提示（隔夜外盘/消息面/持仓风险点）。"
        "结论必须附依据，不用 JSON，直接输出可归档的自然语言纪要。"
    ),
    "post_close": (
        "现在召开收盘复盘会议。请以会议纪要形式输出：① 当日复盘（行情回顾、"
        "操作得失、与计划的偏差）；② 明日计划（关注点与预案）。"
        "结论必须附依据，不用 JSON，直接输出可归档的自然语言纪要。"
    ),
}


class MeetingError(Exception):
    """会议线业务失败（校验拒绝/LLM 不可达/投递未成功），与 Reject 同款 fail-closed 语义。"""


def _resolve_channel(mid: str, profile: dict, channel: str | None) -> str:
    """channel=None 时按经理档案 delivery_channels 解析默认通道（取声明中首个注册通道）。"""
    if channel is not None:
        return channel
    declared = profile.get("delivery_channels") or []
    for ch in declared:
        if ch in minutes.DELIVERY_CHANNELS:
            return ch
    return "site"


def _zhipu_fallback_allowed(fp=None) -> bool:
    """兜底门控：读 meeting_fallback.json，文件缺失/解析失败一律 false（默认关）。"""
    fp = Path(fp) if fp is not None else FALLBACK_CONFIG_FP
    try:
        cfg = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(cfg, dict) and cfg.get("allow_zhipu_fallback", False))


def _zhipu_model_name(ledger_model) -> str | None:
    """雇佣账本模型名 → 智谱 coding 端点可用名。

    探针实测（2026-10-12）：GLM-5.2 与 glm-5.2 在智谱 coding 端点均接受
    （端点别名归一，实际服务 glm-5.3）。仅 GLM 家族可映射，非 GLM 模型
    （deepseek/kimi 等）智谱无对应 → 返回 None（兜底不可用）。
    """
    m = (ledger_model or "").strip()
    return m.lower() if m.lower().startswith("glm-") else None


def _try_zhipu_fallback(messages, ledger_model, orig_err) -> str | None:
    """智谱兜底生成（仅限 GAORTA_API_KEY 缺失类失败 + 门控开启）。

    返回纪要正文；不可兜底/兜底失败一律返回 None（调用方维持原 MeetingError）。
    通道兜底不改变模型目录约束：GLM-5.2 仍属 gaorta 目录模型，仅计费通道
    经智谱官方，启用须老田批准。
    """
    if not (isinstance(orig_err, RuntimeError) and "GAORTA_API_KEY" in str(orig_err)):
        return None
    if not _zhipu_fallback_allowed() or _LLMClient is None:
        return None
    zname = _zhipu_model_name(ledger_model)
    if not zname:
        return None
    try:
        client = _LLMClient(glm_model=zname, timeout=180)
        # glm-5.2 系思考型模型：推理消耗预算，长上下文下 4000 会导致可见正文为空
        text, _prov = client.chat(messages, json_mode=False, max_tokens=8000)
    except Exception as e:
        # 兜底失败必须显式留痕：静默 None 会让上层误报原始 gaorta 错误（排查血泪）
        print(f"[zhipu-fallback] 兜底通道异常: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if not (text or "").strip():
        # 空正文同样显式留痕：多为思考超预算，静默 None 会误传上游错误
        print("[zhipu-fallback] 兜底返回空正文（疑似思考消耗超 max_tokens 预算）", file=sys.stderr)
        return None
    return text


def hold_meeting(mid: str, source: str, kind: str,
                 context: str = "", channel: str | None = None) -> tuple[dict, str]:
    """召开会议并投递纪要。返回 (minutes 账本记录, 实际提供方模型名)。"""
    if kind not in KINDS:
        raise MeetingError(f"非法会议类型: {kind!r}（可选: {'/'.join(KINDS)}）")
    if not isinstance(context, str):
        raise MeetingError(f"非法 context: 须为字符串，得到 {type(context).__name__}")

    # ① 经理档案（mid 合法性 + role_prompt/通道声明；manager.Reject → MeetingError）
    try:
        profile = manager.load_profile(mid)
    except manager.Reject as e:
        raise MeetingError(f"会议 {kind} 无法召开: {e}") from e

    # ② active 雇佣校验（复用 manager 账本查询）
    active = [h for h in manager.hires_list()
              if h.get("manager") == mid and h.get("source") == source
              and h.get("status") == "active"]
    if not active:
        raise MeetingError(
            f"会议 {kind} 无法召开: 无 {mid}×{source} 的 active 雇佣"
            f"（账本 {manager.HIRES_PATH}），须先由经理雇佣建立")

    # ③ 组装 LLM 消息并生成纪要正文（LLM 失败 → MeetingError，send 绝不触达）
    role_prompt = profile.get("role_prompt") or (
        f"你是 K-STOCK 模拟仓投资部经理 {profile.get('name', mid)}。")
    messages = [
        {"role": "system",
         "content": role_prompt + "\n\n【会议指令】\n" + _MEETING_INSTRUCTIONS[kind]},
        {"role": "user",
         "content": context.strip() or "（调用方未提供当日行情摘要，请按通用框架输出纪要）"},
    ]
    try:
        out = gaorta_client.chat_for_hire(mid, source, messages)
    except (RuntimeError, ValueError, OSError) as e:
        body = _try_zhipu_fallback(messages, active[0].get("model"), e)
        if body is None:
            raise MeetingError(
                f"会议 {mid}×{source} {kind}: LLM 生成纪要失败（不落半截纪要）→ {e}") from e
        provider_tag = "zhipu-fallback"
    else:
        body = (out or {}).get("content") or ""
        provider_tag = None
    if not body.strip():
        raise MeetingError(f"会议 {mid}×{source} {kind}: LLM 返回空纪要（不落半截纪要）")

    # ④ 投递纪要
    title = f"{profile.get('name', mid)} {_KIND_LABELS[kind]} {datetime.now().strftime('%m-%d')}"
    ch = _resolve_channel(mid, profile, channel)
    try:
        rec = minutes.send(mid, kind, title, body, channel=ch,
                           meta={"source": source, "provider": provider_tag
                                 or (out or {}).get("model")})
    except minutes.Reject as e:
        raise MeetingError(f"会议 {mid}×{source} {kind}: 纪要投递被拒 → {e}") from e
    if rec.get("status") != "delivered":
        raise MeetingError(
            f"会议 {mid}×{source} {kind}: 纪要投递未成功"
            f"（channel={ch}, status={rec.get('status')}, meta={rec.get('meta')}）")
    return rec, provider_tag or (out or {}).get("model") or active[0].get("model") or "unknown"


# ── CLI ──────────────────────────────────────────────────────────
def _main(argv=None):
    ap = argparse.ArgumentParser(prog="meeting", description="经理会议线（纪要生成与投递）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("hold", help="召开会议并投递纪要")
    p.add_argument("mid")
    p.add_argument("source")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("--context-file", default=None, help="当日行情摘要文本文件（缺省为空）")
    p.add_argument("--channel", default=None, help="投递通道（缺省按经理档案 delivery_channels）")
    p.add_argument("--json", action="store_true")

    def _hold(a):
        ctx = Path(a.context_file).read_text(encoding="utf-8") if a.context_file else ""
        rec, provider = hold_meeting(a.mid, a.source, a.kind, context=ctx, channel=a.channel)
        return [{"record": rec, "provider": provider}]

    p.set_defaults(fn=_hold)
    p = sub.add_parser("list", help="纪要账本查询（透传 minutes）")
    p.add_argument("mid", nargs="?", default=None)
    p.set_defaults(fn=lambda a: minutes.list_minutes(a.mid))
    args = ap.parse_args(argv)
    try:
        print(json.dumps(args.fn(args), ensure_ascii=False, indent=1))
        return 0
    except (MeetingError, manager.Reject, minutes.Reject) as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
