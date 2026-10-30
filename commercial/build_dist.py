# -*- coding: utf-8 -*-
"""P2.2 私有化交付包构建器：把代码+脚本+文档+示例报告打成可交付 zip。

用法：
  python -m commercial.build_dist --out "C:\\项目\\deliverables\\2026-10-30_私有化交付包"
产物：stock-analysis-private-<VERSION>.zip（含 MANIFEST.sha256 与 VERSION）
约束：不打包 data/work/license/密钥/__pycache__；不含任何第三方受限数据；
      交付包内 config.py 会重写为"安装目录相对路径"版本（客户无需改代码）。
"""
import argparse
import hashlib
import os
import time
import zipfile

VERSION = "0.2.0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INCLUDE_DIRS = [("analysis", ".py"), ("commercial", ".py"), ("deploy/private", "")]
INCLUDE_FILES = [("analysis/watchlist.txt", "")]
SAMPLE_DIR = os.environ.get("DIST_SAMPLE_DIR", "")

EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", "tests", "data", "work", "runs", "cache"}
# 研究/回测类工具不随私有化交付包发布（内部研发用）
EXCLUDE_FILES = {"analysis/backtest.py", "analysis/event_backtest.py",
                 "analysis/event_v2.py", "analysis/prereview.py",
                 "commercial/build_dist.py"}

# 交付包内 config.py 重写：去掉平台内网路径，改为"安装目录相对路径"
CONFIG_REWRITES = [
    ('os.environ.get("ANALYSIS_MAIN_ROOT", r"C:\\项目\\老田的AI助手")',
     'os.environ.get("ANALYSIS_MAIN_ROOT") or os.path.dirname(PKG)'),
    ('os.environ.get("ANALYSIS_WORK", r"C:\\项目\\analysis_work")',
     'os.environ.get("ANALYSIS_WORK") or os.path.join(MAIN_ROOT, "work")'),
    ('os.environ.get("ANALYSIS_DELIVER", r"C:\\项目\\deliverables")',
     'os.environ.get("ANALYSIS_DELIVER") or os.path.join(MAIN_ROOT, "deliverables")'),
]


def _iter_files():
    for d, ext in INCLUDE_DIRS:
        base = os.path.join(ROOT, *d.split("/"))
        if not os.path.isdir(base):
            continue
        for cur, _dirs, files in os.walk(base):
            rel = os.path.relpath(cur, ROOT).replace("\\", "/")
            if any(p in rel.split("/") for p in EXCLUDE_PARTS):
                continue
            for f in sorted(files):
                if f.startswith("."):
                    continue
                if ext and not f.endswith(ext):
                    continue
                p = os.path.join(cur, f)
                relp = os.path.join(rel, f).replace("\\", "/")
                if relp in EXCLUDE_FILES:
                    continue
                yield p, relp
    for rel, _ in INCLUDE_FILES:
        p = os.path.join(ROOT, *rel.split("/"))
        if os.path.exists(p):
            yield p, rel


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


TENANTS_REWRITES = [
    (r'os.environ.get("COMMERCIAL_HOME") or r"C:\项目\commercial"',
     'os.environ.get("COMMERCIAL_HOME") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work", "commercial")'),
]


def _content_of(p: str, rel: str) -> bytes:
    if rel == "analysis/config.py":
        s = open(p, encoding="utf-8").read()
        for old, new in CONFIG_REWRITES:
            s = s.replace(old, new)
        return s.encode("utf-8")
    if rel == "commercial/tenants.py":
        s = open(p, encoding="utf-8").read()
        for old, new in TENANTS_REWRITES:
            s = s.replace(old, new)
        return s.encode("utf-8")
    return open(p, "rb").read()


def build(out_dir: str, version: str = VERSION) -> str:
    os.makedirs(out_dir, exist_ok=True)
    name = "stock-analysis-private-%s.zip" % version
    zp = os.path.join(out_dir, name)
    manifest = ["# sha256  MANIFEST（%s）" % time.strftime("%F %T"), ""]
    entries = []
    for p, rel in _iter_files():
        data = _content_of(p, rel)
        entries.append((rel, data))
        manifest.append("%s  %s" % (_sha256_bytes(data), rel))
    if os.path.isdir(SAMPLE_DIR):
        for f in ("report.md", "h5.html"):
            p = os.path.join(SAMPLE_DIR, f)
            if os.path.exists(p):
                data = open(p, "rb").read()
                rel = "sample_reports/603667.SH_" + f
                entries.append((rel, data))
                manifest.append("%s  %s" % (_sha256_bytes(data), rel))
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in entries:
            z.writestr(rel, data)
        z.writestr("VERSION", version + "\n")
        z.writestr("MANIFEST.sha256", "\n".join(manifest) + "\n")
        z.writestr("INSTALL.txt",
                   "零界个股分析 · 私有化交付包 v%s\n\n"
                   "1) 解压到不含中文/空格的目录\n"
                   "2) .\\install.ps1 -LicenseKeyFile .\\license.key\n"
                   "3) 编辑 config.ps1 后 .\\activate.ps1 -ConfigFile ...\\config.ps1\n"
                   "4) python -m analysis.run --code 600337.SH --no-llm\n\n"
                   "详见 deploy/private/README.md 与 OPS.md\n" % version)
    return zp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.environ.get("DIST_OUT") or os.path.join(os.getcwd(), "dist"))
    ap.add_argument("--version", default=VERSION)
    a = ap.parse_args()
    zp = build(a.out, a.version)
    size = os.path.getsize(zp)
    print(zp)
    print("size: %.1f MB" % (size / 1024 / 1024))
    with zipfile.ZipFile(zp) as z:
        print("files: %d" % len(z.namelist()))


if __name__ == "__main__":
    main()
