# -*- coding: utf-8 -*-
"""九维快照查询（K35-b §2① 命令块）：单票在当日快照 / 池史中的记录。

输出三段：
  ① 当日快照 meta（交易日/漏斗/环境/入池数）
  ② 该票评分行（score/cov/band/各维/J 扣分/r20/rps50…）；
     缺席时给出判读 + 同行业在榜票对照（弱者为何不在榜的间接证据）
  ③ 警报池历史（nine_dim_history.jsonl 中该票全部记录）

用法:
  python nd_lookup.py 301216            # 6 位码自动补后缀
  python nd_lookup.py 301216.SZ
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.join(HERE, "data")
SNAP = os.path.join(DATA, "nine_dim_scores.json")
HIST = os.path.join(DATA, "nine_dim_history.jsonl")


from codes import normalize_code as _norm  # noqa: E402  P1-2 统一代码归一（北交所 8/4/92 开头 → .BJ）


def _ind_of(code):
    try:
        import tdx_read as tdxr

        sb = tdxr.read_all("ts_stock_basic", codes=[code])
        if len(sb):
            return str(sb.iloc[0].get("industry") or ""), str(
                sb.iloc[0].get("name") or ""
            )
    except Exception:
        pass
    return "", ""


def main():
    if len(sys.argv) < 2:
        print("usage: python nd_lookup.py <code>")
        return 1
    code = _norm(sys.argv[1])
    snap = {}
    try:
        with open(SNAP, encoding="utf-8") as f:
            snap = json.load(f)
    except OSError:
        print("快照不存在：%s" % SNAP)
        return 1

    print("=== 九维快照 meta ===")
    print(
        json.dumps(
            {
                k: snap.get(k)
                for k in (
                    "generated_at",
                    "trade_date",
                    "env",
                    "funnel",
                    "n_cand",
                    "n_full",
                    "n_alert",
                    "alert_th",
                    "buy_th",
                )
            },
            ensure_ascii=False,
            indent=1,
        )
    )

    rows = snap.get("rows") or []
    hit = [r for r in rows if str(r.get("code", "")).startswith(code.split(".")[0])]
    print("\n=== %s 评分行 ===" % code)
    if hit:
        print(json.dumps(hit[0], ensure_ascii=False, indent=1, default=str))
    else:
        ind, name = _ind_of(code)
        print(
            "缺席：当日 %d 个入围候选中无此票（未过粗筛，粗筛层不落单票"
            "明细——缺席本身=非强势主流票）" % len(rows)
        )
        if ind:
            same = [r for r in rows if r.get("industry") == ind]
            same.sort(key=lambda r: -(r.get("score") or -1))
            print("同行业「%s」在榜 %d/%d 票（前 5）：" % (ind, len(same), len(rows)))
            for r in same[:5]:
                print(
                    "  %-10s %-8s score=%s band=%s rps50=%s r20=%s"
                    % (
                        r.get("code"),
                        r.get("name"),
                        r.get("score"),
                        r.get("band"),
                        r.get("rps50"),
                        r.get("r20"),
                    )
                )

    print("\n=== 警报池历史（nine_dim_history.jsonl）===")
    n = 0
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if str(r.get("code", "")).startswith(code.split(".")[0]):
                    n += 1
                    print(
                        "  %s %s score=%s band=%s pool=%s entry=%s"
                        % (
                            r.get("dt"),
                            r.get("name"),
                            r.get("score"),
                            r.get("band"),
                            r.get("pool_type"),
                            r.get("entry_px"),
                        )
                    )
    if not n:
        print("  无记录（从未入警报池）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
