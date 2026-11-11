# -*- coding: utf-8 -*-
"""把 lens/latest.json 注入模板，生成 multi_lens.html。"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "lens", "latest.json")
TPL = os.path.join(HERE, "lens_template.html")
OUT = os.path.join(HERE, "multi_lens.html")


def main():
    if not os.path.exists(SRC):
        raise SystemExit("缺 lens/latest.json，先跑 lens_engine.py")
    d = json.load(io.open(SRC, encoding="utf-8"))
    tpl = io.open(TPL, encoding="utf-8").read()
    if tpl.count("__DATA__") != 1:
        raise SystemExit("模板占位符 __DATA__ 必须恰好 1 个，实际 %d" % tpl.count("__DATA__"))
    blob = json.dumps(d, ensure_ascii=False)
    # 防止 JSON 里的 </script> 提前闭合
    blob = blob.replace("</", "<\\/")
    html = tpl.replace("__DATA__", blob)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(html)
    print("生成 %s （%.1f KB，假设 %d 条，维度 %d 个）"
          % (os.path.basename(OUT), len(html.encode("utf-8")) / 1024.0,
             len(d.get("hypotheses") or []), len(d.get("dimensions") or [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
