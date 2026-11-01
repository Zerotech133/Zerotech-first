# -*- coding: utf-8 -*-
"""检索评测集资产化管理（wave14 支线 AP）：把冻结评测集导出为带版本 + sha256 的标准资产。

背景
----
评测集此前散落在 `eval_search_v2_ext.py`（41 条）与 `eval_search_v2_ext2.py`（89 条）代码常量里，
没有版本/哈希/校验，无法保证「冻结标注」不被静默修改。本模块把 wave10 的 89 条冻结查询
（分级标注 2/1/0）导出为**不可变资产**：

- `research/reports/eval_sets/eval_set_20261101_v2.json`：版本 2 资产
  （version/date/hash/annotation_policy/queries[含 expected_units 与 grade]）；
- `research/reports/eval_sets/manifest.json`：版本清单（版本/hash/创建时间/来源/查询数/语法分布）；
- CLI：`freeze`（从 ext2 只读导出 + 算 hash + 写 manifest）、`verify`（hash/结构/规范字段校验）、
  `list`（列版本）、`load --version`（加载资产）。

规范校验（对应《检索评测标注规范 v1》）
------------------------------------
- 总查询数 n ≥ 60；
- 每条查询至少 1 个 grade-2 标注；
- 题型覆盖与配额：主题 ≥40%n、同义词 ≥8、AND ≥8、OR ≥8、短语 ≥4、排除 ≥4、短查询 ≥3、长查询 ≥3；
- id/查询文本唯一，grade 只允许 {2,1}，不得为空关键词。

不改动 `eval_search_v2_ext2.py`（只读导出）；数据源 `portal/static/kb_units_all.json` 只读取单元文本
用于展开 expected_units（unit 索引 + grade）。

运行
----
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_registry.py freeze
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_registry.py verify
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_registry.py list
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_registry.py load --version 2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))
OUT_DIR = ROOT / "research" / "reports" / "eval_sets"
MANIFEST = OUT_DIR / "manifest.json"
SCHEMA_VERSION = "eval_set.v1"
DEFAULT_VERSION = 2
DEFAULT_SET_ID = "eval_set_20261101_v2"
DEFAULT_FILENAME = "eval_set_20261101_v2.json"

REQUIRED_TYPES = ("主题", "同义词", "AND", "OR", "短语", "排除")
TYPE_QUOTAS = {"主题": ("ratio", 0.40), "同义词": ("min", 8), "AND": ("min", 8),
               "OR": ("min", 8), "短语": ("min", 4), "排除": ("min", 4),
               "短查询": ("min", 3), "长查询": ("min", 3)}
MIN_QUERIES = 60
ANNOTATION_POLICY = {
    "policy_ref": "docs/standards/2026-11-01_检索评测标注规范_v1.md",
    "grades": {"2": "直接相关（直接回答查询主题，命中核心知识点）",
               "1": "部分相关（背景/相邻概念/同主题分支）",
               "0": "不相关"},
    "unit_rule": "单元等级=concept/claim/evidence 三字段命中标注关键词的最高等级",
    "expected_units": "由人工标注关键词规则展开（不反向使用检索器结果）",
    "frozen": "冻结后不可修改；新增评测集必须新建版本文件，不得原地改标注",
    "no_post_hoc_tuning": "不得看结果后改标注关键词以提升某方法指标；口径变更须并列报告敏感性",
    "significance": "n≥60 才可称显著；配对 t + bootstrap 95% CI 不含 0 + McNemar",
}


# --------------------------------------------------------------------------- #
# 规范化 / 哈希
# --------------------------------------------------------------------------- #
def canonical_payload(asset: dict) -> dict:
    """参与 hash 的规范内容（排除时间戳等易变字段）。"""
    return {
        "schema_version": asset.get("schema_version"),
        "version": asset.get("version"),
        "set_id": asset.get("set_id"),
        "annotation_policy": asset.get("annotation_policy"),
        "queries": asset.get("queries"),
    }


def compute_hash(asset: dict) -> str:
    """内容 sha256：规范化 JSON（sort_keys + 紧凑分隔符）后哈希。"""
    raw = json.dumps(canonical_payload(asset), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 从 ext2 冻结导出（只读）
# --------------------------------------------------------------------------- #
def collect_queries() -> list[dict]:
    """只读读取 eval_search_v2_ext2 的 89 条冻结查询（不改源文件）。"""
    import eval_search_v2_ext2 as e2  # noqa: E402 - 延迟导入，避免无谓依赖
    out: list[dict] = []
    for q in e2.all_queries():
        out.append({
            "id": q["id"],
            "q": q["q"],
            "type": q["type"],
            "source": q.get("source", ""),
            "graded": {"2": list((q.get("graded") or {}).get("2") or []),
                       "1": list((q.get("graded") or {}).get("1") or [])},
        })
    return out


def expand_expected_units(queries: list[dict], data_path: Path | str | None = None) -> list[dict]:
    """展开 expected_units [{unit: 索引, grade: 2/1}]（读取 498 单元文本，只读）。"""
    import eval_search_v2_ext2 as e2
    import search_v2 as sv
    units = sv.load_units(data_path or sv.DEFAULT_DATA)
    out: list[dict] = []
    for q in queries:
        graded = q["graded"]
        exp = []
        for i, u in enumerate(units):
            g = e2.unit_grade(u, graded)
            if g > 0:
                exp.append({"unit": i, "grade": g})
        out.append({**q, "expected_units": exp})
    return out


def build_asset(version: int = DEFAULT_VERSION,
                set_id: str = DEFAULT_SET_ID) -> dict:
    """构建评测集资产（内容确定，时间戳不参与 hash）。"""
    queries = expand_expected_units(collect_queries())
    asset = {
        "schema_version": SCHEMA_VERSION,
        "set_id": set_id,
        "version": version,
        "name": f"研报检索评测集 v{version}（89 条，分级标注 2/1/0，冻结）",
        "frozen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "eval_search_v2_ext2.all_queries()（wave10 冻结 89 查询，未重新标注）",
        "n_queries": len(queries),
        "type_distribution": dict(sorted(Counter(q["type"] for q in queries).items(),
                                         key=lambda kv: -kv[1])),
        "annotation_policy": ANNOTATION_POLICY,
        "queries": queries,
        "content_hash": "",
    }
    asset["content_hash"] = compute_hash(asset)
    return asset


# --------------------------------------------------------------------------- #
# 规范校验（对应《检索评测标注规范 v1》）
# --------------------------------------------------------------------------- #
def validate_spec(asset: dict) -> list[str]:
    """校验规范字段：n≥60、每查询≥1 grade-2、题型覆盖与配额、id/查询唯一等。"""
    errors: list[str] = []
    qs = asset.get("queries") or []
    if not isinstance(qs, list):
        return ["queries 不是列表"]
    n = len(qs)
    if n < MIN_QUERIES:
        errors.append(f"查询数 {n} < 规范下限 {MIN_QUERIES}")
    ids, texts = [], []
    for q in qs:
        qid = q.get("id")
        text = q.get("q")
        ids.append(qid)
        texts.append(text)
        if not qid or not str(qid).strip():
            errors.append(f"存在空 id 的查询: {q!r}")
        if not text or not str(text).strip():
            errors.append(f"{qid}: 查询文本为空")
        graded = q.get("graded") or {}
        if not (graded.get("2") or []):
            errors.append(f"{qid}: 缺少 grade-2 标注（规范要求每查询 ≥1）")
        for g in ("2", "1"):
            keys = graded.get(g) or []
            if len(keys) != len(set(keys)):
                errors.append(f"{qid}: grade-{g} 标注存在重复关键词")
            for k in keys:
                if not isinstance(k, str) or not k.strip():
                    errors.append(f"{qid}: grade-{g} 含空关键词")
        if "expected_units" in q and not q.get("expected_units"):
            errors.append(f"{qid}: expected_units 为空（标注未展开）")
    if len(ids) != len(set(ids)):
        errors.append("查询 id 存在重复")
    if len(texts) != len(set(texts)):
        errors.append("查询文本存在重复")
    # 题型覆盖 + 配额
    dist = Counter(q.get("type", "") for q in qs)
    for t in REQUIRED_TYPES:
        if dist.get(t, 0) == 0:
            errors.append(f"缺少题型: {t}")
    for t, (kind, val) in TYPE_QUOTAS.items():
        cnt = dist.get(t, 0)
        if kind == "ratio" and n > 0 and cnt / n < val:
            errors.append(f"题型 {t} 占比 {cnt / n:.2f} 低于规范下限 {val:.2f}")
        elif kind == "min" and cnt < val:
            errors.append(f"题型 {t} 数量 {cnt} 低于规范下限 {val}")
    return errors


def validate_asset(asset: dict, verify_hash: bool = True) -> dict:
    """完整校验：schema 字段 + 内容 hash + 规范字段。"""
    errors: list[str] = []
    for key in ("schema_version", "set_id", "version", "frozen_at", "queries",
                "n_queries", "content_hash"):
        if key not in asset:
            errors.append(f"缺少字段: {key}")
    if asset.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION}: {asset.get('schema_version')!r}")
    if int(asset.get("n_queries") or 0) != len(asset.get("queries") or []):
        errors.append("n_queries 字段与 queries 数量不一致")
    spec_errors = validate_spec(asset)
    errors.extend(spec_errors)
    hash_ok = None
    if verify_hash:
        got = compute_hash(asset)
        hash_ok = (got == asset.get("content_hash"))
        if not hash_ok:
            errors.append(f"content_hash 不匹配：存储 {asset.get('content_hash')} vs 重算 {got}")
    return {"ok": not errors, "errors": errors, "n_queries": len(asset.get("queries") or []),
            "hash": asset.get("content_hash"), "hash_match": hash_ok}


# --------------------------------------------------------------------------- #
# freeze / verify / list / load
# --------------------------------------------------------------------------- #
def _read_manifest(path: Path = MANIFEST) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"schema_version": "eval_manifest.v1", "updated_at": None, "sets": []}


def freeze(out_dir: Path | str = OUT_DIR, version: int = DEFAULT_VERSION,
           set_id: str = DEFAULT_SET_ID) -> dict:
    """导出评测集资产 + 更新 manifest；返回 {asset_path, manifest_path, content_hash, ...}。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    asset = build_asset(version=version, set_id=set_id)
    asset_path = out / f"{set_id}.json"
    asset_path.write_text(json.dumps(asset, ensure_ascii=False, indent=1), encoding="utf-8")
    # 写文件前先校验（避免资产不合规入库）
    check = validate_asset(asset, verify_hash=True)
    manifest = _read_manifest(out / "manifest.json")
    entry = {
        "version": version,
        "set_id": set_id,
        "file": asset_path.name,
        "content_hash": asset["content_hash"],
        "created_at": asset["frozen_at"],
        "n_queries": asset["n_queries"],
        "source": asset["source"],
        "type_distribution": asset["type_distribution"],
        "annotation_policy_ref": ANNOTATION_POLICY["policy_ref"],
        "validation_ok": check["ok"],
    }
    sets = [s for s in manifest.get("sets") or [] if s.get("version") != version]
    sets.append(entry)
    sets.sort(key=lambda s: int(s.get("version") or 0))
    manifest.update({"schema_version": "eval_manifest.v1",
                     "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                     "sets": sets})
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return {"asset_path": str(asset_path), "manifest_path": str(manifest_path),
            "content_hash": asset["content_hash"], "n_queries": asset["n_queries"],
            "validation": check}


def verify(asset_path: Path | str) -> dict:
    """校验单个评测集资产（hash/结构/规范字段）。"""
    p = Path(asset_path)
    if not p.exists():
        return {"ok": False, "errors": [f"文件不存在: {p}"], "file": str(p)}
    try:
        asset = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": [f"JSON 解析失败: {exc!r}"], "file": str(p)}
    rep = validate_asset(asset, verify_hash=True)
    rep["file"] = str(p)
    rep["set_id"] = asset.get("set_id")
    rep["version"] = asset.get("version")
    return rep


def verify_all(out_dir: Path | str = OUT_DIR) -> dict:
    out = Path(out_dir)
    files = sorted(p for p in out.glob("eval_set_*.json"))
    if not files:
        return {"ok": False, "errors": [f"{out} 下无 eval_set_*.json"], "sets": []}
    sets = [verify(p) for p in files]
    return {"ok": all(s["ok"] for s in sets), "n_sets": len(sets), "sets": sets}


def list_sets(out_dir: Path | str = OUT_DIR) -> dict:
    manifest = _read_manifest(Path(out_dir) / "manifest.json")
    return {"manifest": str(Path(out_dir) / "manifest.json"),
            "n_sets": len(manifest.get("sets") or []),
            "sets": manifest.get("sets") or []}


def load(version: int | None = DEFAULT_VERSION, out_dir: Path | str = OUT_DIR,
         verify_hash: bool = True) -> dict:
    """按版本加载资产（可选同时校验 hash）。"""
    out = Path(out_dir)
    manifest = _read_manifest(out / "manifest.json")
    entry = next((s for s in manifest.get("sets") or []
                  if int(s.get("version") or -1) == int(version)), None)
    if entry is None:
        raise FileNotFoundError(f"manifest 中无 version={version} 的评测集")
    asset = json.loads((out / entry["file"]).read_text(encoding="utf-8"))
    if verify_hash:
        check = validate_asset(asset, verify_hash=True)
        if not check["ok"]:
            raise ValueError(f"评测集校验失败: {check['errors']}")
    return asset


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_freeze(args) -> int:
    rep = freeze(out_dir=args.out_dir, version=args.version)
    print(json.dumps({k: rep[k] for k in ("asset_path", "manifest_path", "content_hash",
                                          "n_queries")} | {"validation_ok": rep["validation"]["ok"]},
                     ensure_ascii=False))
    if not rep["validation"]["ok"]:
        print(json.dumps(rep["validation"]["errors"], ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


def cmd_verify(args) -> int:
    if args.file:
        rep = verify(args.file)
        print(json.dumps(rep, ensure_ascii=False))
        return 0 if rep["ok"] else 1
    rep = verify_all(args.out_dir)
    print(json.dumps({"ok": rep["ok"], "n_sets": len(rep["sets"]),
                      "sets": [{k: s.get(k) for k in ("file", "set_id", "version", "ok")}
                               for s in rep["sets"]]}, ensure_ascii=False))
    return 0 if rep["ok"] else 1


def cmd_list(args) -> int:
    print(json.dumps(list_sets(args.out_dir), ensure_ascii=False, indent=1))
    return 0


def cmd_load(args) -> int:
    asset = load(args.version, args.out_dir, verify_hash=not args.no_verify)
    if args.summary:
        print(json.dumps({k: asset.get(k) for k in
                          ("set_id", "version", "n_queries", "content_hash",
                           "type_distribution", "frozen_at")}, ensure_ascii=False))
    else:
        print(json.dumps(asset, ensure_ascii=False))
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="eval_registry",
                                description="检索评测集资产化管理（freeze/verify/list/load）")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("freeze", help="从 ext2 只读导出评测集资产 + 写 manifest")
    pf.add_argument("--out-dir", default=str(OUT_DIR))
    pf.add_argument("--version", type=int, default=DEFAULT_VERSION)
    pf.set_defaults(func=cmd_freeze)

    pv = sub.add_parser("verify", help="校验 hash/结构/规范字段")
    pv.add_argument("--file", default=None, help="单个资产文件；缺省校验目录下全部")
    pv.add_argument("--out-dir", default=str(OUT_DIR))
    pv.set_defaults(func=cmd_verify)

    pl = sub.add_parser("list", help="列出 manifest 中的版本")
    pl.add_argument("--out-dir", default=str(OUT_DIR))
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("load", help="加载指定版本资产")
    pd.add_argument("--version", type=int, default=DEFAULT_VERSION)
    pd.add_argument("--out-dir", default=str(OUT_DIR))
    pd.add_argument("--no-verify", action="store_true")
    pd.add_argument("--summary", action="store_true")
    pd.set_defaults(func=cmd_load)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
