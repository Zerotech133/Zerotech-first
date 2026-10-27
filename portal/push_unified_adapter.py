# -*- coding: utf-8 -*-
"""P2 生产者统一分装适配器（2026-10-25）。

生产者拿旧卡 + 业务分节文本调用一次 `adapt()`：
  · 开关 disabled → 原样返回旧卡（零行为变化）
  · shadow → 记录新旧卡摘要到 shadow jsonl，仍返回旧卡
  · enabled → 返回统一分装卡（失败一律回落旧卡）
"""

from __future__ import annotations


try:
    import feishu_push as fp
except Exception:  # pragma: no cover
    fp = None


def enabled(name):
    if fp is None:
        return False
    try:
        return fp.should_unify(name)[0]
    except Exception:  # noqa: BLE001
        return False


def adapt(
    name,
    legacy,
    scene,
    title,
    sections,
    ts="",
    source="",
    footer="",
    buttons=None,
    links=None,
    level="info",
    summary=None,
    kpis=None,
):
    """旧卡 → 统一卡（按开关决定）；任何异常返回旧卡。"""
    if fp is None:
        return legacy
    try:
        send_new, shadow = fp.should_unify(name)
        if not (send_new or shadow):
            return legacy
        if summary is not None:
            items = []
            for s in sections or []:
                line = str(s).replace("**", "").strip().split("\n")[0]
                if line and line != summary:
                    items.append(line)
            new = fp.build(
                scene,
                title,
                summary=summary,
                kpis=kpis,
                items=items,
                ts=ts,
                source=source,
                level=level,
                buttons=buttons,
                links=links,
                note=footer,
                tags="#unified #%s" % name,
            )
        else:
            new = fp.from_sections(
                name,
                scene,
                title,
                sections,
                ts=ts,
                source=source,
                footer=footer,
                buttons=buttons,
                links=links,
                level=level,
            )
        if shadow:
            fp.shadow_log(name, legacy, new)
            return legacy
        return new
    except Exception:  # noqa: BLE001 —— 统一卡失败绝不反噬主链
        return legacy
