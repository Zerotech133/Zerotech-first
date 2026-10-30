# -*- coding: utf-8 -*-
r"""依赖漏洞审计兜底：PYPI 冻结清单 → OSV batch API。

用法：
    python tools/pip_osv_audit.py -r C:\Temp\py312_freeze.txt --out C:\Temp\pip_osv_report.json

退出码：0 无漏洞；1 有漏洞；2 输入/网络错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)==([^\s;]+)")


def parse_freeze(path: Path) -> list[tuple[str, str]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-e "):
            continue
        m = LINE_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def query_osv(packages: list[tuple[str, str]], timeout: float = 60.0) -> list[dict]:
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v}
               for n, v in packages]
    body = json.dumps({"queries": queries}).encode("utf-8")
    req = urllib.request.Request(OSV_BATCH, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    # 直连 https 公网；容器/代理场景下同样走默认证书校验
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - 固定 OSV 官方 https 端点
        data = json.loads(r.read().decode("utf-8"))
    results = data.get("results") or []
    findings = []
    for (name, version), res in zip(packages, results):
        for v in (res or {}).get("vulns") or []:
            findings.append({
                "package": name,
                "version": version,
                "id": v.get("id"),
                "aliases": v.get("aliases") or [],
                "summary": (v.get("summary") or "")[:300],
            })
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OSV dependency audit (PyPI freeze)")
    ap.add_argument("-r", "--requirements", required=True)
    ap.add_argument("--out", default=r"C:\Temp\pip_osv_report.json")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)
    req = Path(args.requirements)
    if not req.exists():
        print("freeze not found: %s" % req, file=sys.stderr)
        return 2
    packages = parse_freeze(req)
    if not packages:
        print("no packages parsed", file=sys.stderr)
        return 2
    try:
        findings = query_osv(packages, timeout=args.timeout)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("OSV query failed: %s" % e, file=sys.stderr)
        return 2
    report = {
        "scanned": len(packages),
        "vulnerable_packages": len({f["package"] for f in findings}),
        "vulnerabilities": len(findings),
        "findings": findings,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("scanned", "vulnerable_packages", "vulnerabilities")},
                     ensure_ascii=False))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
