# -*- coding: utf-8 -*-
"""L2 危险操作评审门（K-STOCK-416）——宿主机级危险操作合并拦截器。

背景（判例 K-STOCK-413）：2026-10-16 AI agent 以「根治 Task Scheduler 引擎」
为由擅自 shutdown 重启宿主机，且无留痕。K-STOCK-416 规范定：L2 动作
（重启/关机/注销、改系统代理/防火墙/计划任务、动生产服务器）一律
blocking 人工确认；本守卫把该规则做成**代码强制层**——分支 diff 新增行
命中 L2 关键词即拒绝合并，agent 无法凭自觉绕过。

扫描纪律（防误伤，宁缺毋滥）：
- 只扫可执行代码后缀（.py/.ps1/.bat/.cmd/.sh/.vbs/.js/.ts）；
  .md/.txt/.json/.yaml 等文档配置不扫——治理文档/判例本身必须引用这些词；
- 跳过 _l2_guard.py 自身（关键词定义）与测试文件（路径含 test/tests，
  或文件名 test_*）；
- 跳过纯注释行（# / // 开头）——注释说明用法不构成危险操作；
  **行尾注释不跳**（``os.system("shutdown /r")  # 重启`` 的代码部分是
  真危险；安全侧 fail-safe：宁可误伤不可漏检，误伤走 --l2-ack 人工通道）；
- 命中即 exit 2（区别于三守卫的 exit 1，便于 devtask 分流告警）。

豁免通道：devtask.py merge <TID> --l2-ack --l2-reason "<五要素>"
→ 写 approvals.db 审批单并把 approval_id 回写工单 note（双向可追溯）。
"""
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 只扫可执行代码后缀
_CODE_EXTS = (".py", ".ps1", ".bat", ".cmd", ".sh", ".vbs", ".js", ".ts")

# L2 关键词表：(正则, 说明)。精准优先：宽泛词宁可不收，不可误伤。
L2_PATTERNS = [
    (r"""shutdown[\s"',]+(?:/[rfh]\w*|-\w+)""", "重启/关机命令（shutdown /r 等）"),
    (r"\bshutdown\.exe\b", "直接调用 shutdown.exe"),
    (r"\bRestart-Computer\b", "PowerShell 重启本机"),
    (r"\bStop-Computer\b", "PowerShell 关机"),
    (r"(?m)^\s*logoff\b", "注销当前会话"),
    (r"\bInitiateSystemShutdown", "系统关机 WinAPI"),
    (r"10\.205\.0\.118", "生产服务器地址（zeroq 宿主机）"),
    (r"netsh\s+advfirewall", "修改 Windows 防火墙"),
    (r"New-NetFirewallRule|Set-NetFirewallProfile", "修改防火墙规则"),
    (r"Set-ItemProperty[^\n]*ProxyEnable|ProxyEnable[^\n]*Internet Settings",
     "修改系统代理开关"),
    (r"Set-ItemProperty[^\n]*Internet Settings", "修改系统代理配置"),
    (r"schtasks\s+/create|Register-ScheduledTask", "创建计划任务（持久化面）"),
    (r"Remove-Item[^\n]*(?:HKLM|HKCU):", "删除注册表项"),
]



# FP exemption (2026-10-27): pure string / docstring lines carry no execution surface.
_STRING_PREFIXES = ('"', "'", '"' * 3, "'" * 3, "\u300c")


def _doc_delta(content: str) -> int:
    return (content.count('"' * 3) + content.count("'" * 3)) % 2


def line_hits(content: str, in_doc: bool = False):
    if in_doc:
        return []
    stripped = content.lstrip()
    if stripped.startswith(("#", "//")):
        return []
    if stripped.startswith(_STRING_PREFIXES):
        return []
    out = []
    for pat, desc in L2_PATTERNS:
        if re.search(pat, content):
            out.append(desc)
            break
    return out

def _git(*args):
    r = subprocess.run(["git"] + list(args), cwd=REPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("git %s 失败: %s" % (" ".join(args),
                                             (r.stderr or r.stdout).strip()))
    return r.stdout


def _scannable(path):
    """白名单后缀 + 自身/测试豁免。

    测试豁免只认两种常规约定：文件名以 test_ 开头、或位于 tests/ 目录。
    勿放宽到「文件名含 test_」——那会变成绕过漏洞
    （如 _l2_selftest_danger.py / x_test_shutdown.py 均可逃检）。
    """
    base = os.path.basename(path)
    if base == "_l2_guard.py":
        return False
    low = base.lower()
    norm = path.replace(os.sep, "/")
    if low.startswith("test_") or "/tests/" in norm:
        return False
    return path.lower().endswith(_CODE_EXTS)


def scan_branch(branch, base="master"):
    """返回 [(file, lineno, line, desc)]；空列表 = 干净。"""
    try:
        files = _git("diff", "%s...%s" % (base, branch),
                     "--name-only").splitlines()
    except SystemExit:
        return []
    targets = [f for f in files if f.strip() and _scannable(f.strip())]
    if not targets:
        return []
    diff = _git("diff", "%s...%s" % (base, branch), "--", *targets)
    hits = []
    cur_file, lineno = None, 0
    in_doc = False
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur_file = line[len("+++ b/"):]
            in_doc = False
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            lineno += 1
            content = line[1:]
            for desc in line_hits(content, in_doc):
                hits.append((cur_file or "?", lineno, content.strip(), desc))
            in_doc ^= _doc_delta(content)
        elif line.startswith("-") or line.startswith(" "):
            if not line.startswith("---"):
                # 上下文/删除行也推进行号（diff -U3 上下文行）
                if line.startswith(" "):
                    lineno += 1
                    in_doc ^= _doc_delta(line[1:])
    return hits


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    branch = argv[0]
    hits = scan_branch(branch)
    if not hits:
        print("L2 门 ✓：%s 未命中宿主机级危险操作" % branch)
        return 0
    print("L2 门 ✗：%s 命中 %d 处宿主机级危险操作（K-STOCK-416）"
          % (branch, len(hits)))
    for f, ln, text, desc in hits:
        print("  %s:%s  [%s]  %s" % (f, ln, desc, text[:100]))
    print("—— 已拒绝合并。若确属业务必需：人工确认后执行")
    print("   devtask.py merge <TID> --l2-ack --l2-reason "
          "\"<五要素：命令全文+影响面+不可逆点+替代方案+回滚预案>\"")
    return 2


if __name__ == "__main__":
    sys.exit(main())
