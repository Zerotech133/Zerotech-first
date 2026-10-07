# -*- coding: utf8 -*-
"""A 股日线深历史回填（K58）：daily + adj_factor 双表按日回拉。

背景：ts_daily（xtcloud）仅 2023-07-31 起 3 年，深历史「未建仓」。
本脚本按交易日逐日拉 tushare daily / adj_factor（单日全市场一页），
落 data/tushare/daily/d_YYYYMMDD.parquet（by_date 布局），止于
2023-07-30（ts_daily 起点前一日，无重叠）；跑完 build_catalog 后
别名 daily → [ts_daily, daily] 即得 1990→今 全史。

幂等：已有 d_*.parquet 跳过 → 中断重跑自然续。限频错误由
ts_call 自动退避 61s 重试。
"""
import os, sys, glob, time
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tushare_sync as T  # 复用 ts_call 限频退避 / OUT / tdir

END = "20230730"          # ts_daily 起点 2023-07-31 前一日，零重叠
START = "19901219"        # 沪市开市日


def days_between():
    parts = sorted(glob.glob(os.path.join(T.OUT, "trade_cal",
                                          "part_*.parquet")))
    cal = pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"])
                     for p in parts])
    days = sorted(set(cal[cal["is_open"] == 1]["cal_date"]
                      .astype(str)))          # part 为重叠快照，须去重
    return [d for d in days if START <= d <= END]


def main():
    days = days_between()
    print("daily+adj_factor 深历史 %s→%s 共 %d 交易日" %
          (START, END, len(days)), flush=True)
    t0 = time.time()
    for api in ("daily", "adj_factor"):
        root = T.tdir(api)
        os.makedirs(root, exist_ok=True)
        todo = [d for d in days
                if not os.path.exists(os.path.join(root,
                                                   "d_%s.parquet" % d))]
        print("== %s：待拉 %d / %d ==" % (api, len(todo), len(days)),
              flush=True)
        done = 0
        for d in todo:
            f = os.path.join(root, "d_%s.parquet" % d)
            try:
                df = T.pull_paged(api, {"trade_date": d}, 6000,
                                  max_off=6000 * 20)
                df.to_parquet(f, index=False)
                done += 1
            except Exception as e:
                print("  %s %s FAIL %s" % (api, d, str(e)[:60]), flush=True)
            time.sleep(T.SLEEP)
            if done % 100 == 0 and done:
                el = time.time() - t0
                eta = el / max(done, 1) * (len(todo) - done)
                print("  %s %d/%d (%.0f%%) ETA %.0fmin"
                      % (api, done, len(todo), done * 100.0 / len(todo),
                         eta / 60), flush=True)
        print("== %s 完成 +%d ==" % (api, done), flush=True)
    print("全部完成 %.0fs。下一步：python build_catalog.py" %
          (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
