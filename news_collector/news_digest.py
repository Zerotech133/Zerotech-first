# -*- coding: utf-8 -*-
"""金融新闻日报 v2 — 每日 08:30：汇总近 24h 新闻，飞书互动卡推送（K-STOCK-348）。

升级点（相对 v1 纯文本）：
  ① 卡片化：card_build.build_card（v1 兼容按钮，手机可点——判例见其 docstring）
  ② 舆论风向：聚合 news.jev 列（AI 多空判定 bias+conf）24h 分布 + 高置信信号
  ③ 尾部按钮：直达公网选股看板（域名/访问码单点=portal/feishu_card_v2）
  ④ 降级保底：卡片发送失败 → 原纯文本链路照发（send_feishu 文本模式）

结构（卡片 md 块序）:
  🧭 舆论风向（bias 分布 + 高置信信号）
  ▍🌍 全球财经（8 条） ▍🇨🇳 市场要闻（8 条） ▍⚡ 7x24 快讯（8 条）
  ▍⭐ 关注命中（10 条） + 按钮「今日选股看板」
推完标记 digested；条目过少跳过（原逻辑保留）。
用法: python news_digest.py [--dry] [--text]   # --text=强制降级链路（对照）
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")
LOG_PATH = os.path.join(HERE, "logs", "digest.log")
CST = timezone(timedelta(hours=8))

SECTION_ORDER = ["wallstreetcn", "sina", "eastmoney"]
PER_SECTION = 8
TITLE_W = 52          # 卡片内标题截宽（手机屏一屏 2 行内）
HITS_LIMIT = 10
# 高置信信号：conf 达标才有资格上榜；scope（涉及标的/板块）非空的优先
HI_CONF = 0.80
SIGNAL_TOP = 4

sys.path.insert(0, HERE)
from card_build import build_card, push_card  # noqa: E402


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def push_text(text):
    """降级链路：纯文本（原 v1 推送，保底不回归）。"""
    tmp = os.path.join(HERE, "outbox", "pending_digest.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "send_feishu.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = (r.stdout or "").strip()
    log("push(text) -> " + out)
    return out.startswith("SEND_OK")


def record_flow(ok, kind="daily:news_digest"):
    try:  # 统一发送流水（商业化计费依据；fail-safe）
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import push_flow
        push_flow.record(kind=kind, channel="feishu",
                         target="个人", count=1, ok=ok)
    except Exception:
        pass


def _picks_url():
    """公网看板按钮 URL——单点收敛在 portal/feishu_card_v2（K-STOCK-305 判例）。"""
    try:
        sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "portal")))
        from feishu_card_v2 import PORTAL_BASE, PORTAL_KEY
        return "%s/picks.html?key=%s" % (PORTAL_BASE, PORTAL_KEY)
    except Exception:
        return None


# ---------- 舆论风向（news.jev 聚合） ----------

def _bar(pct, width=10):
    """纯文本比例条：▓ 已占 ░ 空（A股色语：红=多/绿=空，此处只用字形）。"""
    n = int(round(pct * width))
    return "▓" * n + "░" * (width - n)


def sentiment_block(db, since):
    """聚合近 24h jev 判定 → 舆论风向 md 块。"""
    rows = db.execute(
        "SELECT title, jev FROM news WHERE jev IS NOT NULL AND jev != '' "
        "AND created_at >= ?", (since,)).fetchall()
    if not rows:
        return None
    cnt = {"bull": 0, "bear": 0, "neutral": 0}
    parsed = []
    for title, jev in rows:
        try:
            j = json.loads(jev)
        except ValueError:
            continue
        b = j.get("bias")
        if b not in cnt:
            continue
        cnt[b] += 1
        parsed.append((title, j))
    total = sum(cnt.values())
    if not total:
        return None
    pb, pn, ps = (cnt["bull"] / total, cnt["neutral"] / total, cnt["bear"] / total)
    # 风向定性：偏多占比 - 偏空占比
    skew = pb - ps
    if skew > 0.10:
        tone, tone_color = "整体偏暖", "red"      # A 股语境：红=多
    elif skew < -0.10:
        tone, tone_color = "整体偏冷", "green"
    else:
        tone, tone_color = "多空均衡", "grey"

    lines = ["**🧭 舆论风向**（AI 判定 %d 条 · 近 24h）" % total, ""]
    lines.append("<font color='red'>偏多 %d · %s %.0f%%</font>" % (cnt["bull"], _bar(pb), pb * 100))
    lines.append("<font color='grey'>中性 %d · %s %.0f%%</font>" % (cnt["neutral"], _bar(pn), pn * 100))
    lines.append("<font color='green'>偏空 %d · %s %.0f%%</font>" % (cnt["bear"], _bar(ps), ps * 100))
    lines.append("")
    lines.append("判定：**<font color='%s'>%s</font>**（多空差 %+.0f%%）" % (tone_color, tone, skew * 100))

    # 高置信信号：scope 非空优先、按 conf 降序，多空各取头部的混合榜
    sigs = [p for p in parsed
            if p[1].get("conf", 0) >= HI_CONF and p[1].get("bias") in ("bull", "bear")]
    sigs.sort(key=lambda p: (bool(p[1].get("scope")), p[1].get("conf", 0)), reverse=True)
    seen, picked = set(), []
    for title, j in sigs:
        key = title[:20]
        if key in seen:
            continue
        seen.add(key)
        icon = "🐂" if j["bias"] == "bull" else "🐻"
        scope = (j.get("scope_cn") or j.get("scope") or "").strip()
        tag = "【%s】" % scope[:8] if scope else ""
        picked.append("%s %s%s（%s %.2f）" % (
            icon, tag, title[:TITLE_W], j.get("bias_cn", ""), j.get("conf", 0)))
        if len(picked) >= SIGNAL_TOP:
            break
    if picked:
        lines.append("")
        lines.append("**高置信信号**（conf ≥ %.2f）" % HI_CONF)
        lines.extend("· " + s for s in picked)
    return "\n".join(lines)


# ---------- 主流程 ----------

def main():
    dry = "--dry" in sys.argv
    force_text = "--text" in sys.argv
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    sections = cfg.get("digest_sections", {})
    db = sqlite3.connect(DB)
    since = (datetime.now(CST) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    day = datetime.now(CST).strftime("%m-%d")

    sec_blocks, ids, n_total = [], [], 0
    for src in SECTION_ORDER:
        rows = db.execute(
            "SELECT id, ts, title FROM news WHERE source=? AND created_at>=? "
            "ORDER BY created_at DESC, ts DESC LIMIT ?",
            (src, since, PER_SECTION)).fetchall()
        label = sections.get(src, src)
        if not rows:
            continue
        ids.extend(r[0] for r in rows)
        n_total += len(rows)
        md = ["**▍%s（%d 条）**" % (label, len(rows))]
        md += ["· %s" % r[2][:TITLE_W] for r in rows]
        sec_blocks.append("\n".join(md))

    hit_rows = db.execute(
        "SELECT title FROM news WHERE hit=1 AND created_at>=? ORDER BY created_at DESC LIMIT ?",
        (since, HITS_LIMIT * 2)).fetchall()
    seen_hits, hits = set(), []
    for (t,) in hit_rows:  # 同题去重（源间转载常见）
        k = t[:20]
        if k not in seen_hits:
            seen_hits.add(k)
            hits.append((t,))
        if len(hits) >= HITS_LIMIT:
            break
    hit_rows = hits

    if len(ids) < 3 and not dry:
        log("digest skipped: too few items (%d)" % len(ids))
        db.close()
        print("DIGEST SKIP")
        return

    sent = False
    if not force_text:
        sent = push_card_flow(db, since, day, n_total, sec_blocks, hit_rows, dry)
    if not sent:  # 降级：纯文本（原 v1 链路，保底不回归）
        sent = fallback_text_flow(day, sections, n_total, sec_blocks, hit_rows, dry)
    if sent:
        db.executemany("UPDATE news SET digested=1 WHERE id=?", [(i,) for i in ids])
        db.execute("INSERT INTO runs(kind,ts,ok,new_count,push_count,detail) VALUES(?,?,?,?,?,?)",
                   ("digest", datetime.now(CST).strftime("%Y-%m-%d %H:%M"), 1,
                    0, 1, "card=%s items=%d" % (not force_text, len(ids))))
        db.commit()
    db.close()
    print("DIGEST %s items=%d card=%s" % ("SENT" if sent else "FAIL", len(ids), not force_text))


def push_card_flow(db, since, day, n_total, sec_blocks, hit_rows, dry):
    """卡片链路：舆论风向 + 版块 + 关注命中 + 看板按钮。失败返回 False 由上层降级。"""
    blocks = []
    sb = sentiment_block(db, since)
    if sb:
        blocks.append(sb)
    blocks.extend(sec_blocks)
    if hit_rows:
        md = ["**▍⭐ 关注命中（%d 条）**" % len(hit_rows)]
        md += ["· %s" % t[0][:TITLE_W] for t in hit_rows]
        blocks.append("\n".join(md))

    buttons = []
    url = _picks_url()
    if url:
        buttons.append(("今日选股看板", url, "primary"))
    card = build_card(
        "📰 金融新闻日报 · %s" % day,
        "近 24 小时 · %d 条 · 信息采集域" % n_total,
        "turquoise", blocks,
        footer="AI 工作站 · 信息采集域 · 每日 08:30 · 发送失败自动降级文本",
        buttons=buttons)
    if dry:
        print(json.dumps(card, ensure_ascii=False, indent=1)[:4000])
        return True
    ok = push_card(card)
    log("push(card) -> %s" % ok)
    record_flow(ok)
    return ok


def fallback_text_flow(day, sections, n_total, sec_blocks, hit_rows, dry):
    """纯文本链路（v1 原样 + 舆论一行摘要）。"""
    lines = ["📰 金融新闻日报 · %s" % day, "（近 24 小时，按源分版）", ""]
    for b in sec_blocks:
        lines.append(b.replace("**", ""))
        lines.append("")
    if hit_rows:
        lines.append("▍⭐ 关注命中（%d 条）" % len(hit_rows))
        lines += ["· %s" % t[0][:64] for t in hit_rows]
        lines.append("")
    lines.append("—— AI 工作站 · 信息采集域 · 每日 08:30")
    text = "\n".join(lines)
    if dry:
        print(text[:2000])
        return True
    ok = push_text(text)
    record_flow(ok)
    return ok


if __name__ == "__main__":
    main()
