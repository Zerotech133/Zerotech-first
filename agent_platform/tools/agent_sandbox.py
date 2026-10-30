# -*- coding: utf-8 -*-
"""Agent 沙箱 + 内部 benchmark（P0⑧ · WO-AGENT-SANDBOX-20261027）。

安全基线（默认全部满足）：
  · Docker 隔离执行，**不碰生产容器**、不重启服务；
  · 仓库只读挂载 `/workspace:ro`；只有临时 scratch 目录 `/work:rw`；
  · 网络默认 `--network none`（出口白名单的当前实现=默认拒绝；需联网必须显式 --allow-bridge，属另一批）；
  · `--cap-drop ALL`、`--security-opt no-new-privileges`、容器 rootfs `--read-only` + `/tmp` tmpfs；
  · 外部命令有超时，超时 `docker rm -f` 兜底。

用法：
  python -X utf8 agent_platform/tools/agent_sandbox.py run --cmd "python3 -c 'print(1)'" --dry-run
  python -X utf8 agent_platform/tools/agent_sandbox.py run --cmd "python3 -c 'print(1)'"
  python -X utf8 agent_platform/tools/agent_sandbox.py benchmark --dry-run
  python -X utf8 agent_platform/tools/agent_sandbox.py benchmark --out work/sandbox/bench.json
退出码：0 成功/全过；1 benchmark 有失败；2 输入错误。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
BENCH = REPO / "agent_platform" / "evals" / "sandbox_benchmark.json"
DEFAULT_IMAGE = "docker.1ms.run/openmetadata/ingestion:2.0.5"  # 本地已有、含 python3，无需拉取

SECURITY_FLAGS = [
    "--network", "none",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--read-only",
    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",  # nosec B108 - 测试夹具/docker参数/字符串常量（已核实）
]


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def build_docker_cmd(image: str, repo: Path, scratch: Path, cmd: str,
                     network: str = "none", memory: str = "512m",
                     cpus: str = "1.0", pids_limit: int = 256) -> list[str]:
    flags = list(SECURITY_FLAGS)
    if network == "bridge":
        i = flags.index("--network")
        flags[i + 1] = "bridge"
    flags += ["--memory", str(memory), "--cpus", str(cpus),
              "--pids-limit", str(int(pids_limit))]
    return [
        "docker", "run", "--rm",
        *flags,
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-v", "%s:/workspace:ro" % str(repo),
        "-v", "%s:/work:rw" % str(scratch),
        "-w", "/work",
        "--entrypoint", "/bin/sh",
        image, "-lc", cmd,
    ]


def run_cmd(cmd: str, image: str = DEFAULT_IMAGE, network: str = "none",
            timeout: int = 120, dry_run: bool = False, repo: Path | None = None,
            memory: str = "512m", cpus: str = "1.0", pids_limit: int = 256) -> dict:
    repo = Path(repo or REPO).resolve()
    if not repo.is_dir():
        raise SystemExit("repo_invalid: %s" % repo)
    quotas = {"memory": memory, "cpus": cpus, "pids_limit": int(pids_limit)}
    if dry_run:
        scratch = Path("<tmp>/sandbox")
        return {"ok": True, "dry_run": True, "image": image, "network": network,
                "quotas": quotas, "cmd": cmd,
                "docker_cmd": build_docker_cmd(image, repo, scratch, cmd, network,
                                               memory, cpus, pids_limit)}
    scratch = Path(tempfile.mkdtemp(prefix="sandbox_"))
    name = "sbx_" + str(abs(hash(cmd + str(time.time()))) % 10**8)
    try:
        docker_cmd = build_docker_cmd(image, repo, scratch, cmd, network,
                                      memory, cpus, pids_limit)
        # 注入 --name（放在 run 之后）
        docker_cmd = docker_cmd[:2] + ["--name", name] + docker_cmd[2:]
        t0 = time.time()
        try:
            r = subprocess.run(docker_cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            rc, out, err = r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            rc, out, err = 124, "", "timeout"
        return {"ok": rc == 0, "rc": rc, "elapsed_s": round(time.time() - t0, 2),
                "image": image, "network": network, "quotas": quotas, "cmd": cmd,
                "stdout": out[-4000:], "stderr": err[-2000:]}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def load_benchmark(path: Path = BENCH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("benchmark_load_failed: %s" % exc)
    tasks = data.get("tasks") or []
    if not tasks:
        raise SystemExit("benchmark_empty")
    return data


def benchmark(tasks_path: Path = BENCH, image: str = DEFAULT_IMAGE, out: str = "",
              timeout: int = 120, dry_run: bool = False,
              memory: str = "512m", cpus: str = "1.0", pids_limit: int = 256) -> dict:
    data = load_benchmark(tasks_path)
    tasks = data["tasks"]
    quotas = {"memory": memory, "cpus": cpus, "pids_limit": int(pids_limit)}
    if dry_run:
        return {"ok": True, "dry_run": True, "tasks": len(tasks),
                "image": image, "quotas": quotas,
                "plan": [{"id": t.get("id"), "category": t.get("category"),
                          "cmd": t.get("cmd")} for t in tasks]}
    results = []
    for t in tasks:
        r = run_cmd(t["cmd"], image=image, network="none",
                    timeout=int(t.get("timeout", timeout)),
                    memory=memory, cpus=cpus, pids_limit=pids_limit)
        expect_out = t.get("expect_stdout", "")
        blob = (r.get("stdout") or "") + (r.get("stderr") or "")
        passed = r.get("rc") == int(t.get("expect_rc", 0)) and expect_out in blob
        results.append({"id": t.get("id"), "category": t.get("category"),
                        "passed": bool(passed), "rc": r.get("rc"),
                        "elapsed_s": r.get("elapsed_s"),
                        "expect_stdout": expect_out,
                        "stdout_tail": (r.get("stdout") or "")[-300:]})
    cats: dict[str, dict] = {}
    for r in results:
        c = cats.setdefault(r["category"], {"n": 0, "passed": 0})
        c["n"] += 1
        c["passed"] += 1 if r["passed"] else 0
    total = len(results)
    passed_n = sum(1 for r in results if r["passed"])
    report = {"schema": "agent_sandbox_benchmark/v1",
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "image": image, "network": "none",
              "total": total, "passed": passed_n,
              "rate": round(passed_n / total, 3) if total else 0.0,
              "categories": {k: {**v, "rate": round(v["passed"] / v["n"], 3)}
                             for k, v in cats.items()},
              "results": results}
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report["written"] = str(p)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent Docker 沙箱 + 内部 benchmark")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="action", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--cmd", required=True)
    p_run.add_argument("--image", default=DEFAULT_IMAGE)
    p_run.add_argument("--network", default="none", choices=["none", "bridge"])
    p_run.add_argument("--timeout", type=int, default=120)
    p_run.add_argument("--dry-run", action="store_true")

    p_bench = sub.add_parser("benchmark")
    p_bench.add_argument("--tasks", default=str(BENCH))
    p_bench.add_argument("--image", default=DEFAULT_IMAGE)
    p_bench.add_argument("--out", default="")
    p_bench.add_argument("--timeout", type=int, default=120)
    p_bench.add_argument("--dry-run", action="store_true")

    for p in (p_run, p_bench):
        p.add_argument("--memory", default="512m", help="容器内存上限（默认 512m）")
        p.add_argument("--cpus", default="1.0", help="CPU 配额（默认 1.0 核）")
        p.add_argument("--pids-limit", type=int, default=256, help="进程数上限（默认 256）")

    args = ap.parse_args(argv)
    if not _docker_available() and not args.dry_run:
        print(json.dumps({"ok": False, "error": "docker_not_found"}, ensure_ascii=False))
        return 2
    if args.action == "run":
        if args.network == "bridge":
            print("[warn] --network bridge 会放开容器网络；出口白名单尚未实现，仅限临时排障")
        out = run_cmd(args.cmd, image=args.image, network=args.network,
                      timeout=args.timeout, dry_run=args.dry_run,
                      memory=args.memory, cpus=args.cpus, pids_limit=args.pids_limit)
    else:
        out = benchmark(Path(args.tasks), image=args.image, out=args.out,
                        timeout=args.timeout, dry_run=args.dry_run,
                        memory=args.memory, cpus=args.cpus, pids_limit=args.pids_limit)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.action == "benchmark" and not args.dry_run:
        return 0 if out.get("passed") == out.get("total") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
