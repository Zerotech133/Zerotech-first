# -*- coding: utf-8 -*-
"""字符级模板匹配 OCR（保持比例，不拉伸）

要点
    · 5×11px 的小字符若 resize 到 14×20 会严重变形，通用 OCR 认不出
    · 改为"原尺寸贴到固定画布底部居中"，形状不变，只对齐基线
    · 模板库自举：未命中时才调 ddddocr（单字符、放大后准确率高），命中即入库
    · 同一次会话字体/字号固定 -> 命中率迅速逼近 100%
"""
from __future__ import annotations

import numpy as np

CW, CH = 12, 18          # 画布尺寸 (w,h)，足够容纳最宽字符且不缩放


def binarize(bgr: np.ndarray) -> np.ndarray:
    import cv2
    v = bgr.max(axis=2)
    if v.mean() > 160:
        v = 255 - v
    _, bw = cv2.threshold(v, max(40, int(v.max() * 0.35)), 255, cv2.THRESH_BINARY)
    return bw


def to_canvas(sub: np.ndarray) -> np.ndarray:
    """原尺寸贴到画布底部居中，保持形状"""
    h, w = sub.shape
    cv = np.zeros((CH, CW), np.float32)
    if h > CH:
        sub = sub[:CH]
        h = CH
    if w > CW:
        sub = sub[:, :CW]
        w = CW
    y0 = CH - h
    x0 = max(0, (CW - w) // 2)
    cv[y0:y0 + h, x0:x0 + w] = sub.astype(np.float32) / 255.0
    return cv


def segment(bgr: np.ndarray, min_h: int = 5, min_area: int = 5) -> list[tuple[np.ndarray, int, int]]:
    """连通域切字符 -> [(canvas, x, 原宽)]，按 x 升序"""
    import cv2
    bw = binarize(bgr)
    if bw.max() == 0:
        return []
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    parts = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if h < min_h or area < min_area:
            continue
        sub = (lab[y:y + h, x:x + w] == i).astype(np.uint8) * 255
        parts.append((x, w, h, sub))
    parts.sort(key=lambda t: t[0])
    return [(to_canvas(sub), x, w) for x, w, h, sub in parts]


class TemplateOCR:
    def __init__(self, fallback=None, max_per_label: int = 8, max_dist: float = 0.055):
        self.tmpl: dict[str, list[tuple[np.ndarray, int]]] = {}
        self.fallback = fallback
        self.max_per_label = max_per_label
        self.max_dist = max_dist
        self.hits = 0
        self.learns = 0
        self.whole_calls = 0
        self.rejects = 0

    def _match(self, cv: np.ndarray, w: int) -> tuple[str | None, float]:
        """按画布 L2 距离匹配；字宽不符加惩罚（'1' 宽 3px、'8' 宽 5px，是强特征）"""
        best, bd = None, 1e9
        for lab, arrs in self.tmpl.items():
            for t, tw in arrs:
                d = float(np.abs(t - cv).mean())
                if abs(tw - w) > 1:
                    d += 0.04 + 0.01 * abs(tw - w)
                if d < bd:
                    bd, best = d, lab
        return best, bd

    def _learn(self, cv: np.ndarray, lab: str, w: int) -> None:
        arrs = self.tmpl.setdefault(lab, [])
        if len(arrs) < self.max_per_label:
            arrs.append((cv, w))

    def _fallback_whole(self, bgr: np.ndarray) -> str:
        """整格交通用 OCR。整格比逐字符准（'168011' 全对，逐字符 8->6）。"""
        self.whole_calls += 1
        s = self.fallback.read(bgr) if self.fallback else ''
        return ''.join(ch for ch in (s or '') if ch.isdigit())

    def read(self, bgr: np.ndarray) -> str:
        parts = segment(bgr)
        if not parts:
            return ''
        med = float(np.median([w for _, _, w in parts]))
        valid = [(cv, x, w) for cv, x, w in parts if w >= med * 0.6]   # 剔除小数点窄块
        if not valid:
            return ''

        # 1) 全模板匹配：命中即返回，最快路径
        out, all_hit = [], True
        for cv, _, w in valid:
            lab, dist = self._match(cv, w)
            if lab is None or dist > self.max_dist:
                all_hit = False
                break
            out.append(lab)
        if all_hit and out:
            self.hits += len(out)
            return ''.join(out)

        # 2) 整格 fallback + 位数对齐后才自举学习（位数不符说明有漏/多识，拒绝学习）
        s = self._fallback_whole(bgr)
        if s and len(s) == len(valid):
            for (cv, _, w), ch in zip(valid, s):
                self._learn(cv, ch, w)
            self.learns += len(valid)
            return s

        self.rejects += 1
        res = []
        for cv, _, w in valid:
            lab, dist = self._match(cv, w)
            res.append(lab if (lab and dist <= self.max_dist) else '?')
        return ''.join(res) if any(c != '?' for c in res) else ''

    def stats(self) -> dict:
        return {'labels': ''.join(sorted(self.tmpl)),
                'samples': sum(len(v) for v in self.tmpl.values()),
                'hits': self.hits, 'learns': self.learns,
                'whole_calls': self.whole_calls, 'rejects': self.rejects}
