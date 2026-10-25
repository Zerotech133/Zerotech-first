# -*- coding: utf-8 -*-
"""JEV 新闻判断图表（外网可见：飞书卡片图片推送）。

数据：news.db jev 列（近 7 天）→ matplotlib 四联图 PNG → lark-cli 上传
→ v1 卡片（img 组件）→ 田峰会话推送。手机飞书随时随地可看（不依赖内网门户）。

用法: python news_jev_chart.py [--dry]   --dry 只画图不上传不推送
挂在 news_ai.py 晨报/复盘推送成功后自动触发（Popen 后台，不阻塞）。
"""
import json
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from news_time import parse_full_time, parse_news_time
# 2026-10-25 SEC 收口（EXEC-R14）：news_config 敏感字段读 env/本地私有覆盖，见 config_local.py
from config_local import load_news_config, save_jev_doc_url

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
PNG = os.path.join(HERE, "assets", "jev_chart.png")
LARK_ROOT = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark"
CST = timezone(timedelta(hours=8))

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

BJ = {"bull": "利多", "bear": "利空", "neutral": "中性"}
SC = {"cn": "A股", "hk": "港股", "us": "美股",
      "commodity": "商品", "crypto": "加密", "macro": "宏观"}
C_BULL, C_BEAR, C_NEUT, C_GRID = "#3fb950", "#f85149", "#8b949e", "#e3e5e8"
# K-ORG-157：低置信阈值与时效衰减（实证 K-ORG-156——0.5-0.7 段命中仅
# 32-43%，0.7+ 才稳定在 41-54%；净倾向半衰期 6h 衰减加权）
LOW_CONF = 0.7
LAMBDA_H = 0.1155   # ln2/6，w=exp(-λ·Δt小时)
C_LOW = "#a3adb8"   # 低置信灰段


def load(hours=24 * 7):
    db = sqlite3.connect(DB)
    try:
        rows = db.execute(
            "SELECT ts, title, jev, source, created_at FROM news WHERE jev IS NOT NULL "
            "AND jev!=''").fetchall()
    finally:
        db.close()
    now = datetime.now(CST)
    out = []
    for ts, title, jev, source, created_at in rows:
        try:
            j = json.loads(jev)
            t = parse_full_time(created_at)
            event_t = parse_news_time(ts, reference=created_at, source=source)
            if t is None or event_t is None or not isinstance(j, dict):
                continue
            conf = float(j.get("conf") or 0)
        except Exception:
            continue  # 豁免（C2批2）：jev/时间戳坏行跳过，正常容错
        if not timedelta(0) <= now - t <= timedelta(hours=hours):
            continue
        out.append({"t": t, "event_t": event_t, "available_t": t,
                    "title": title or "", "bias": j.get("bias", "?"),
                    "conf": conf,
                    "scope": j.get("scope") or "?",
                    "reason": str(j.get("reason") or "")[:60],
                    "source": source or ""})
    out.sort(key=lambda item: item["t"], reverse=True)
    return now, out


def render(now, items):
    """四联图：24h 方向计数 / 市场多空 / 置信度直方图 / 24h 净倾向。"""
    day = [x for x in items if now - x["t"] <= timedelta(hours=24)]

    fig, axes = plt.subplots(2, 2, figsize=(10, 7.2), dpi=150)
    fig.suptitle("JEV 新闻判断 · %s（累计 %d 条 · 24h %d 条）" % (
        now.strftime("%m-%d %H:%M"), len(items), len(day)),
        fontsize=14, fontweight="bold", y=0.98)
    for ax in axes.flat:
        ax.tick_params(labelsize=9)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)

    # 1) 24h 方向计数（K-ORG-157：高置信实色 + 低置信灰叠段，低置信不参与
    #    方向信号，仅展示数量）
    ax = axes[0][0]
    hi_cnt = {b: sum(1 for x in day if x["bias"] == b
                     and x["conf"] >= LOW_CONF)
              for b in ("bull", "bear", "neutral")}
    lo_cnt = {b: sum(1 for x in day if x["bias"] == b and x["conf"] < LOW_CONF)
              for b in ("bull", "bear", "neutral")}
    ys = range(3)
    ax.barh(list(ys), [hi_cnt["bull"], hi_cnt["bear"], hi_cnt["neutral"]],
            color=[C_BULL, C_BEAR, C_NEUT], height=0.55)
    ax.barh(list(ys),
            [lo_cnt["bull"], lo_cnt["bear"], lo_cnt["neutral"]],
            left=[hi_cnt["bull"], hi_cnt["bear"], hi_cnt["neutral"]],
            color=C_LOW, height=0.55, label="低置信<70%")
    ax.set_yticks(list(ys))
    ax.set_yticklabels(["利多", "利空", "中性"])
    ax.set_title("近 24h 方向分布（灰=低置信<70%）", fontsize=11, loc="left")
    for i, b in enumerate(("bull", "bear", "neutral")):
        tot = hi_cnt[b] + lo_cnt[b]
        ax.text(tot + max(tot for tot in
                          (hi_cnt[x] + lo_cnt[x] for x in ("bull", "bear",
                                                          "neutral")))
                * 0.02, i, str(tot) if not lo_cnt[b] else
                "%d(%d低)" % (tot, lo_cnt[b]),
                va="center", fontsize=10, fontweight="bold")
    ax.set_xlim(0, max(hi_cnt[x] + lo_cnt[x] for x in
                       ("bull", "bear", "neutral")) * 1.2 + 2)
    ax.legend(fontsize=8, loc="lower right")

    # 2) 市场分组多空（近7天，左空右多）
    ax = axes[0][1]
    scopes = {}
    for x in items:
        scopes.setdefault(x["scope"], []).append(x)
    order = [s for s in ("cn", "us", "hk", "commodity", "crypto", "macro")
             if s in scopes]
    nb = [sum(1 for x in scopes[s] if x["bias"] == "bull") for s in order]
    nr = [-sum(1 for x in scopes[s] if x["bias"] == "bear") for s in order]
    ys = range(len(order))
    ax.barh(list(ys), nb, color=C_BULL, height=0.55, label="利多")
    ax.barh(list(ys), nr, color=C_BEAR, height=0.55, label="利空")
    ax.set_yticks(list(ys))
    ax.set_yticklabels([SC.get(s, s) for s in order])
    ax.axvline(0, color="#58606a", lw=0.8)
    ax.set_title("按市场（近7天 · 左空右多）", fontsize=11, loc="left")
    ax.legend(fontsize=8, loc="lower right")

    # 3) 置信度分布直方图（近7天，多空堆叠）
    ax = axes[1][0]
    los = list(range(40, 100, 10))
    buckets = {lo: {"bull": 0, "bear": 0, "neutral": 0} for lo in los}
    for x in items:
        lo = min(max(int(x["conf"] * 100) // 10 * 10, 40), 90)
        buckets[lo][x["bias"] if x["bias"] in buckets[lo] else "neutral"] += 1
    b_bull = [buckets[lo]["bull"] for lo in los]
    b_bear = [buckets[lo]["bear"] for lo in los]
    b_neut = [buckets[lo]["neutral"] for lo in los]
    ax.bar(los, b_bull, width=8, color=C_BULL, label="利多")
    ax.bar(los, b_bear, width=8, bottom=b_bull, color=C_BEAR, label="利空")
    ax.bar(los, b_neut, width=8,
           bottom=[a + b for a, b in zip(b_bull, b_bear)],
           color=C_NEUT, label="中性")
    ax.set_xticks(los)
    ax.set_xticklabels(["%d%%" % lo for lo in los])
    # K-ORG-157：70% 待观察线（实证：0.5-0.7 段命中仅 32-43%，0.7+ 才稳定）
    ax.axvline(LOW_CONF * 100, color="#f0883e", lw=1.2, ls="--")
    ax.text(LOW_CONF * 100 + 1.5,
            max(b_bull[i] + b_bear[i] + b_neut[i] for i in range(len(los)))
            * 0.97 if any(b_bull[i] + b_bear[i] + b_neut[i]
                          for i in range(len(los))) else 1,
            "待观察<70%", fontsize=8, color="#f0883e", va="top")
    ax.set_title("置信度分布（近7天 · 竖线=待观察线）", fontsize=11, loc="left")
    ax.legend(fontsize=8, loc="upper left")

    # 4) 24h 净倾向时间轴（2h 桶，K-ORG-157：低置信不参与 + 时衰加权）
    #    w=exp(-λ·Δt小时)，半衰期 6h → 近 6h 新闻权重≈1，24h 边缘≈0.06
    ax = axes[1][1]
    bkt = [0.0] * 12
    for x in day:
        if x["conf"] < LOW_CONF:
            continue  # 低置信不参与方向信号
        h = int((now - x["t"]).total_seconds() // 7200)
        if 0 <= h < 12:
            age_h = max(0.0, (now - x["t"]).total_seconds() / 3600.0)
            w = math.exp(-LAMBDA_H * age_h)
            bkt[11 - h] += w * (1 if x["bias"] == "bull"
                                else -1 if x["bias"] == "bear" else 0)
    ax.bar(range(12), bkt, width=0.7,
           color=[C_BULL if v > 0 else C_BEAR if v < 0 else C_NEUT
                  for v in bkt])
    ax.axhline(0, color="#58606a", lw=0.8)
    ax.set_xticks(range(12))
    ax.set_xticklabels([(now - timedelta(hours=(11 - i) * 2)).strftime("%H")
                        for i in range(12)], fontsize=7)
    ax.set_title("净倾向时间轴（24h · 时衰×高置信）", fontsize=11, loc="left")

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    os.makedirs(os.path.dirname(PNG), exist_ok=True)
    fig.savefig(PNG)
    plt.close(fig)


def _find_cli():
    """lark-cli 解析（K-STOCK-396 解耦）：仓库 bin/ 固定副本优先，
    插件目录版本扫描回退。找不到返回 None。"""
    _bin = os.path.join(os.path.dirname(HERE), "bin", "lark-cli.exe")
    if os.path.isfile(_bin):
        return _bin
    if os.path.isdir(LARK_ROOT):
        for d in sorted(os.listdir(LARK_ROOT), reverse=True):
            p = os.path.join(LARK_ROOT, d, "bin", "lark-cli.exe")
            if os.path.isfile(p):
                return p
    return None


def upload_key():
    """PNG → lark-cli 上传 → image_key（失败 None）。"""
    exe = _find_cli()
    if not exe:
        return None
    try:
        for who in ("user", "bot"):  # strict-mode=user 时 bot 被拒，双保险
            r = subprocess.run(
                [exe, "im", "images", "create", "--as", who,
                 "--data", json.dumps({"image_type": "message"}),
                 "--file", PNG],
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            data = json.loads(r.stdout or "{}")
            if data.get("ok") is False:
                continue
            key = (data.get("data") or {}).get("image_key") \
                or data.get("image_key")
            if key:
                return key
        return None
    except Exception:
        return None  # 豁免（C2批2）：图片上传失败自动省略（卡片无图推送），长尾留待批3


def _lark(args, timeout=90):
    r = subprocess.run([(_find_cli() or "lark-cli")] + args,
                       capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    t = r.stdout or ""
    i = t.find("{")
    return json.loads(t[i:]) if i >= 0 else {}


def sync_base(items):
    """判断记录增量写入 Base 表（幂等：跳过已写入的判断时间）。
    2026-10-25 SEC 收口（EXEC-R14）：Base token 不再存 tracked config，
    取值优先级 env JEV_BASE_TOKEN > 本地私有文件 > news_config.json（应已空）。"""
    try:
        cfg = load_news_config()
    except Exception:
        cfg = {}
    base = cfg.get("jev_base_token")
    if not base:
        # 明确报错而不是静默退化：缺 token 时 Base 增量停更，主卡片仍推送
        print("JEV_CHART base SKIP: JEV_BASE_TOKEN 未配置"
              "（env 或 _vault/secrets/news_config_private.json）")
        return 0
    tbl = "判断记录"
    r = _lark(["base", "+record-list", "--as", "user", "--base-token", base,
               "--table-id", tbl, "--page-size", "20",
               "--sort-json", '[{"field":"判断时间","desc":true}]',
               "--format", "json"])
    dd = r.get("data") or {}
    heads, rows2 = dd.get("fields") or [], dd.get("data") or []
    last = ""
    try:  # 已按判断时间倒序：首行即增量水位
        ci = heads.index("判断时间")
        if rows2 and len(rows2[0]) > ci:
            last = str(rows2[0][ci] or "").replace("T", " ")[:16]
    except ValueError:
        last = ""
    BJ = {"bull": "利多", "bear": "利空", "neutral": "中性"}
    SC = {"cn": "A股", "hk": "港股", "us": "美股", "commodity": "商品",
          "crypto": "加密", "macro": "宏观"}
    rows = []
    for x in items:  # items 新→旧；只写比 last 新的
        ts = x["t"].strftime("%Y-%m-%d %H:%M")
        if last and ts <= last[:16]:
            break
        rows.append({"标题": x["title"][:200],
                     "方向": [BJ.get(x["bias"], "中性")],
                     "置信度": round(x["conf"] * 100),
                     "市场": [SC.get(x["scope"], x["scope"])],
                     "判断时间": ts,
                     "依据": x["reason"],
                     "来源": x["source"][:30]})
    n = 0
    for i in range(0, len(rows), 200):  # 单批上限 200
        batch = rows[i:i + 200][::-1]  # 旧→新写入
        r = _lark(["base", "+record-batch-create", "--as", "user",
                   "--base-token", base, "--table-id", tbl,
                   "--json", json.dumps({"create_records": batch},
                                        ensure_ascii=False)])
        if r.get("ok"):
            n += len(batch)
    return n


def _md_h5(now, items):
    """文档版 H5（飞书文档本身即响应式网页，链接飞书内外均可打开）。"""
    day = [x for x in items if now - x["t"] <= timedelta(hours=24)]
    bj = {"bull": "利多", "bear": "利空", "neutral": "中性"}
    sc = {"cn": "A股", "hk": "港股", "us": "美股", "commodity": "商品",
          "crypto": "加密", "macro": "宏观"}
    cnt = {b: sum(1 for x in day if x["bias"] == b)
           for b in ("bull", "bear", "neutral")}
    L = ["# ⚖️ JEV 新闻判断",
         "",
         "> 更新：%s（北京）· 随晨报/复盘自动刷新 · 仅信息参考，"
         "不构成投资建议" % now.strftime("%Y-%m-%d %H:%M"),
         ""]
    try:
        base_url = load_news_config().get("jev_base_url")
    except Exception:
        base_url = ""
    if base_url:
        L += ["**[🖱 点击进入交互版（图表可点下钻 · 记录点开看依据）]"
              "(%s)**" % base_url, ""]
    L += ["## 近 24h 方向分布", "",
          "| 利多 | 利空 | 中性 | 合计 |", "|---|---|---|---|",
         "| %d | %d | %d | %d |" % (
             cnt["bull"], cnt["bear"], cnt["neutral"], len(day)),
         "*低置信（<70%）%d 条不参与净倾向 · 净倾向按 6h 半衰期时衰加权*"
         % sum(1 for x in day if x["conf"] < LOW_CONF),
         "", "## 按市场（近 7 天）", "",
         "| 市场 | 利多 | 利空 | 中性 | 合计 |", "|---|---|---|---|---|"]
    scopes = {}
    for x in items:
        scopes.setdefault(x["scope"], []).append(x)
    for s in [k for k in ("cn", "us", "hk", "commodity", "crypto", "macro")
              if k in scopes] + [k for k in scopes
                                 if k not in sc and k != "?"]:
        g = scopes[s]
        L.append("| %s | %d | %d | %d | %d |" % (
            sc.get(s, s), sum(1 for x in g if x["bias"] == "bull"),
            sum(1 for x in g if x["bias"] == "bear"),
            sum(1 for x in g if x["bias"] == "neutral"), len(g)))
    L += ["", "## 置信度分布（近 7 天）", "", "```"]
    for lo in range(40, 100, 10):
        g = [x for x in items if lo <= x["conf"] * 100 < lo + 10]
        nb = sum(1 for x in g if x["bias"] == "bull")
        nr = sum(1 for x in g if x["bias"] == "bear")
        nn = len(g) - nb - nr
        L.append("%d-%d%% | 多%-2d 空%-2d 中%-2d |%s %d" % (
            lo, lo + 9, nb, nr, nn, "▇" * len(g), len(g)))
    L += ["```", "", "## 高置信要闻（≥70%，近 7 天）", ""]
    hi = sorted((x for x in items if x["conf"] >= 0.7),
                key=lambda x: (-x["conf"], x["t"]))
    for x in hi[:15]:
        L.append("- %s %d%% · %s · %s · [%s](%s)" % (
            bj.get(x["bias"], "?"), round(x["conf"] * 100),
            sc.get(x["scope"], x["scope"]),
            x["t"].strftime("%m-%d %H:%M"), x["title"][:60],
            _search_url(x["title"])))
    L += ["", "## 判断明细（近 24h）", "",
          "| 时间 | 方向 | 置信 | 市场 | 标题 |", "|---|---|---|---|---|"]
    for x in day[:80]:
        L.append("| %s | %s | %d%% | %s | %s |" % (
            x["t"].strftime("%H:%M"), bj.get(x["bias"], "?"),
            round(x["conf"] * 100), sc.get(x["scope"], x["scope"]),
            x["title"][:60].replace("|", "／")))
    return "\n".join(L)


def _search_url(title):
    import urllib.parse
    return ("https://www.baidu.com/s?wd="
            + urllib.parse.quote(title[:40]))


def refresh_doc(now, items):
    """确保文档存在 → overwrite markdown → 文档尾部插图。
    2026-10-25 SEC 收口（EXEC-R14）：文档 URL 属敏感字段，读 env/本地私有覆盖，
    首次建成后只写私有文件，绝不回写 tracked 的 news_config.json。成功返回 URL。"""
    try:
        cfg = load_news_config()
    except Exception:
        cfg = {}
    doc = cfg.get("jev_doc_url") or ""
    exe = _find_cli() or "lark-cli"  # K-STOCK-396：解耦副本优先
    tmp = os.path.join(HERE, "_jev_doc_tmp.md")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(_md_h5(now, items))
    try:
        if not doc:  # 首次建文档
            r = subprocess.run(
                [exe, "docs", "+create", "--as", "user",
                 "--doc-format", "markdown", "--content", "@./_jev_doc_tmp.md",
                 "--format", "json"],
                capture_output=True, text=True, timeout=120, cwd=HERE)
            data = json.loads(r.stdout or "{}")
            doc = (((data.get("data") or {}).get("document") or {})
                   .get("url") or "")
            if not doc:
                return None
            save_jev_doc_url(doc)  # 2026-10-25 SEC 收口：写私有覆盖，不回写 tracked config
        else:
            r = subprocess.run(
                [exe, "docs", "+update", "--as", "user", "--doc", doc,
                 "--command", "overwrite", "--doc-format", "markdown",
                 "--content", "@./_jev_doc_tmp.md", "--format", "json"],
                capture_output=True, text=True, timeout=120, cwd=HERE)
            if '"ok": true' not in (r.stdout or "") \
                    and '"ok":true' not in (r.stdout or ""):
                return None
        subprocess.run(  # 文档尾部插图表 PNG（4 步编排，失败不影响表格）
            [exe, "docs", "+media-insert", "--as", "user",
             "--doc", doc, "--file", PNG, "--align", "center",
             "--caption", "JEV 判断图表", "--format", "json"],
            capture_output=True, text=True, timeout=120, cwd=HERE)
        return doc
    except Exception:
        return None  # 豁免（C2批2）：文档刷新失败仅缺下钻链接，主卡片仍推送，长尾留待批3
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass  # 豁免（C2批2）：临时文件清理兜底


def _card_btns(doc_url, base_url):
    """按钮组：交互下钻（Base）优先，图表快照（文档）次之。"""
    acts = []
    if base_url:
        acts.append({"tag": "button",
                     "text": {"tag": "plain_text", "content": "🖱 JEV 下钻"},
                     "type": "primary", "url": base_url})
    if doc_url:
        acts.append({"tag": "button",
                     "text": {"tag": "plain_text", "content": "📄 图表快照"},
                     "type": "default", "url": doc_url})
    return ([{"tag": "action", "actions": acts}] if acts else [])


def push(key, n_total, n_day, now, doc_url=None, base_url=None):
    card = {
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "⚖️ JEV 新闻判断图表"},
            "subtitle": {"tag": "plain_text",
                         "content": "累计 %d 条 · 24h %d 条 · %s" % (
                             n_total, n_day, now.strftime("%m-%d %H:%M"))},
            "template": "blue",
        },
        "elements": [
            {"tag": "column_set", "flex_mode": "none", "columns": [
                {"tag": "column", "width": "weighted", "weight": 1,
                 "elements": [{"tag": "img", "img_key": key,
                               "alt": {"tag": "plain_text",
                                       "content": "JEV判断图表"}}]}]},
            {"tag": "hr"},
        ] + _card_btns(doc_url, base_url) + [
            {"tag": "hr"},
            {"tag": "markdown", "text_size": "notation",
             "content": "<font color='grey'>随晨报/复盘自动推送 · "
                        "不构成投资建议</font>"},
        ],
    }
    from card_build import push_card
    return push_card(card)


def main():
    dry = "--dry" in sys.argv
    now, items = load()
    if not items:
        print("JEV_CHART skip: no judged news")
        return
    render(now, items)
    if dry:
        print("JEV_CHART dry ok %d items -> %s" % (len(items), PNG))
        return
    key = upload_key()
    if not key:
        print("JEV_CHART UPLOAD_FAIL")
        return
    day_n = sum(1 for x in items if now - x["t"] <= timedelta(hours=24))
    try:
        n_new = sync_base(items)
        if n_new:
            print("JEV_CHART base +%d" % n_new)
    except Exception as e:
        print("JEV_CHART base sync ERR %s" % str(e)[:100])
    try:
        base_url = load_news_config().get("jev_base_url")
    except Exception:
        base_url = ""
    doc_url = refresh_doc(now, items)  # 先刷文档，卡片按钮才能带上链接
    ok = push(key, len(items), day_n, now, doc_url, base_url)
    try:  # 微信渠道（企微机器人，未配置自动跳过）
        import send_wecom
        if send_wecom.send_image(PNG):
            send_wecom.send_text(
                "⚖️ JEV 新闻判断图表 · 累计 %d 条 · 24h %d 条 · %s\n"
                "（随晨报/复盘自动推送 · 不构成投资建议）"
                % (len(items), day_n, now.strftime("%m-%d %H:%M")))
    except Exception as e:
        # C2批3: 接 print 观测通道（news_ai Popen 已重定向本模块 stdout 落盘）；
        # send_wecom 内部已自捕获并 print WECOM_FAIL/WECOM_SKIP，此处只兜 import 失败等编程错误
        print("JEV_CHART wecom ERR %s" % str(e)[:100])
    print("JEV_CHART pushed" if ok else "JEV_CHART PUSH_FAIL")


if __name__ == "__main__":
    main()
