# -*- coding: utf-8 -*-
"""A-3 预校准盲测：jev 本地回落（9b）vs typesafe 历史判定一致率。

K-STOCK-082。只读评测，不改在线链路。输出落 logs/a3_blindtest.log。
抽样：bias 三类分层各 10 条（近 90 天，bull/bear/neutral）。
门（预研文档 §三）：一致率 >=80% 启用；60~80% 启用但 conf 七折；<60% 不启用。
"""

import json
import random
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "a3_blindtest.log"
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"
N_PER_CLASS = 10

import requests  # noqa: E402


def sample_rows():
    con = sqlite3.connect(str(HERE / "news.db"))
    out = []
    for bias in ("bull", "bear", "neutral"):
        rows = con.execute(
            "SELECT id, title, stocks, jev FROM news "
            "WHERE jev LIKE ? ORDER BY id DESC LIMIT 400",
            ('%"bias": "' + bias + '"%',),
        ).fetchall()
        pick = random.Random(20261010).sample(rows, min(N_PER_CLASS, len(rows)))
        out.extend(pick)
    con.close()
    return out


def local_judge(title, stocks):
    tag = ("（关联标的：%s）" % "/".join(stocks.split("/"))) if stocks else ""
    prompt = (
        "财经快讯：%s%s\n判断该快讯对相关市场【资产价格】的短期影响方向，只输出 JSON："
        '{"bias":"bull|bear|neutral","conf":0.0到1.0}，不要输出其他内容。'
        "口径：只看对资产价格的直接影响——外交会晤、一般性产业与技术进展、常规信息披露、"
        "国债/政策性金融债发行供给等属 neutral；明确的需求扩张、超预期业绩、降息等利好才判 bull；"
        "明确的供给冲击、业绩暴雷、加息、制裁等才判 bear。" % (title[:100], tag)
    )
    r = requests.post(
        OLLAMA,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 60},
        },
        timeout=60,
    )
    j = json.loads(r.json()["message"]["content"])
    b = (j.get("bias") or "").strip().lower()
    if b not in ("bull", "bear", "neutral"):
        return None, None
    return b, j.get("conf")


def main():
    LOG.parent.mkdir(exist_ok=True)
    rows = sample_rows()
    lines = ["== A-3 blindtest %s n=%d model=%s ==" % ("2026-10-10", len(rows), MODEL)]
    agree = 0
    invalid = 0
    confusions = {}
    for nid, title, stocks, jev in rows:
        truth = json.loads(jev)["bias"]
        try:
            pred, conf = local_judge(title, stocks)
        except Exception as e:  # noqa: BLE001
            pred, conf = None, "ERR:%s" % str(e)[:60]
        if pred is None:
            invalid += 1
            lines.append("INVALID id=%s truth=%s pred=%r" % (nid, truth, conf))
            continue
        ok = pred == truth
        agree += ok
        if not ok:
            confusions[(truth, pred)] = confusions.get((truth, pred), 0) + 1
        lines.append(
            "%s id=%s truth=%-7s pred=%-7s conf=%s | %s"
            % ("OK " if ok else "MISS", nid, truth, pred, conf, title[:50])
        )
    rate = 100.0 * agree / len(rows)
    lines.append(
        "== 一致率 %.1f%% (%d/%d), 非法输出 %d ==" % (rate, agree, len(rows), invalid)
    )
    lines.append("混淆矩阵: %s" % confusions)
    verdict = (
        "ENABLE"
        if rate >= 80
        else "ENABLE-WITH-DISCOUNT(conf 七折)"
        if rate >= 60
        else "REJECT"
    )
    lines.append("== 门结论: %s ==" % verdict)
    text = "\n".join(lines)
    LOG.write_text(text, encoding="utf-8")
    print(text[-600:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
