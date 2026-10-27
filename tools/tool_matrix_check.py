# -*- coding: utf-8 -*-
"""tool_matrix_check.py —— 岗×操作级二维工具白名单校验器（K-ORG-080/N9 · 087/T4）

设计出处与判例：
- 087 判例：Trae 一维黑名单 → 本组织「岗×操作级」二维矩阵（agent_platform/rules/tool_matrix.json），
  默认 deny、白名单放行；QA1 只读白名单判例迁移为矩阵首条。
- T4 拍板：agent_platform/data/llm_tool_audit.jsonl observe 阶段只报不拦，10-18 校准后 enforce——
  本矩阵即拍板时的白名单依据，时序咬合。
- fail-visible 判例（087）：未登记岗/未命中规则一律 DENIED，绝不 fail-open 放行；
  唯一例外是矩阵文件本身缺失时按任务书 fail-open 打印提示退出 0（避免误伤无矩阵环境）。
- 083 判例（本体落库≠投影已同步）：--plan 只生成登记命令清单并**只打印不执行**，
  写注册表必须由主控走 8093 API（POST /api/agent/update）逐岗同步投影，防批量事故。

用法：
  python tool_matrix_check.py --check --agent QA1 --op write --target tools/foo.py
  python tool_matrix_check.py --audit
  python tool_matrix_check.py --plan
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "agent_platform" / "rules" / "tool_matrix.json"
AUDIT_PATH = ROOT / "agent_platform" / "data" / "llm_tool_audit.jsonl"
BUNDLE_PATH = ROOT / "portal" / "agent_ide_bundle.json"
API_BASE = "http://127.0.0.1:8093"

# endpoint→操作映射（T4 任务书内置简单表；顺序敏感：具体 read 路径先于 /api/task/* 通配）
ENDPOINT_OP_RULES = [
    ("/api/task/list", "read"),
    ("/api/task/status", "read"),
    ("/api/org", "read"),
    ("/api/home", "read"),
    ("/api/task/*", "execute"),
    ("/api/agent/*", "write"),
]


def load_matrix() -> dict:
    """加载矩阵；文件缺失 → fail-open：打印提示退出 0（任务书唯一例外）。"""
    if not MATRIX_PATH.exists():
        print(f"[fail-open] 矩阵文件缺失：{MATRIX_PATH}，跳过校验（exit 0）")
        sys.exit(0)
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def norm(path: str) -> str:
    """Windows 反斜杠归一为 /，便于 fnmatch 匹配仓库相对路径。"""
    return path.replace("\\", "/")


def map_endpoint(endpoint: str) -> str | None:
    for pat, op in ENDPOINT_OP_RULES:
        if fnmatch.fnmatch(endpoint, pat):
            return op
    return None  # 未映射 endpoint：无法判定，audit 中单独列出


def check(matrix: dict, agent: str, op: str, target: str) -> tuple[bool, str]:
    """核心判定：返回 (是否放行, 命中/拒绝原因)。默认 deny、白名单放行（087）。
    K-ORG-108：岗未命中 matrix 段时回退查 platform_callers（G5 连接器等
    平台级 caller 的合法登记位）——否则 enforce 后平台调用全部假越权。"""
    if op == "danger":
        # danger 级全岗 deny；回收站删除例外沿铁律走主控特批通道，不在矩阵放行
        return False, "danger 全岗 deny（087：危险操作=全局禁）"
    rules = matrix.get("matrix", {}).get(agent)
    if rules is None:
        rules = matrix.get("platform_callers", {}).get(agent)
    if rules is None:
        # fail-visible：未登记 caller 默认 deny（087 判例，绝不 fail-open 放行）
        return False, f"caller {agent} 未登记（matrix+platform_callers 均无），默认 deny（fail-visible）"
    tg = norm(target)
    allowed = rules.get(op, [])
    if op == "read" and "*" in allowed:
        return True, "read 全放行（\"*\"）"
    if op == "execute":
        if tg in allowed:
            return True, f"execute 白名单精确命中：{tg}"
        return False, f"execute 未命中白名单（允许：{allowed or '空'}）"
    for pat in allowed:
        if fnmatch.fnmatch(tg, pat):
            return True, f"{op} 白名单 fnmatch 命中：{pat}"
    return False, f"{op} 未命中白名单（允许：{allowed or '空'}）"


def run_check(args) -> int:
    matrix = load_matrix()
    ok, why = check(matrix, args.agent, args.op, args.target)
    print(f"{'ALLOWED' if ok else 'DENIED'} | agent={args.agent} op={args.op} target={args.target}")
    print(f"规则：{why}")
    return 0 if ok else 1  # 退出码 0/1 供脚本判定


def judge(matrix: dict, caller: str | None, ep: str) -> tuple[bool, str]:
    """K-ORG-111：endpoint 级三层判定（单一事实源）——run_audit 与 8093
    enforce 接线共用。observe 账本/服务端入口只有 endpoint 无仓库路径：
      ① caller 归属（matrix + platform_callers 双段，未登记默认 deny·fail-visible）
      ② execute：endpoint→工具归一（/api/task/* → devtask）后精确匹配白名单
      ③ read/write：白名单非空粗判放行 / 空（如 QA1 write=[]）判越权
    target 级 fnmatch 判定留给 --check（调用方自报真实路径）。"""
    op = map_endpoint(ep)
    if op is None:
        return False, "endpoint 未映射操作级，默认拒绝"
    if caller is None or caller == "":
        return False, "caller 未标注（无法归岗，默认 deny）"
    rules = (matrix.get("matrix", {}).get(caller)
             or matrix.get("platform_callers", {}).get(caller))
    if rules is None:
        return False, "caller 未登记（默认 deny·fail-visible）"
    allowed = rules.get(op, [])
    if op == "execute":
        tool = "devtask" if ep.startswith("/api/task/") else ep
        if tool in allowed:
            return True, f"execute 白名单命中：{tool}"
        return False, f"execute 未命中白名单（允许：{allowed or '空'}·归一工具={tool}）"
    if allowed:
        return True, f"{op} 白名单非空（endpoint 级粗判放行）"
    return False, f"{op} 白名单为空（如 QA1 只读判例）"


def run_audit() -> int:
    """observe 阶段只报不拦（T4 拍板）：越权调用列清单，退出码恒 0。
    判定口径见 judge()（K-ORG-108 定型、111 抽出共用）；
    另输出 caller 归因率等统计（10-18 校准决策的核心口径）。"""
    matrix = load_matrix()
    if not AUDIT_PATH.exists():
        print(f"[fail-open] 审计台账缺失：{AUDIT_PATH}，跳过（exit 0）")
        return 0
    violations, unknown_caller, unmapped = [], [], []
    ok_rows, caller_stat, ep_stat = [], {}, {}
    total = 0
    for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        total += 1
        caller, ep = rec.get("caller"), rec.get("endpoint", "")
        caller_stat[caller] = caller_stat.get(caller, 0) + 1
        ep_stat[ep] = ep_stat.get(ep, 0) + 1
        if map_endpoint(ep) is None:
            unmapped.append(rec)
            continue
        if caller is None:
            unknown_caller.append(rec)  # 未标注 caller 无法归岗，observe 阶段仅提示
            continue
        ok, why = judge(matrix, caller, ep)
        if ok:
            ok_rows.append(rec)
        else:
            violations.append((rec, map_endpoint(ep), why))
    print("== K-ORG-080/N9 audit（observe 只报不拦 · T4 拍板 10-18 校准）==")
    print(f"总量 {total} | 放行 {len(ok_rows)} | 越权 {len(violations)} | caller 缺失 {len(unknown_caller)} | endpoint 未映射 {len(unmapped)}")
    attributed = total - len(unknown_caller)
    print(f"caller 归因率：{attributed}/{total} = {attributed * 100 // max(total, 1)}%"
          "（归因靠调用方 body/query 带 caller——晨检/自动化探活建议统一带 caller）")
    print("caller 分布：", json.dumps(caller_stat, ensure_ascii=False))
    print("endpoint 分布：", json.dumps(ep_stat, ensure_ascii=False))
    for rec, op, why in violations:
        print(f"  [越权][{rec['ts']}] caller={rec.get('caller')} {rec['method']} {rec['endpoint']} → op={op} | {why}")
    for rec in unmapped:
        print(f"  [未映射][{rec['ts']}] {rec['method']} {rec['endpoint']}")
    return 0


def run_plan() -> int:
    """生成登记命令清单——只打印不执行（083 判例：写注册表由主控走 8093 API 逐岗执行）。"""
    matrix = load_matrix()
    bundle = {}
    if BUNDLE_PATH.exists():
        bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8")).get("agents", {})
    print(f"== K-ORG-080/N9 --plan：登记命令清单（只打印，由主控逐岗 POST {API_BASE}/api/agent/update）==")
    for agent, rules in matrix.get("matrix", {}).items():
        info = bundle.get(agent, {})
        # K-ORG-108 修正：body 必须顶层 tools_allowed（agent_update 只认顶层固定字段，
        # 嵌套 {attributes:{...}} 报「没有要改的字段」静默无效——105 判例，--plan 生成器同步修正）
        body = {"agent_id": agent, "tools_allowed": rules,
                "caller": "laotian@matrix-plan"}
        curl = (
            f"curl -s -X POST {API_BASE}/api/agent/update "
            f"-H \"Content-Type: application/json\" -d '{json.dumps(body, ensure_ascii=False)}'"
        )
        row = {
            "agent_id": agent,
            "dept": info.get("dept", "?"),
            "name": info.get("name", "?"),
            "tools_allowed": rules,
            "curl_body": body,
            "curl": curl,
        }
        print(json.dumps(row, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="岗×操作级二维工具白名单校验器（K-ORG-080/N9）")
    ap.add_argument("--check", action="store_true", help="单点校验模式")
    ap.add_argument("--agent", help="岗 ID，如 QA1")
    ap.add_argument("--op", choices=["read", "write", "execute", "danger"], help="操作级")
    ap.add_argument("--target", help="目标路径或命令")
    ap.add_argument("--audit", action="store_true", help="审计台账全量对照（observe 只报不拦）")
    ap.add_argument("--plan", action="store_true", help="生成登记命令清单（只打印不执行）")
    args = ap.parse_args()
    if args.check:
        if not (args.agent and args.op and args.target):
            ap.error("--check 需要 --agent/--op/--target 三参数")
        return run_check(args)
    if args.audit:
        return run_audit()
    if args.plan:
        return run_plan()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
