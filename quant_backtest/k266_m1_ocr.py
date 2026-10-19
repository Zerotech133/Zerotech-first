# -*- coding: utf-8 -*-
"""K266 M1 OCR 验收：PaddleOCR PP-OCRv5 文本抽取 vs pypdf 真值。

指标：字符 2-gram Dice（滤空白）。门槛：均值 >=0.90。
表格页另跑 PP-StructureV3 存 markdown 供人眼核（结构不乱即可）。
产出 data/k266_m1/ocr_out/{sample}.txt + eval_result.json + structure/*.md
"""
import json
import os
import re
import time

ROOT = r"C:\项目\老田的AI助手\quant_backtest\data\k266_m1"
OUT = os.path.join(ROOT, "ocr_out")
TABLE_PAGE = "ann_summary_p2.png"  # 考核表格页


def ngrams(s, n=2):
    s = re.sub(r"\s+", "", s)
    return set(s[i:i + n] for i in range(len(s) - n + 1))


def dice(a, b, n=2):
    ga, gb = ngrams(a, n), ngrams(b, n)
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


def main():
    os.makedirs(os.path.join(OUT, "structure"), exist_ok=True)
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        lang="ch",
        enable_mkldnn=False,  # paddle3.3 CPU oneDNN bug: ArrayAttribute not supported
    )
    manifest = json.load(open(os.path.join(ROOT, "manifest.json"),
                              encoding="utf-8"))
    results = []
    t0 = time.time()
    for m in manifest:
        img = os.path.join(ROOT, "samples", m["img"])
        t1 = time.time()
        r = ocr.predict(img)
        res = r[0]
        texts = (res.get("rec_texts") if hasattr(res, "get")
                 else getattr(res, "rec_texts", None)) or []
        text = "\n".join(str(t) for t in texts)
        base = m["img"].rsplit(".", 1)[0]
        with open(os.path.join(OUT, base + ".txt"), "w",
                  encoding="utf-8") as f:
            f.write(text)
        truth = open(os.path.join(ROOT, "truth", base + ".txt"),
                     encoding="utf-8").read()
        d = dice(text, truth)
        results.append({"img": m["img"], "truth_chars": m["truth_chars"],
                        "ocr_chars": len(text), "dice2": round(d, 4),
                        "sec": round(time.time() - t1, 1)})
        print(f"  {m['img']}: dice2={d:.3f} ocr={len(text)}c "
              f"truth={m['truth_chars']}c {results[-1]['sec']}s")
    mean = sum(x["dice2"] for x in results) / len(results)
    verdict = "PASS" if mean >= 0.90 else "FAIL"
    print(f"[text] mean dice2 = {mean:.3f} -> {verdict} "
          f"(total {time.time()-t0:.0f}s)")

    # ---- 表格页结构化（人眼核，不计分） ----
    # 正确用法：结果为 LayoutParsingResultV2，表格在 table_res_list[].pred_html
    # （无 markdown 键；save_to_markdown 可整页导出）
    try:
        from paddleocr import PPStructureV3
        pp = PPStructureV3(enable_mkldnn=False,
                           use_chart_recognition=False,
                           use_formula_recognition=False,
                           use_seal_recognition=False)
        r = pp.predict(os.path.join(ROOT, "samples", TABLE_PAGE))
        res = r[0]
        tables = res.get("table_res_list") or []
        for i, t in enumerate(tables):
            html = (t.get("pred_html") or "")
            fn = os.path.join(OUT, "structure",
                              TABLE_PAGE + f"_table{i}.html")
            with open(fn, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"[table] {TABLE_PAGE}#{i}: {len(html)}c html -> {fn}")
        res.save_to_markdown(os.path.join(OUT, "structure"))
        print(f"[table] {TABLE_PAGE}: {len(tables)} tables + markdown saved")
    except Exception as e:
        print(f"[table] PPStructureV3 failed: {type(e).__name__}: {e}")

    json.dump({"mean_dice2": round(mean, 4), "verdict": verdict,
               "samples": results},
              open(os.path.join(OUT, "eval_result.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[done] -> {OUT}")


if __name__ == "__main__":
    main()
