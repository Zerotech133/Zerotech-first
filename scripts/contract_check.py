#!/usr/bin/env python
"""governance_upgrade 升级包契约校验（CI 与本机共用，仅标准库）。

校验项：
  - version 文件存在且非空
  - rules.json 合法 JSON 且含必要字段（version/normalize/fields/pii_gate）
  - changelog.md 存在

用法：
  python contract_check.py [--repo <governance_upgrade 目录>]
退出码：0=通过，1=失败。
"""
import json
import sys
import argparse
from pathlib import Path

REQUIRED_RULES_KEYS = ["version", "normalize", "fields", "pii_gate"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()
    repo = Path(args.repo)
    errors = []

    ver_file = repo / "version"
    if not ver_file.exists():
        errors.append("缺少 version 文件")
    else:
        v = ver_file.read_text(encoding="utf-8").strip()
        if not v:
            errors.append("version 文件为空")
        else:
            print(f"[OK] version = {v}")

    rules = repo / "rules.json"
    if not rules.exists():
        errors.append("缺少 rules.json")
    else:
        try:
            data = json.loads(rules.read_text(encoding="utf-8"))
            missing = [k for k in REQUIRED_RULES_KEYS if k not in data]
            if missing:
                errors.append(f"rules.json 缺字段: {missing}")
            else:
                gate = data.get("pii_gate", {})
                print(f"[OK] rules.json 合法 | version={data.get('version')} "
                      f"| pii_gate.types={gate.get('types')}")
        except json.JSONDecodeError as e:
            errors.append(f"rules.json 非法 JSON: {e}")

    cl = repo / "changelog.md"
    if not cl.exists():
        errors.append("缺少 changelog.md")
    else:
        print(f"[OK] changelog.md 存在 ({len(cl.read_text(encoding='utf-8'))} 字节)")

    if errors:
        print("\n契约校验失败:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\n契约校验通过 ✅")
    sys.exit(0)


if __name__ == "__main__":
    main()
