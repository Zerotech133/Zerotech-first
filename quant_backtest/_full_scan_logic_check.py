#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""--full 逻辑最小合成验证（W1-2 任务B，一次性探针，仓库 _3xx 探针风格）。

不跑全市场、不碰真 DB 取数、不落盘：monkeypatch block_members /
eval_stock / chanlun_data._db_query，只验证 scan_block 两条路径的
选取逻辑——
  1) 默认路径：50 成员只评前 20（清单序截断，行为与旧版一致）
  2) --full：50 成员评全部 50
  3) --full 兜底：250 成员触发 200 截断 + 按 20 日成交额降序
     （假 turnover：amount 高的票必须入选，清单序头部低额票必须被挤掉）

用法: python _full_scan_logic_check.py   # 全断言通过打印 CHECK_OK
"""

import sys

HERE = __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0]
sys.path.insert(0, HERE)

import chanlun_data  # noqa: E402
import linkage  # noqa: E402
import strong_scan  # noqa: E402


def fake_members(prefix, n):
    return [
        {"code": "%s%04d.SZ" % (prefix, i), "name": "fake%d" % i, "industry": "x"}
        for i in range(n)
    ]


def install_env(n_members, fake_amt=None):
    """装载合成环境：成员清单 + 单股评估 + （可选）成交额查询。"""
    linkage.block_members = lambda bc: fake_members("3001", n_members)

    def fake_eval(code, block_code):
        i = int(code[4:8])
        # 全员 bull；ex250=i 取余——保证每个位置都有机会命中，
        # 截断路径的差异只能来自「评了谁」而非「谁命中」
        return (float(i % 50), True, True, 250)

    strong_scan.eval_stock = fake_eval

    if fake_amt is not None:
        chanlun_data._db_query = lambda sql, params: fake_amt


counter = [0]


def run(full, n_members, fake_amt=None):
    counter[0] = 0
    install_env(n_members, fake_amt)
    hits, n, n_eval, skip, cap = strong_scan.scan_block(
        "881019", "合成板块", strong_scan.PER_BLOCK_CAP, 0.0, 1, counter, full=full
    )
    return hits, n, n_eval, cap


def main():
    # 1) 默认路径：50 成员 → 只评前 20（不触发 200 兜底）
    hits, n, n_eval, cap = run(False, 50)
    assert n == 50 and n_eval == 20 and cap is False, (n, n_eval, cap)
    assert all(int(h["code"][4:8]) < 20 for h in hits), "默认路径应只含前 20"
    print("1) 默认路径 50 成员 → 评估 %d（前 20 截断）OK" % n_eval)

    # 2) --full：50 成员 → 全量 50
    hits, n, n_eval, cap = run(True, 50)
    assert n == 50 and n_eval == 50 and cap is False, (n, n_eval, cap)
    print("2) full 路径 50 成员 → 评估 %d（全量）OK" % n_eval)

    # 3) --full 兜底：250 成员 → 成交额降序截 200；
    #    假 turnover：成员 i 的 20 日成交额 = i（清单序即升序）
    #    → 入选的必须是 [50..249]（高额尾段），清单序头部 [0..49] 被挤出
    amt = [("3001%04d.SZ" % i, float(i)) for i in range(250)]
    hits, n, n_eval, cap = run(True, 250, fake_amt=amt)
    assert cap is True, "250 成员应触发 200 兜底"
    assert n_eval == 200, n_eval
    codes = sorted(int(h["code"][4:8]) for h in hits)
    assert codes == list(range(50, 250)), "入选应为成交额最高的 [50..249]"
    print("3) full 兜底 250 成员 → 截 200，高额票入选、低额头部被挤出 OK")

    # 4) 兜底降级：成交额查询失败 → 清单序截 200（可用性优先）
    hits, n, n_eval, cap = run(True, 250, fake_amt=None)  # 不 patch 时真查会失败?

    # 上一步 patch 已被 install_env(fake_amt=None) 保留旧 patch——
    # 直接验证 rank_by_amount_20d 的异常分支：
    def boom(sql, params):
        raise RuntimeError("合成故障")

    chanlun_data._db_query = boom
    sel, ok = strong_scan.rank_by_amount_20d(fake_members("3001", 250), 200)
    assert ok is False and len(sel) == 200, (ok, len(sel))
    assert int(sel[0]["code"][4:8]) == 0, "降级应保留清单序头部"
    print("4) 成交额查询故障 → 回退清单序截 200 OK")

    print("CHECK_OK（--full 与默认两条路径逻辑全部通过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
