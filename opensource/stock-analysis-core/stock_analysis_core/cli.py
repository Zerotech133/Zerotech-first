# -*- coding: utf-8 -*-
"""命令行：stock-analysis run/sample/version。"""
import argparse
import json
import sys

from . import __version__, sample
from .pipeline import Pipeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stock-analysis", description="个股研究流水线（开源内核）")
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="对一只股票跑完整流水线")
    p_run.add_argument("--code", required=True)
    p_run.add_argument("--data-dir", required=True)
    p_run.add_argument("--out", default="./out")

    p_sample = sub.add_parser("sample", help="生成合成示例数据")
    p_sample.add_argument("--out", required=True)

    sub.add_parser("version", help="打印版本")

    a = ap.parse_args(argv)
    if a.cmd == "run":
        r = Pipeline().run(a.code, a.data_dir, a.out)
        print(json.dumps({"code": r["code"], "rating": r["verdict"]["rating"],
                          "score": r["verdict"]["score"], "report": r["artifacts"]["report"]},
                         ensure_ascii=False))
        return 0
    if a.cmd == "sample":
        sample.write_sample(a.out)
        print("sample data -> " + a.out)
        return 0
    if a.cmd == "version":
        print(__version__)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
